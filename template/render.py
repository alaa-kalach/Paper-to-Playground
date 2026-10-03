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
INLINE_MATH_RE = re.compile(r"(?<!\\)\$(.+?)(?<!\\)\$", re.S)
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
        parts.append(latex_to_mathml(m.group(1), warnings, where=where))
        last = m.end()
    parts.append(_esc_text(s[last:]))
    return "".join(parts)


def _esc_text(t: str) -> str:
    t = html.escape(t.replace("\\$", "$"))
    return ITAL_RE.sub(r"<i>\1</i>", BOLD_RE.sub(r"<b>\1</b>", t))


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
    url = view["paper"]["url"]
    if url and not re.match(r"^https?://", url):
        view["paper"]["url"] = None

    view["symbols"] = []
    for i, sy in enumerate(_as_list(spec.get("symbols"))):
        if isinstance(sy, dict) and sy.get("latex"):
            view["symbols"].append({
                "latex_html": latex_to_mathml(sy["latex"], w, where=f"symbols[{i}]"),
                "meaning_html": text_html(sy.get("meaning", ""), w, f"symbols[{i}]"),
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
        view["controls"].append(c)

    view["outputs"] = []
    for i, o in enumerate(_as_list(spec.get("outputs"))):
        if not isinstance(o, dict) or not o.get("id"):
            continue
        o = dict(o)
        if o.get("type") and o["type"] not in OUTPUT_TYPES:
            w.append(f"outputs[{i}] type {o['type']!r} unknown; will infer from data")
            o.pop("type")
        if o.get("caption"):
            o["caption_html"] = text_html(o.pop("caption"), w, f"outputs[{i}].caption")
        view["outputs"].append(o)

    view["compute"] = str(spec.get("compute") or "")

    view["steps"] = []
    for i, st in enumerate(_as_list(spec.get("steps"))):
        if isinstance(st, str):
            st = {"latex": st}
        if isinstance(st, dict) and (st.get("latex") or st.get("text")):
            item = {"html": latex_to_mathml(st["latex"], w, where=f"steps[{i}]") if st.get("latex")
                    else text_html(st.get("text"), w, f"steps[{i}]")}
            if st.get("note"):
                item["note_html"] = text_html(st["note"], w, f"steps[{i}].note")
            view["steps"].append(item)

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
    return view, w


# ------------------------------------------------------------------ output
def _safe_json(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _fill(template: str, title: str, spec_json: str) -> str:
    vals = {"__TITLE__": html.escape(title), "__SPEC_JSON__": spec_json}
    return re.sub(r"__TITLE__|__SPEC_JSON__", lambda m: vals[m.group(0)], template, count=2)


def render_html(spec: dict) -> tuple[str, list[str]]:
    view, warnings = build_view(spec)
    try:
        payload = _safe_json(view)
    except ValueError as e:  # NaN/Infinity in defaults etc.
        warnings.append(f"spec contained non-JSON numbers: {e}")
        payload = _safe_json(json.loads(json.dumps(view, default=str).replace("NaN", "null")
                                        .replace("-Infinity", "null").replace("Infinity", "null")))
    return _fill(TEMPLATE.read_text(encoding="utf-8"), view["title"], payload), warnings


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
