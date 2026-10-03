"""Person B — turns a spec dict into a single self-contained out/index.html.

Public API (A calls these):
    render(spec, out_dir)            -> {"path", "bytes", "warnings"}
    render_html(spec)                -> (html_str, warnings)
    render_fallback(case, reason, out_dir) -> path   # last-resort static page
    safety_issues(html_str)          -> list[str]    # for check 9

CLI preview:
    python -m template.render specs/entropy.json out
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

try:
    from latex2mathml.converter import convert as _l2m
except Exception:  # pragma: no cover - page still renders, equations as text
    _l2m = None

TEMPLATE = Path(__file__).with_name("page.html")

# {{name}}, {{name[0]}}, {{name[0][1]}}, optional :digits  e.g. {{H:2}}
PH_RE = re.compile(r"\{\{\s*([A-Za-z_]\w*(?:\[\d+\])*(?::\d)?)\s*\}\}")
INLINE_MATH_RE = re.compile(r"\\\((.+?)\\\)|(?<!\\)\$(.+?)(?<!\\)\$", re.S)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITAL_RE = re.compile(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])")
MTEXT_PH_RE = re.compile(r"<mtext>ZZPH(\d+)ZZ</mtext>")
CONTROL_TYPES = {"slider", "toggle", "select", "vector", "matrix"}
OUTPUT_TYPES = {"scalar", "vector", "matrix", "series", "table", "flow", "custom"}


# --------------------------------------------------------------------- LaTeX
def _strip_delims(tex: str) -> str:
    t = tex.strip()
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if t.startswith(a) and t.endswith(b) and len(t) >= len(a) + len(b):
            return t[len(a): len(t) - len(b)].strip()
    return t


def latex_to_mathml(tex: str, warnings: list[str], display: bool = False, where: str = "") -> str:
    """LaTeX -> MathML string. Live placeholders {{x}} become <mn data-ph="x">.
    On failure returns escaped LaTeX in <code> and records a warning."""
    if not isinstance(tex, str) or not tex.strip():
        return ""
    tex = _strip_delims(tex)
    phs: list[str] = []

    def _sub(m):
        phs.append(m.group(1))
        return r"\text{ZZPH%dZZ}" % (len(phs) - 1)

    work = PH_RE.sub(_sub, tex)
    if _l2m is None:
        warnings.append(f"latex2mathml not installed ({where})")
        return _tex_fallback(tex)
    try:
        out = _l2m(work, display="block" if display else "inline")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"LaTeX conversion failed in {where or 'field'}: {type(e).__name__}: {e} :: {tex[:120]}")
        return _tex_fallback(tex)
    if "<script" in out.lower() or "javascript:" in out.lower():
        warnings.append(f"unsafe MathML dropped in {where}")
        return _tex_fallback(tex)

    def _back(m):
        i = int(m.group(1))
        ph = phs[i] if i < len(phs) else "?"
        return f'<mn class="live" data-ph="{html.escape(ph, quote=True)}">…</mn>'

    out = MTEXT_PH_RE.sub(_back, out)
    if "ZZPH" in out:  # placeholder got mangled (e.g. inside \frac{}{}) — leave readable
        warnings.append(f"placeholder could not be wired in {where}: {tex[:80]}")
        out = re.sub(r"ZZPH(\d+)ZZ", lambda m: "?", out)
    return out


def _tex_fallback(tex: str) -> str:
    shown = PH_RE.sub(lambda m: "{{" + m.group(1) + "}}", tex)
    return f'<code class="tex-fallback">{html.escape(shown)}</code>'


def text_html(s, warnings: list[str], where: str = "") -> str:
    """Plain text with optional $inline LaTeX$ and **bold** -> safe HTML."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    parts, last = [], 0
    for m in INLINE_MATH_RE.finditer(s):
        parts.append(_esc_text(s[last:m.start()]))
        parts.append(latex_to_mathml(m.group(1) or m.group(2), warnings, where=where))
        last = m.end()
    parts.append(_esc_text(s[last:]))
    return "".join(parts)


