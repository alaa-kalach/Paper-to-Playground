"""OpenRouter chat client with budget enforcement, tracing and tolerant JSON parsing."""
import json
import os
import re
import time

import requests

URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?|\n?\s*```\s*$")


def _escape_ctrl_in_strings(s):
    """Escape raw newlines/tabs that models put inside JSON strings (common with JS code)."""
    out, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch == "\n":
                out.append("\\n"); continue
            elif ch == "\r":
                continue
            elif ch == "\t":
                out.append("\\t"); continue
            elif ord(ch) < 0x20:
                continue
        elif ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def _fix_bad_escapes(s):
    """Double backslashes that are not valid JSON escapes (LaTeX like \\sum written as \sum)."""
    return re.sub(r'\\(.)', lambda m: m.group(0) if m.group(1) in '"\\/bfnrtu' else "\\\\" + m.group(1), s,
                  flags=re.S)


def _strip_trailing_commas(s):
    return re.sub(r",(\s*[}\]])", r"\1", s)


def parse_json_loose(text):
    """Return (obj, note). Raises ValueError if nothing parseable."""
    if text is None:
        raise ValueError("empty response")
    t = _FENCE.sub("", text.strip())
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("no JSON object found")
    t = t[a:b + 1]
    attempts = [
        ("plain", lambda s: s),
        ("ctrl_escaped", _escape_ctrl_in_strings),
        ("ctrl+backslash", lambda s: _fix_bad_escapes(_escape_ctrl_in_strings(s))),
        ("ctrl+backslash+commas", lambda s: _strip_trailing_commas(_fix_bad_escapes(_escape_ctrl_in_strings(s)))),
    ]
    last = None
    for note, fn in attempts:
        try:
            obj = json.loads(fn(t), strict=False)
            if isinstance(obj, dict):
                return obj, note
        except Exception as e:  # noqa: BLE001
            last = e
    raise ValueError(f"JSON parse failed: {last}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class OpenRouterClient:
    def __init__(self, model, budget, trace):
        self.model = model
        self.budget = budget
        self.trace = trace
        self.key = os.environ.get("OPENROUTER_API_KEY", "")
        self.use_json_mode = os.environ.get("P2P_JSON_MODE", "1") != "0"
        self.reasoning_mode = os.environ.get("P2P_REASONING", "off")  # off | low | omit
        self.temperature = float(os.environ.get("P2P_TEMPERATURE", "0.2"))
        self.provider_sort = os.environ.get("P2P_PROVIDER_SORT", "throughput")  # throughput|latency|price|none
        self.session = requests.Session()

    def _payload(self, messages, max_tokens):
        p = {"model": self.model, "messages": messages, "temperature": self.temperature,
             "max_tokens": max_tokens, "usage": {"include": True}}
        sort = self.provider_sort
        if sort in ("throughput", "latency", "price"):
            p["provider"] = {"sort": sort}
        if self.use_json_mode:
            p["response_format"] = {"type": "json_object"}
        if self.reasoning_mode == "off":
            p["reasoning"] = {"enabled": False, "exclude": True}
        elif self.reasoning_mode == "low":
            p["reasoning"] = {"effort": "low", "exclude": True}
        return p

    def chat_json(self, stage, messages, max_tokens=4000, min_tokens=800, reserve_calls=0, want_timeout=90):
        """One logical request (may make up to 2 HTTP attempts). Returns (obj, raw_text, meta)."""
        if not self.key:
            raise LLMError("OPENROUTER_API_KEY not set")
        attempt = 0
        while True:
            ok, why = self.budget.can_call(min_tokens=min_tokens, reserve_calls=reserve_calls)
            if not ok:
                self.trace.event(stage, "llm_call_skipped", "budget", reason=why, budget=self.budget.summary())
                raise BudgetExceeded(why)
            attempt += 1
            mt = self.budget.max_tokens_for(max_tokens)
            timeout = self.budget.timeout_for(want_timeout)
            payload = self._payload(messages, mt)
            self.budget.record_call()
            t = time.monotonic()
            status, data, err = None, None, None
            try:
                r = self.session.post(URL, json=payload, timeout=(10, timeout), headers={
                    "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                    "X-Title": "paper-to-playground"})
                status = r.status_code
                try:
                    data = r.json()
                except Exception:
                    data = None
                    err = f"non-JSON body: {r.text[:300]}"
            except requests.Timeout:
                err = f"timeout after {timeout:.0f}s"
            except requests.RequestException as e:
                err = f"network error: {type(e).__name__}: {e}"
            dt = round(time.monotonic() - t, 3)

            usage = (data or {}).get("usage") or {}
            self.budget.record_usage(usage)
            api_err = (data or {}).get("error") if isinstance(data, dict) else None
            if api_err and not err:
                err = f"api error: {json.dumps(api_err)[:400]}"
            if status and status >= 400 and not err:
                err = f"http {status}"
            choice = ((data or {}).get("choices") or [{}])[0] if data else {}
            text = (choice.get("message") or {}).get("content") if choice else None
            finish = choice.get("finish_reason") if choice else None

            call_meta = {
                "call_no": self.budget.calls, "attempt": attempt, "http_status": status,
                "generation_id": (data or {}).get("id"), "model": (data or {}).get("model", self.model),
                "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                "cost": usage.get("cost"), "elapsed_s": dt, "max_tokens": mt, "finish_reason": finish,
                "json_mode": self.use_json_mode, "reasoning_mode": self.reasoning_mode,
                "provider": (data or {}).get("provider"), "provider_sort": self.provider_sort,
                "prompt_chars": sum(len(m.get("content", "")) for m in messages),
                "completion_chars": len(text or ""),
            }

            if err or not text:
                err = err or f"empty content (finish_reason={finish})"
                self.trace.event(stage, "llm_call", "error", error=err, **call_meta, budget=self.budget.summary())
                low = err.lower()
                # adapt parameters the provider rejects, then retry once
                adapted = False
                if status == 400 and "response_format" in low and self.use_json_mode:
                    self.use_json_mode = False; adapted = True
                elif status == 400 and "reasoning" in low and self.reasoning_mode != "omit":
                    self.reasoning_mode = "omit"; adapted = True
                elif status in (400, 404) and "provider" in low and self.provider_sort != "none":
                    self.provider_sort = "none"; adapted = True
                retryable = adapted or status in (408, 429, 500, 502, 503, 504) or status is None
                if attempt < 2 and retryable:
                    if status == 429:
                        time.sleep(min(4, max(0, self.budget.remaining_s() - 30)))
                    continue
                raise LLMError(err)

            self.trace.event(stage, "llm_call", "ok", **call_meta, budget=self.budget.summary())
            try:
                obj, note = parse_json_loose(text)
            except ValueError as e:
                self.trace.event(stage, "parse_json", "fail", error=str(e), finish_reason=finish,
                                 head=text[:200], tail=text[-200:])
                raise LLMError(f"unparseable JSON ({e}); finish_reason={finish}")
            self.trace.event(stage, "parse_json", "ok", method=note, keys=sorted(obj.keys())[:40])
            return obj, text, call_meta


class MockClient:
    """Offline client: returns JSON files listed in P2P_MOCK (comma separated) in order.
    Records fake usage so trace/budget logic is exercised."""

    def __init__(self, files, budget, trace):
        self.files = [f for f in files.split(",") if f]
        self.budget, self.trace = budget, trace
        self.model = "mock"

    def chat_json(self, stage, messages, max_tokens=4000, min_tokens=800, reserve_calls=0, want_timeout=90):
        ok, why = self.budget.can_call(min_tokens=min_tokens, reserve_calls=reserve_calls)
        if not ok:
            self.trace.event(stage, "llm_call_skipped", "budget", reason=why)
            raise BudgetExceeded(why)
        self.budget.record_call()
        if not self.files:
            self.trace.event(stage, "llm_call", "error", error="mock exhausted")
            raise LLMError("mock exhausted")
        path = self.files.pop(0)
        text = open(path, encoding="utf-8").read()
        usage = {"prompt_tokens": sum(len(m["content"]) for m in messages) // 4, "completion_tokens": len(text) // 4}
        self.budget.record_usage(usage)
        self.trace.event(stage, "llm_call", "ok", mock_file=path, **usage, elapsed_s=0.0)
        try:
            obj, note = parse_json_loose(text)
        except ValueError as e:
            self.trace.event(stage, "parse_json", "fail", error=str(e))
            raise LLMError(str(e))
        self.trace.event(stage, "parse_json", "ok", method=note)
        return obj, text, {}
