"""Agent loop: generate spec -> check -> repair (<=2) -> sanitize -> render -> page checks -> write.
Fallback ladder: passing spec > spec with failed parts hidden > emergency minimal spec > static page (exit 1)."""
import json
import os
import threading
import time
from pathlib import Path

from .budget import Budget, HARD_SECONDS
from .checks import normalize, run_checks, check_page
from .fallback import sanitize, usable, static_page
from .llm import OpenRouterClient, MockClient, LLMError
from .trace import Trace

ROOT = Path(__file__).resolve().parent.parent
MAX_REPAIRS = 2
REQUIRED = ("source_url", "focus", "audience")


def _prompt(name, default=""):
    p = ROOT / "prompts" / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else default


def _log_report(trace, stage, report, attempt):
    """One event per check family + summary. Keeps the trace readable and evidences real checks."""
    by = {}
    for f in report["failures"]:
        by.setdefault(f["check"], []).append(f)
    for chk in ("c1_schema", "c2_outcomes", "c3_compute", "c4_edges", "c5_sensitivity",
                "c6_selftests", "c7_explorations", "c8_equation", "c9_latex"):
        fl = by.get(chk, [])
        res = "pass" if not fl else ("fail" if any(f["severity"] == "major" for f in fl) else "warn")
        trace.event(stage, f"check:{chk}", res, attempt=attempt, failures=[f"[{f['severity']}] {f['msg']}" for f in fl][:6])
    stats = {k: v for k, v in report["stats"].items() if not k.startswith("_")}
    trace.event(stage, "checks_summary", "pass" if report["pass"] else "fail", attempt=attempt,
                n_major=report["n_major"], n_minor=report["n_minor"], usable=report["usable"], stats=stats)


def _score(report):
    return (0 if report["usable"] else 1, report["n_major"], report["n_minor"])