def _esc_text(t: str) -> str:
    out, last = [], 0
    for m in PH_RE.finditer(t):  # keep {{placeholders}} intact, typeset the prose around them
        out.append(_prose(t[last:m.start()]))
        out.append(f'<b class="live" data-ph="{html.escape(m.group(1), quote=True)}">…</b>')
        last = m.end()
    out.append(_prose(t[last:]))
    return "".join(out)


def _prose(t: str) -> str:
    t = html.escape(t.replace("\\$", "$"))
    t = ITAL_RE.sub(r"<i>\1</i>", BOLD_RE.sub(r"<b>\1</b>", t))
    return _typeset(t)


# ---- plain-text labels written in ASCII math (p_i, QK^T, sqrt(d_k), log2) -> textbook HTML
_SUB_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z\u0391-\u03C9]{1,3})_(?:\{([^{}]{1,12})\}|([A-Za-z0-9]{1,4}|[+\-]))(?![A-Za-z0-9_])")
_SUP_RE = re.compile(r"(?<=[A-Za-z0-9)\]>\u0391-\u03C9])\^(?:\{([^{}]{1,20})\}|\(([^()]{1,20})\)|((?:-|&minus;)?[A-Za-z0-9\u0391-\u03C9]{1,3}))")
_SQRT_RE = re.compile(r"\bsqrt\(([^()]{1,30})\)")
_LOG_RE = re.compile(r"\blog(2|10)\b")
_MINUS_RE = re.compile(r"(^|[\s(=])-(?=[^\s\-.,;:])")


def _v(x: str) -> str:
    return f"<i>{x}</i>" if len(x) == 1 and x.isalpha() else x


def _typeset(t: str) -> str:
    """Typeset ASCII math (p_i, x^2, sqrt(d), log2, -p) in already-escaped text."""
    t = _SQRT_RE.sub(lambda m: '&radic;<span class="ovl">' + m.group(1) + "</span>", t)
    t = _LOG_RE.sub(r"log<sub>\1</sub>", t)
    t = re.sub(r"(?<=[0-9)])\s?\*\s?(?=[0-9(])", "&times;", t)
    t = _MINUS_RE.sub(lambda m: m.group(1) + "&minus;", t)
    t = re.sub(r"(?<=[A-Za-z0-9)\]\u0391-\u03C9]) - (?=[A-Za-z0-9(\u0391-\u03C9])", " &minus; ", t)

    def _sub(m):
        base, sub = m.group(1), (m.group(2) or m.group(3))
        if m.group(3) and len(base) > 1 and len(sub) > 1:  # looks like a code name (row_sums), leave it
            return m.group(0)
        return _v(base) + "<sub>" + ("".join(_v(c) for c in sub) if len(sub) == 1 else sub) + "</sub>"

    t = _SUB_RE.sub(_sub, t)
    t = _SUP_RE.sub(lambda m: "<sup>" + re.sub(r"^-", "&minus;", m.group(1) or m.group(2) or m.group(3)) + "</sup>", t)
    return t


def label_html(s, warnings: list[str] | None = None) -> str:
    """Escape a short label and typeset ASCII math in it. $...$ segments become MathML."""
    if s is None:
        return ""
    s = str(s)
    if "$" in s or "\\(" in s:
        return text_html(s, warnings if warnings is not None else [], "label")
    return _typeset(html.escape(s))


# ------------------------------------------------------------- normalisation
def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _src(x) -> str:
    return "paper" if str(x or "").strip().lower() == "paper" else "ours"


def _first(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def canonicalize(spec: dict) -> dict:
    """Accept the three team dialects (B: SPEC_FORMAT.md, A: SPEC.md, C: spec_format.md)
    and map them onto one shape. Unknown keys are ignored."""
    if not isinstance(spec, dict):
        return {}
    s = dict(spec)
    paper = dict(s.get("paper") or {}) if isinstance(s.get("paper"), dict) else {}
    paper["title"] = _first(paper, "title", "name")
    if paper.get("year") and paper.get("authors") and str(paper["year"]) not in str(paper["authors"]):
        paper["authors"] = f'{paper["authors"]} ({paper["year"]})'
    s["paper"] = paper
    s["symbols"] = [
        {"latex": _first(sy, "latex", "tex", "symbol"), "meaning": sy.get("meaning", "")}
        for sy in (s.get("symbols") or []) if isinstance(sy, dict)
    ]
    eq = s.get("equation")
    if isinstance(eq, dict):
        s["equation"] = {"latex": _first(eq, "latex", "tex"), "source": eq.get("source"),
                         "note": _first(eq, "note", "caption")}
    steps = []
    for st in s.get("steps") or []:
        if isinstance(st, dict):
            steps.append({"latex": _first(st, "latex", "tex"), "text": st.get("text"),
                          "note": _first(st, "note", "explain", "explanation")})
        else:
            steps.append(st)
    s["steps"] = steps
    tests = s.get("self_tests") or s.get("selftests") or s.get("selfTests") or []
    s["self_tests"] = [
        {"name": _first(t, "name", "why") or _first(t, "expect", "assert"), "set": t.get("set") or {},
         "expect": _first(t, "expect", "assert")}
        for t in tests if isinstance(t, dict)
    ]
    exps = []
    for ex in s.get("explorations") or []:
        if isinstance(ex, dict):
            ex = dict(ex)
            ex["expect"] = _first(ex, "expect", "assert")
            exps.append(ex)
    s["explorations"] = exps
    outs = []
    for o in s.get("outputs") or []:
        if isinstance(o, dict):
            o = dict(o)
            for a, b in (("rowLabels", "row_labels"), ("colLabels", "col_labels"),
                         ("xLabel", "x_label"), ("yLabel", "y_label")):
                if a in o and b not in o:
                    o[b] = o.pop(a)
            outs.append(o)
    s["outputs"] = outs
    for c in s.get("controls") or []:
        if isinstance(c, dict):
            for a, b in (("rowLabels", "row_labels"), ("colLabels", "col_labels")):
                if a in c and b not in c:
                    c[b] = c.pop(a)
    return s


def _looks_like_prose(tex: str) -> bool:
    """Model mixed sentences with \\( \\) / $ $ math -> render as text with inline math."""
    return "\\(" in tex or bool(re.search(r"(?<!\\)\$", tex))


_WORD_RUN = re.compile(r"(?<![\\A-Za-z])([A-Za-z]{2,}(?:[ ,]+[A-Za-z]{2,})*)")


def _wrap_words(tex: str) -> str:
    """Wrap bare English words (outside braces) in \\text{} so they don't render as italic letter soup.
    Only runs containing a word of 4+ letters; skips placeholders and LaTeX commands."""
    out, buf, depth, i = [], [], 0, 0

    def flush():
        chunk = "".join(buf)
        buf.clear()
        out.append(_WORD_RUN.sub(lambda m: "\\;\\text{" + m.group(1) + "}\\;" if re.search(r"[A-Za-z]{4,}", m.group(1)) else m.group(1), chunk))

    while i < len(tex):
        if tex.startswith("{{", i):
            j = tex.find("}}", i)
            if j > 0:
                flush(); out.append(tex[i:j + 2]); i = j + 2; continue
        ch = tex[i]
        if ch == "\\":  # command or escaped char: copy name verbatim
            m = re.match(r"\\([A-Za-z]+|.)", tex[i:])
            flush(); out.append(m.group(0)); i += len(m.group(0)); continue
        if ch == "{":
            if depth == 0: flush()
            depth += 1; out.append(ch); i += 1; continue
        if ch == "}":
            depth = max(0, depth - 1); out.append(ch); i += 1; continue
        (buf if depth == 0 else out).append(ch); i += 1
    flush()
    return "".join(out)


def _unknown_placeholders(spec: dict) -> list[str]:
    """Placeholder ids that are neither a control id nor a key in compute's return statement(s)."""
    ids = {c.get("id") for c in spec.get("controls") or [] if isinstance(c, dict)}
    ids |= {o.get("id") for o in spec.get("outputs") or [] if isinstance(o, dict)}
    src = str(spec.get("compute") or "")
    ids |= set(re.findall(r"([A-Za-z_]\w*)\s*:", src)) | set(re.findall(r"[{,]\s*([A-Za-z_]\w*)\s*(?=[,}])", src))
    used = set()
    for st in spec.get("steps") or []:
        txt = st if isinstance(st, str) else (st.get("latex") or st.get("text") or "") if isinstance(st, dict) else ""
        used |= {m.group(1).split("[")[0].split(":")[0] for m in PH_RE.finditer(str(txt))}
    return sorted(u for u in used if u not in ids)


def build_view(spec: dict) -> tuple[dict, list[str]]:
    """Normalise a (possibly imperfect) spec into what page.html expects.
    Never raises on bad content: drops/hides broken parts and records warnings."""
    w: list[str] = []
    spec = canonicalize(spec)
    paper = spec.get("paper") if isinstance(spec.get("paper"), dict) else {}
    view: dict = {
        "title": str(spec.get("title") or "Interactive explanation"),
        "audience": spec.get("audience") or "",
        "paper": {k: (str(paper[k]) if paper.get(k) not in (None, "") else None)
                  for k in ("title", "authors", "url", "section", "equation")},
        "hook_html": text_html(spec.get("hook"), w, "hook"),
        "hide_undeclared": bool(spec.get("hide_undeclared", False)),
    }
    eqlab = view["paper"]["equation"]
    if eqlab and (re.search(r"[\\^_=]", eqlab) or len(eqlab) > 25):  # a formula, not a label like "Eq. (1)"
        w.append(f"paper.equation looks like a formula, not a label; dropped: {eqlab[:60]}")
        view["paper"]["equation"] = None
    sec = view["paper"]["section"]
    if sec and re.fullmatch(r"\d+(\.\d+)*", sec.strip()):
        view["paper"]["section"] = "Section " + sec.strip()
    url = view["paper"]["url"]
    if url and not re.match(r"^https?://", url):
        view["paper"]["url"] = None

    view["symbols"] = []
    for i, sy in enumerate(_as_list(spec.get("symbols"))):
        if isinstance(sy, dict) and sy.get("latex"):
            view["symbols"].append({
                "latex_html": latex_to_mathml(sy["latex"], w, where=f"symbols[{i}]"),
                "meaning_html": text_html(sy.get("meaning", ""), w, f"symbols[{i}]"),
                "source": _src(sy.get("source")) if sy.get("source") else None,
            })

    eq = spec.get("equation")
    if isinstance(eq, str):
        eq = {"latex": eq, "source": "ours"}
    if isinstance(eq, dict) and eq.get("latex"):
        view["equation"] = {
            "html": latex_to_mathml(eq["latex"], w, display=True, where="equation"),
            "source": _src(eq.get("source")),
            "note_html": text_html(eq.get("note"), w, "equation.note") if eq.get("note") else "",
        }

    # controls
    view["controls"] = []
    for i, c in enumerate(_as_list(spec.get("controls"))):
        if not isinstance(c, dict) or not c.get("id"):
            w.append(f"controls[{i}] dropped: missing id")
            continue
        c = dict(c)
        if c.get("type") not in CONTROL_TYPES:
            w.append(f"controls[{i}] '{c.get('id')}' dropped: unknown type {c.get('type')!r}")
            continue
        if "default" not in c:
            c["default"] = {"toggle": False, "slider": c.get("min", 0), "vector": [], "matrix": []}.get(c["type"])
            if c["type"] == "select" and c.get("options"):
                o = c["options"][0]
                c["default"] = o.get("value") if isinstance(o, dict) else o
        c["label_html"] = label_html(c.get("label") or c["id"], w)
        view["controls"].append(c)

    view["outputs"] = []
    for i, o in enumerate(_as_list(spec.get("outputs"))):
        if not isinstance(o, dict) or not o.get("id"):
            continue
        o = dict(o)
        if o.get("type") and o["type"] not in OUTPUT_TYPES:
            w.append(f"outputs[{i}] type {o['type']!r} unknown; will infer from data")
            o.pop("type")
        o["label_html"] = label_html(o.get("label") or o["id"], w)
        if o.get("caption"):
            o["caption_html"] = text_html(o.pop("caption"), w, f"outputs[{i}].caption")
        view["outputs"].append(o)

    view["compute"] = str(spec.get("compute") or "")

    view["steps"] = []
    for i, st in enumerate(_as_list(spec.get("steps"))):
        if isinstance(st, str):
            st = {"latex": st}
        if isinstance(st, dict) and (st.get("latex") or st.get("text")):
            lx = st.get("latex")
            if lx and _looks_like_prose(lx):  # model mixed words and \( math \) -> render as text + inline math
                item = {"html": text_html(lx, w, f"steps[{i}]")}
            else:
                item = {"html": latex_to_mathml(_wrap_words(lx), w, where=f"steps[{i}]") if lx
                        else text_html(st.get("text"), w, f"steps[{i}]")}
            if st.get("note"):
                item["note_html"] = text_html(st["note"], w, f"steps[{i}].note")
            view["steps"].append(item)

    for u in _unknown_placeholders(spec):
        w.append(f"steps use {{{{{u}}}}} but no control/output named '{u}' exists; it will show as a dash")
    view["explorations"] = []
    for i, ex in enumerate(_as_list(spec.get("explorations"))):
        if not isinstance(ex, dict):
            continue
        view["explorations"].append({
            "title": str(ex.get("title") or f"Exploration {i + 1}"),
            "predict_html": text_html(ex.get("predict"), w, f"explorations[{i}].predict"),
            "set": ex.get("set") if isinstance(ex.get("set"), dict) else {},
            "observe_html": text_html(ex.get("observe"), w, f"explorations[{i}].observe"),
            "why_html": text_html(ex.get("why"), w, f"explorations[{i}].why"),
            "expect": ex.get("expect") if isinstance(ex.get("expect"), str) else None,
        })

    view["limitation_html"] = text_html(spec.get("limitation"), w, "limitation")
    view["misconception_html"] = text_html(spec.get("misconception"), w, "misconception")
    view["claims"] = [
        {"html": text_html(c.get("text"), w, f"claims[{i}]"), "source": _src(c.get("source"))}
        for i, c in enumerate(_as_list(spec.get("claims"))) if isinstance(c, dict) and c.get("text")
    ]
    view["self_tests"] = [t for t in _as_list(spec.get("self_tests")) if isinstance(t, dict) and t.get("expect")]

    # ---- optional enrichments (all free if the spec already has them)
    ctl_names = {c["id"]: c.get("label") or c["id"] for c in view["controls"]}
    out_names = {o["id"]: o.get("label") or o["id"] for o in view["outputs"]}

    def _via(ref):
        kind, _, rid = str(ref).partition(":")
        if kind == "control" and rid in ctl_names:
            return "the control " + ctl_names[rid]
        if kind == "output" and rid in out_names:
            return out_names[rid]
        if kind == "exploration" and rid.isdigit():
            return "Exercise " + rid
        return None

    view["outcomes"] = []
    for i, oc in enumerate(_as_list(spec.get("outcomes"))):
        text = oc.get("outcome") if isinstance(oc, dict) else oc
        if not text:
            continue
        via = [v for v in (_via(x) for x in _as_list(oc.get("covered_by") if isinstance(oc, dict) else [])) if v]
        view["outcomes"].append({"html": text_html(text, w, f"outcomes[{i}]"),
                                 "via_html": label_html(", ".join(dict.fromkeys(via)), w) if via else ""})
    view["context_html"] = text_html(spec.get("context") or spec.get("background"), w, "context")
    view["intuition_html"] = text_html(spec.get("intuition"), w, "intuition")
    tk = spec.get("takeaways") or spec.get("key_takeaways")
    view["takeaways"] = [text_html(x, w, "takeaways") for x in (_as_list(tk) if not isinstance(tk, str) else [tk]) if x]
    ver = spec.get("_verification") if isinstance(spec.get("_verification"), dict) else {}
    ran = len(_as_list(ver.get("self_tests"))) + len(_as_list(ver.get("explorations"))) + int(ver.get("edge_runs") or 0)
    if ver.get("summary") and ran > 0:  # never show an empty "0/0 checks" line
        view["verification"] = {"summary": str(ver["summary"])[:300], "passed": bool(ver.get("passed")),
                                "open_issues": [str(x)[:140] for x in _as_list(ver.get("open_issues"))][:3]}
    return view, w


# ------------------------------------------------------------------ output
def _safe_json(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _fill(template: str, title: str, spec_json: str, eq_html: str = "") -> str:
    vals = {"__TITLE__": html.escape(title), "__SPEC_JSON__": spec_json, "__EQUATION_HTML__": eq_html}
    return re.sub(r"__TITLE__|__SPEC_JSON__|__EQUATION_HTML__", lambda m: vals[m.group(0)], template, count=3)


def render_html(spec: dict) -> tuple[str, list[str]]:
    view, warnings = build_view(spec)
    try:
        payload = _safe_json(view)
    except ValueError as e:  # NaN/Infinity in defaults etc.
        warnings.append(f"spec contained non-JSON numbers: {e}")
        payload = _safe_json(json.loads(json.dumps(view, default=str).replace("NaN", "null")
                                        .replace("-Infinity", "null").replace("Infinity", "null")))
    eq_html = (view.get("equation") or {}).get("html", "")  # static copy: visible before JS runs
    return _fill(TEMPLATE.read_text(encoding="utf-8"), view["title"], payload, eq_html), warnings


render_report: dict = {}


def render(spec: dict, out_dir: str | Path | None = None):
    """render(spec) -> HTML string (A's SPEC.md interface).
    render(spec, out_dir) -> writes out_dir/index.html and returns {"path","bytes","warnings"}.
    Either way the latest warnings are in template.render.render_report."""
    page, warnings = render_html(spec)
    render_report.clear()
    render_report.update({"warnings": warnings, "bytes": len(page.encode("utf-8")), "safety_issues": safety_issues(page)})
    if out_dir is None:
        return page
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return {"path": str(path), "bytes": render_report["bytes"], "warnings": warnings}


def render_fallback(case: dict | None, reason: str, out_dir: str | Path) -> str:
    """Static last-resort page (no JS needed). A uses this before exiting nonzero."""
    case = case or {}
    url = str(case.get("source_url") or "")
    link = (f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{html.escape(url)}</a>'
            if re.match(r"^https?://", url) else html.escape(url))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Explanation unavailable</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#1d1f23;background:#f7f7f5}}
.box{{background:#fff;border:1px solid #e2e2de;border-radius:12px;padding:20px}}</style></head><body><div class="box">
<h1>Interactive explanation could not be generated</h1>
<p><b>Topic requested:</b> {html.escape(str(case.get('focus') or ''))}</p>
<p><b>Audience:</b> {html.escape(str(case.get('audience') or ''))}</p>
<p><b>Source:</b> {link}</p>
<p><b>Reason:</b> {html.escape(reason)}</p>
</div></body></html>"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "index.html"
    p.write_text(page, encoding="utf-8")
    return str(p)


_EXT_PATTERNS = [
    (re.compile(r"<script[^>]+\bsrc\s*=", re.I), "external <script src>"),
    (re.compile(r"<link[^>]+\bhref\s*=", re.I), "<link href> (stylesheet/font)"),
    (re.compile(r"<(img|iframe|video|audio|source|embed|object)[^>]+\bsrc\s*=\s*[\"']?(https?:)?//", re.I), "remote media"),
    (re.compile(r"@import", re.I), "CSS @import"),
    (re.compile(r"url\(\s*[\"']?(https?:)?//", re.I), "remote url() in CSS"),
    (re.compile(r"\bfetch\s*\(|XMLHttpRequest|WebSocket\s*\(", re.I), "network call in JS"),
]


def safety_issues(page: str) -> list[str]:
    """Check 9 helper: things that would break offline use. <a href> links to the paper are allowed."""
    issues = [msg for rx, msg in _EXT_PATTERNS if rx.search(page)]
    m = re.search(r'<script type="application/json" id="spec">(.*?)</script>', page, re.S)
    if not m:
        issues.append("embedded spec block missing or broken (</script> not escaped?)")
    else:
        try:
            json.loads(m.group(1))
        except ValueError as e:
            issues.append(f"embedded spec is not valid JSON: {e}")
    if 'class=\\"tex-fallback' in page or 'class="tex-fallback' in page:
        issues.append("some LaTeX shown as plain text (conversion failed)")
    return issues


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m template.render SPEC.json [OUT_DIR]")
        sys.exit(2)
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    res = render(spec, sys.argv[2] if len(sys.argv) > 2 else "out")
    page = Path(res["path"]).read_text(encoding="utf-8")
    print(json.dumps({**res, "safety_issues": safety_issues(page)}, indent=2))