class Agent:
    def __init__(self, input_path, out_dir, model):
        self.t0 = time.monotonic()
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.trace = Trace(self.out / "trace.jsonl", self.t0)
        self.budget = Budget(self.t0)
        self.model = model
        self.input_path = input_path
        self.case = {}
        self.page_written = False
        self.best = None  # (spec, report)
        self._lock = threading.Lock()
        mock = os.environ.get("P2P_MOCK")
        self.client = MockClient(mock, self.budget, self.trace) if mock else OpenRouterClient(model, self.budget, self.trace)

    # ------------------------------------------------------------------ io
    def write_page(self, html, kind):
        with self._lock:
            tmp = self.out / "index.html.tmp"
            tmp.write_text(html, encoding="utf-8")
            os.replace(tmp, self.out / "index.html")
            self.page_written = True
        self.trace.event("output", "write_index_html", "ok", kind=kind, bytes=len(html.encode("utf-8")))

    def load_case(self):
        try:
            with open(self.input_path, encoding="utf-8-sig") as f:  # tolerate BOM (Windows Notepad)
                case = json.load(f)
        except Exception as e:
            self.trace.event("input", "read_case", "fail", error=f"{type(e).__name__}: {e}")
            return False
        missing = [k for k in REQUIRED if not isinstance(case.get(k), str) or not case[k].strip()]
        self.case = {k: v for k, v in case.items() if isinstance(v, str)}
        if missing:
            self.trace.event("input", "validate_case", "fail", missing=missing)
            return False
        self.trace.event("input", "read_case", "ok", source_url=case["source_url"],
                         focus_chars=len(case["focus"]), audience=case["audience"][:80])
        return True

    # ------------------------------------------------------------------ llm stages
    def _user_msg(self):
        return json.dumps(self.case, ensure_ascii=False)

    def generate(self, emergency=False):
        system = _prompt("spec_system.txt")
        if emergency:
            system += "\n\n" + _prompt("emergency_system.txt")
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": self._user_msg()}]
        stage = "emergency" if emergency else "generate"
        self.trace.event(stage, "build_prompt", "ok", system_chars=len(system), user_chars=len(msgs[1]["content"]))
        obj, _, _ = self.client.chat_json(stage, msgs, max_tokens=2500 if emergency else 4000,
                                          reserve_calls=0 if emergency or self.budget.max_calls < 3 else 1)
        if "spec" in obj and isinstance(obj["spec"], dict):
            obj = obj["spec"]
        return obj

    def repair(self, spec, report, n):
        majors = [f for f in report["failures"] if f["severity"] == "major"]
        minors = [f for f in report["failures"] if f["severity"] == "minor"]
        fields = []
        for f in majors + minors:
            for k in f["fields"]:
                if k not in fields and k != "render":
                    fields.append(k)
        if "compute" in fields and "outputs" not in fields:
            fields.append("outputs")  # compute and outputs must agree
        current = {k: spec.get(k) for k in fields}
        context = {
            "controls": [{k: c.get(k) for k in ("id", "type", "min", "max", "default", "options", "length", "rows", "cols")
                          if k in c} for c in spec.get("controls") or [] if isinstance(c, dict)] if "controls" not in fields else "(in fields)",
            "outputs": [{"id": o.get("id"), "type": o.get("type")} for o in spec.get("outputs") or [] if isinstance(o, dict)]
            if "outputs" not in fields else "(in fields)",
            "default_outputs": report["stats"].get("default_outputs", {}),
        }
        user = json.dumps({
            "case": self.case,
            "errors": [f"[{f['check']}] {f['msg']}" for f in majors][:8] + [f"[minor] {f['msg']}" for f in minors][:4],
            **({"previous_attempt": "Your previous patch did NOT resolve these errors. Try a different fix: if compute "
                "is scientifically right, change the failing assert/threshold or set values instead; if a control is "
                "dead, make a declared output depend on it."} if n > 1 else {}),
            "fields_to_fix": current,
            "context_unchanged": context,
        }, ensure_ascii=False)
        msgs = [{"role": "system", "content": _prompt("repair_system.txt")}, {"role": "user", "content": user}]
        self.trace.event("repair", "build_prompt", "ok", repair_no=n, fields=fields, n_errors=len(majors),
                         user_chars=len(user))
        obj, _, _ = self.client.chat_json("repair", msgs, max_tokens=3000, min_tokens=600, reserve_calls=0)
        patch = obj.get("patch") if isinstance(obj.get("patch"), dict) else obj
        if all(k in patch for k in ("title", "controls", "compute", "outputs")):
            self.trace.event("repair", "apply_patch", "ok", mode="full_spec", keys=sorted(patch)[:30])
            return patch
        merged = dict(spec)
        applied = []
        for k, v in patch.items():
            if k.startswith("_"):
                continue
            merged[k] = v
            applied.append(k)
        self.trace.event("repair", "apply_patch", "ok" if applied else "empty", mode="patch", applied=applied)
        return merged

    def check(self, raw, stage, attempt):
        spec, fixes = normalize(raw, self.case)
        if fixes:
            self.trace.event(stage, "normalize", "autofix", attempt=attempt, fixes=fixes[:10])
        t = time.monotonic()
        report = run_checks(spec, self.case)
        report["stats"]["check_seconds"] = round(time.monotonic() - t, 3)
        spec, report = self._bool_scalars(spec, report, stage, attempt)
        spec, report = self._prune_dead_controls(spec, report, stage, attempt)
        _log_report(self.trace, stage, report, attempt)
        if self.best is None or _score(report) < _score(self.best[1]):
            self.best = (spec, report)
        return spec, report

    def _bool_scalars(self, spec, report, stage, attempt):
        """Free fix: scalar outputs returned as true/false are converted to 1/0 by wrapping compute."""
        import re as _re
        ids = sorted({m.group(1) for f in report["failures"] for m in
                      [_re.search(r"output '([A-Za-z_$][\w$]*)' \(scalar\): scalar must be a number; got (true|false)", f["msg"])] if m})
        if not ids:
            return spec, report
        s2 = dict(spec)
        s2["compute"] = (spec["compute"] + "\nvar __compute_orig = compute;\ncompute = function(inputs){ var r = __compute_orig(inputs); "
                         + "".join(f"if (r && typeof r[{json.dumps(k)}] === 'boolean') r[{json.dumps(k)}] = r[{json.dumps(k)}] ? 1 : 0; " for k in ids)
                         + "return r; };")
        outs = []
        for o in s2.get("outputs") or []:
            if isinstance(o, dict) and o.get("id") in ids:
                o = dict(o, label=str(o.get("label") or o["id"]) + " (1 = yes, 0 = no)")
            outs.append(o)
        s2["outputs"] = outs
        r2 = run_checks(s2, self.case)
        accepted = r2["n_major"] < report["n_major"]
        self.trace.event(stage, "autofix:bool_scalars", "accepted" if accepted else "rejected", attempt=attempt,
                         outputs=ids, majors_before=report["n_major"], majors_after=r2["n_major"])
        return (s2, r2) if accepted else (spec, report)

    def _prune_dead_controls(self, spec, report, stage, attempt):
        """Free fix (no API call): drop controls that never change an output if >=2 live controls remain."""
        dead = report["stats"].get("dead_controls") or []
        ctrls = [c for c in spec.get("controls") or [] if isinstance(c, dict)]
        live = [c for c in ctrls if c.get("id") not in dead]
        if not dead or len(live) < 2:
            return spec, report
        s2 = dict(spec)
        s2["controls"] = live
        for key in ("explorations", "self_tests"):
            items = []
            for it in s2.get(key) or []:
                if isinstance(it, dict) and isinstance(it.get("set"), dict):
                    it = dict(it, set={k: v for k, v in it["set"].items() if k not in dead})
                items.append(it)
            s2[key] = items
        oc = []
        for o in s2.get("outcomes") or []:
            if isinstance(o, dict):
                o = dict(o, covered_by=[r for r in o.get("covered_by") or [] if r not in {f"control:{d}" for d in dead}])
            oc.append(o)
        s2["outcomes"] = oc
        r2 = run_checks(s2, self.case)
        accepted = r2["n_major"] < report["n_major"]
        self.trace.event(stage, "autofix:prune_dead_controls", "accepted" if accepted else "rejected",
                         attempt=attempt, removed=dead, majors_before=report["n_major"], majors_after=r2["n_major"])
        return (s2, r2) if accepted else (spec, report)

    # ------------------------------------------------------------------ render
    def render(self, spec):
        html, kind = None, None
        try:
            from template.render import render as render_b  # Person B
            html, kind = render_b(spec), "template"
        except ImportError as e:
            self.trace.event("render", "import_template", "missing", error=str(e)[:200])
        except Exception as e:  # noqa: BLE001
            self.trace.event("render", "template_render", "fail", error=f"{type(e).__name__}: {e}"[:300])
        if html is not None:
            fl = check_page(html, self.case)
            majors = [f for f in fl if f["severity"] == "major"]
            self.trace.event("render", "check:c9_page", "fail" if majors else ("warn" if fl else "pass"),
                             renderer=kind, failures=[f["msg"] for f in fl])
            if not majors:
                return html, kind
        from .render_stub import render as render_stub
        try:
            html2 = render_stub(spec)
        except Exception as e:  # noqa: BLE001
            self.trace.event("render", "stub_render", "fail", error=f"{type(e).__name__}: {e}"[:300])
            return html, kind
        fl = check_page(html2, self.case)
        self.trace.event("render", "check:c9_page", "fail" if any(f["severity"] == "major" for f in fl) else "pass",
                         renderer="stub", failures=[f["msg"] for f in fl])
        return html2, "stub"

    def finish_with(self, spec, report, label):
        final, notes = sanitize(spec, report)
        final["_verification"].update({"model": self.model, "label": label})
        self.trace.event("fallback" if label != "verified" else "finalize", "sanitize", label, notes=notes)
        html, kind = self.render(final)
        if html:
            self.write_page(html, f"{label}/{kind}")
            return True
        return False

    # ------------------------------------------------------------------ main loop
    def run(self):
        self.trace.event("start", "config", "ok", model=self.model, max_calls=self.budget.max_calls,
                         max_completion=self.budget.max_completion, deadline_s=self.budget.deadline_s,
                         mock=bool(os.environ.get("P2P_MOCK")))
        if not self.load_case():
            self.write_page(static_page(self.case, "invalid case.json"), "static")
            return 2

        spec = report = None
        # 1) generate (one retry if the reply is unusable JSON / transport error)
        for attempt in (1, 2):
            try:
                raw = self.generate()
                spec, report = self.check(raw, "check", attempt)
                break
            except LLMError as e:
                self.trace.event("generate", "attempt_failed", "error", attempt=attempt, error=str(e)[:300])
                ok, why = self.budget.can_call(reserve_calls=1)
                if not ok:
                    break

        # 2) repair loop: only on major failures, send failing fields only
        n = 0
        while report is not None and not report["pass"] and n < MAX_REPAIRS:
            ok, why = self.budget.can_call(min_tokens=600, reserve_calls=0 if (self.best and usable(*self.best)) else 1)
            if not ok:
                self.trace.event("repair", "skip", "budget", reason=why); break
            n += 1
            try:
                patched = self.repair(spec, report, n)
            except LLMError as e:
                self.trace.event("repair", "attempt_failed", "error", repair_no=n, error=str(e)[:300])
                continue
            if json.dumps(patched, sort_keys=True, default=str) == json.dumps(spec, sort_keys=True, default=str):
                self.trace.event("repair", "revision", "no_change", repair_no=n)
            new_spec, new_report = self.check(patched, "recheck", n)
            improved = _score(new_report) < _score(report)
            same_errors = sorted(f["msg"] for f in new_report["failures"] if f["severity"] == "major") == \
                sorted(f["msg"] for f in report["failures"] if f["severity"] == "major")
            self.trace.event("repair", "revision", "improved" if improved else "not_improved", repair_no=n,
                             majors_before=report["n_major"], majors_after=new_report["n_major"])
            if improved or new_report["pass"]:
                spec, report = new_spec, new_report
            elif _score(new_report) == _score(report):
                spec, report = new_spec, new_report  # same score: keep newer, avoids re-sending identical fix
            # stop paying for repairs that cannot matter: only internal self-tests still fail and nothing moved
            only_selftests = all(f["check"] == "c6_selftests" for f in report["failures"] if f["severity"] == "major")
            if same_errors and only_selftests:
                self.trace.event("repair", "stop", "no_progress", reason="only self-tests fail; they are dropped by sanitize")
                break

        # 3) fallback ladder
        if self.best and self.best[1]["pass"]:
            if self.finish_with(*self.best, "verified"):
                return self.done(0)
        if self.best and usable(*self.best):
            if self.finish_with(*self.best, "partial"):
                return self.done(0)
        ok, why = self.budget.can_call(min_tokens=1500, min_seconds=40)
        if ok:
            try:
                raw = self.generate(emergency=True)
                es, er = self.check(raw, "emergency_check", 1)
                if usable(es, er) and self.finish_with(es, er, "emergency"):
                    return self.done(0)
            except LLMError as e:
                self.trace.event("emergency", "attempt_failed", "error", error=str(e)[:300])
        else:
            self.trace.event("emergency", "skip", "budget", reason=why)
        self.write_page(static_page(self.case, "no spec passed the checks", self.best[0] if self.best else None), "static")
        return self.done(1)

    def done(self, code):
        self.trace.event("end", "exit", "ok" if code == 0 else "fail", exit_code=code, totals=self.budget.summary(),
                         page_written=self.page_written)
        self.trace.close()
        return code


def main(input_path, out_dir, model):
    agent = Agent(input_path, out_dir, model)

    # hard watchdog: never exceed the 10-minute limit, never exit without a page
    def watchdog():
        try:
            agent.trace.event("watchdog", "timeout", "fail", elapsed_s=agent.trace.elapsed())
            if not agent.page_written:
                agent.write_page(static_page(agent.case, "time limit reached"), "static")
            agent.done(0 if agent.page_written else 1)
        finally:
            os._exit(0 if agent.page_written else 1)

    wd = threading.Timer(HARD_SECONDS - 25, watchdog)
    wd.daemon = True
    wd.start()
    try:
        return agent.run()
    except Exception as e:  # noqa: BLE001 - last line of defense
        import traceback
        agent.trace.event("crash", "exception", "fail", error=f"{type(e).__name__}: {e}"[:300],
                          where=traceback.format_exc().splitlines()[-3:])
        try:
            if agent.best and usable(*agent.best) and agent.finish_with(*agent.best, "partial"):
                return agent.done(0)
        except Exception:  # noqa: BLE001
            pass
        if not agent.page_written:
            agent.write_page(static_page(agent.case, "internal error"), "static")
        return agent.done(1)
    finally:
        wd.cancel()
