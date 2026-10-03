"""Spec normalization and checks 1-9 (see plan). Pure Python + QuickJS, no network.

Each failure: {"check": "c3_compute", "severity": "major|minor", "fields": [...], "msg": "..."}
major  -> triggers repair; minor -> logged, sent along only if a repair happens anyway.
"""
import copy
import json
import math
import re

from .jsrun import Sandbox, JSError, nonfinite_paths, HAVE_QJS

CONTROL_TYPES = {"slider", "toggle", "select", "vector", "matrix"}
OUTPUT_TYPES = {"scalar", "vector", "matrix", "series", "table", "flow"}
RESERVED_KEYS = {"insight", "warning"}
IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
MAX_VEC = 12
MAX_CELLS = 36


def F(check, msg, fields, severity="major"):
    return {"check": check, "severity": severity, "fields": list(fields), "msg": msg[:400]}


# ---------------------------------------------------------------------------
# Normalization (cheap auto-fixes instead of paying for a repair call)
# ---------------------------------------------------------------------------
_CTRL_LATEX = {"\x0c": "\\f", "\x08": "\\b", "\t": "\\t", "\r": "\\r", "\x07": "\\a", "\x0b": "\\v"}


def fix_latex(s):
    """Undo JSON escapes that ate LaTeX commands: \\frac -> formfeed+'rac', \\nabla -> newline+'abla'."""
    if not isinstance(s, str):
        return s
    for k, v in _CTRL_LATEX.items():
        s = s.replace(k, v)
    return re.sub(r"\n(?=[a-zA-Z])", r"\\n", s)


def _num(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _src_tag(x):
    x = str(x or "").lower()
    return "paper" if x.startswith("paper") or x in ("excerpt", "source") else "ours"


def normalize(spec, case):
    """Return (spec, fixes). Never raises on odd shapes; checks report what is still wrong."""
    s = copy.deepcopy(spec) if isinstance(spec, dict) else {}
    fixes = []

    paper = s.get("paper") if isinstance(s.get("paper"), dict) else {}
    if paper.get("url") != case.get("source_url"):
        paper["url"] = case.get("source_url"); fixes.append("paper.url set to source_url")
    s["paper"] = paper

    eq = s.get("equation")
    if isinstance(eq, str):
        s["equation"] = eq = {"latex": eq, "source": "paper"}; fixes.append("equation string -> object")
    if isinstance(eq, dict):
        eq["latex"] = fix_latex(eq.get("latex"))
        eq["source"] = _src_tag(eq.get("source"))
    for sym in s.get("symbols") or []:
        if isinstance(sym, dict):
            sym["symbol"] = fix_latex(sym.get("symbol"))
            sym["source"] = _src_tag(sym.get("source"))
    if isinstance(s.get("steps"), list):
        s["steps"] = [fix_latex(x) for x in s["steps"] if isinstance(x, str)]
    for c in s.get("claims") or []:
        if isinstance(c, dict):
            c["source"] = _src_tag(c.get("source"))
    if isinstance(s.get("compute"), str):
        s["compute"] = s["compute"].strip()
        if s["compute"].startswith("```"):
            s["compute"] = re.sub(r"^```\w*\n?|```$", "", s["compute"]).strip(); fixes.append("compute fences stripped")

    for key in ("explorations", "self_tests"):
        for it in s.get(key) or []:
            if isinstance(it, dict) and not it.get("expect"):
                for alias in ("assert", "check", "expected", "assertion"):
                    if isinstance(it.get(alias), str) and it[alias].strip():
                        it["expect"] = it[alias]; break

    for c in s.get("controls") or []:
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t == "slider":
            lo, hi = _num(c.get("min"), 0.0), _num(c.get("max"), 1.0)
            if lo > hi:
                lo, hi = hi, lo; fixes.append(f"{c.get('id')}: min/max swapped")
            c["min"], c["max"] = lo, hi
            st = _num(c.get("step"))
            if not st or st <= 0:
                c["step"] = (hi - lo) / 100 or 1; fixes.append(f"{c.get('id')}: step defaulted")
            d = _num(c.get("default"), lo)
            if d < lo or d > hi:
                fixes.append(f"{c.get('id')}: default clamped")
            c["default"] = min(max(d, lo), hi)
        elif t == "toggle":
            if not isinstance(c.get("default"), bool):
                c["default"] = bool(c.get("default")); fixes.append(f"{c.get('id')}: default -> bool")
        elif t == "select":
            opts = []
            for o in c.get("options") or []:
                opts.append(o if isinstance(o, dict) else {"value": str(o), "label": str(o)})
            for o in opts:
                o["value"] = str(o.get("value")); o.setdefault("label", o["value"])
            c["options"] = opts
            vals = [o["value"] for o in opts]
            if opts and str(c.get("default")) not in vals:
                c["default"] = vals[0]; fixes.append(f"{c.get('id')}: default -> first option")
            else:
                c["default"] = str(c.get("default"))
        elif t in ("vector", "matrix"):
            lo, hi = _num(c.get("min"), -10.0), _num(c.get("max"), 10.0)
            if lo > hi:
                lo, hi = hi, lo
            c["min"], c["max"] = lo, hi
            st = _num(c.get("step"))
            if not st or st <= 0:
                c["step"] = (hi - lo) / 100 or 0.1
            d = c.get("default")
            if t == "vector" and isinstance(d, list):
                c["default"] = [min(max(_num(v, lo), lo), hi) for v in d]
                n = int(_num(c.get("length"), len(d)) or len(d))
                if n != len(c["default"]):
                    fixes.append(f"{c.get('id')}: length {n} -> {len(c['default'])}")
                c["length"] = len(c["default"])
            if t == "matrix" and isinstance(d, list) and d and all(isinstance(r, list) for r in d):
                c["default"] = [[min(max(_num(v, lo), lo), hi) for v in r] for r in d]
                c["rows"], c["cols"] = len(d), len(d[0])
    _fix_set_values(s, fixes)
    if fixes:
        s.setdefault("_autofix", []).extend(fixes)
    return s, fixes


def _fix_set_values(s, fixes):
    """Explorations/self-tests often use a valid value just outside the declared range (fpr=0 when min=0.01)
    or a too-short vector. Widen the range / pad the vector instead of paying for a repair."""
    cmap = {c.get("id"): c for c in s.get("controls") or [] if isinstance(c, dict)}
    for key in ("explorations", "self_tests"):
        for it in s.get(key) or []:
            st = it.get("set") if isinstance(it, dict) else None
            if not isinstance(st, dict):
                continue
            for k, v in list(st.items()):
                c = cmap.get(k)
                if not c:
                    continue
                t = c.get("type")
                if t == "toggle" and isinstance(v, str) and v.lower() in ("true", "false"):
                    st[k] = v = v.lower() == "true"
                if t in ("slider", "vector", "matrix") and isinstance(v, str) and _num(v) is not None:
                    st[k] = v = _num(v)
                if t == "vector" and isinstance(v, list) and isinstance(c.get("default"), list):
                    n = len(c["default"])
                    if 0 < len(v) < n and all(_num(x) is not None for x in v):
                        pad = 0.0 if c["min"] <= 0 <= c["max"] else c["min"]
                        st[k] = v = list(v) + [pad] * (n - len(v))
                        fixes.append(f"{key} set.{k}: padded to length {n}")
                nums = []
                if t == "slider" and _num(v) is not None and not isinstance(v, bool):
                    nums = [_num(v)]
                elif t == "vector" and isinstance(v, list):
                    nums = [_num(x) for x in v if _num(x) is not None]
                elif t == "matrix" and isinstance(v, list):
                    nums = [_num(x) for r in v if isinstance(r, list) for x in r if _num(x) is not None]
                if nums:
                    lo, hi = min(nums), max(nums)
                    span = (c["max"] - c["min"]) or 1
                    lim = (10 if t == "slider" else 2) * span
                    if lo < c["min"] and c["min"] - lo <= lim:
                        fixes.append(f"{k}: min {c['min']} -> {lo} (used in {key})"); c["min"] = lo
                    if hi > c["max"] and hi - c["max"] <= lim:
                        fixes.append(f"{k}: max {c['max']} -> {hi} (used in {key})"); c["max"] = hi


# ---------------------------------------------------------------------------
# Check 1: schema
# ---------------------------------------------------------------------------
def _nonempty_str(x):
    return isinstance(x, str) and x.strip() != ""


def check_schema(s):
    fl = []
    for k in ("title", "hook", "limitation"):
        if not _nonempty_str(s.get(k)):
            fl.append(F("c1_schema", f"'{k}' must be a non-empty string", [k]))
    if not _nonempty_str(s.get("misconception")):
        fl.append(F("c1_schema", "'misconception' missing", ["misconception"], "minor"))
    eq = s.get("equation")
    if not (isinstance(eq, dict) and _nonempty_str(eq.get("latex"))):
        fl.append(F("c1_schema", "'equation.latex' missing", ["equation"]))
    if not (isinstance(s.get("symbols"), list) and s["symbols"]):
        fl.append(F("c1_schema", "'symbols' should list the main symbols", ["symbols"], "minor"))
    if not _nonempty_str(s.get("compute")):
        fl.append(F("c1_schema", "'compute' JS source missing", ["compute"]))
    if not (isinstance(s.get("steps"), list) and s["steps"]):
        fl.append(F("c1_schema", "'steps' missing", ["steps"], "minor"))
    claims = s.get("claims")
    if not (isinstance(claims, list) and any(isinstance(c, dict) and _nonempty_str(c.get("text")) for c in claims)):
        fl.append(F("c1_schema", "'claims' must list statements tagged paper|ours", ["claims"]))
    if not isinstance(s.get("self_tests"), list) or not s["self_tests"]:
        fl.append(F("c1_schema", "'self_tests' missing: add >=1 known-answer test", ["self_tests"], "minor"))

    # controls
    ctrls = s.get("controls")
    if not isinstance(ctrls, list) or len(ctrls) < 2:
        fl.append(F("c1_schema", "need at least 2 controls", ["controls"]))
        ctrls = ctrls if isinstance(ctrls, list) else []
    ids = set()
    for i, c in enumerate(ctrls):
        if not isinstance(c, dict):
            fl.append(F("c1_schema", f"controls[{i}] is not an object", ["controls"])); continue
        cid, t = c.get("id"), c.get("type")
        tag = f"control '{cid}'"
        if not (isinstance(cid, str) and IDENT.match(cid)):
            fl.append(F("c1_schema", f"controls[{i}].id must be a JS identifier", ["controls"])); continue
        if cid in ids:
            fl.append(F("c1_schema", f"duplicate control id '{cid}'", ["controls"]))
        ids.add(cid)
        if t not in CONTROL_TYPES:
            fl.append(F("c1_schema", f"{tag}: type must be one of {sorted(CONTROL_TYPES)}", ["controls"])); continue
        if not _nonempty_str(c.get("label")):
            fl.append(F("c1_schema", f"{tag}: missing label", ["controls"], "minor"))
        if t == "slider" and c["min"] == c["max"]:
            fl.append(F("c1_schema", f"{tag}: min == max", ["controls"]))
        if t == "select" and len(c.get("options") or []) < 2:
            fl.append(F("c1_schema", f"{tag}: select needs >=2 options", ["controls"]))
        if t == "vector":
            d = c.get("default")
            if not (isinstance(d, list) and 1 <= len(d) <= MAX_VEC):
                fl.append(F("c1_schema", f"{tag}: default must be a list of 1..{MAX_VEC} numbers", ["controls"]))
        if t == "matrix":
            d = c.get("default")
            ok = isinstance(d, list) and d and all(isinstance(r, list) and len(r) == len(d[0]) and r for r in d)
            if not ok or len(d) * len(d[0]) > MAX_CELLS:
                fl.append(F("c1_schema", f"{tag}: default must be a rectangular list of lists (<= {MAX_CELLS} cells)", ["controls"]))

    # outputs
    outs = s.get("outputs")
    if not isinstance(outs, list) or not outs:
        fl.append(F("c1_schema", "need at least 1 declared output", ["outputs"]))
        outs = []
    oids = set()
    for i, o in enumerate(outs):
        if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not IDENT.match(o.get("id", "")):
            fl.append(F("c1_schema", f"outputs[{i}] needs an identifier id", ["outputs"])); continue
        if o["id"] in RESERVED_KEYS:
            fl.append(F("c1_schema", f"output id '{o['id']}' is reserved", ["outputs"]))
        if o["id"] in oids:
            fl.append(F("c1_schema", f"duplicate output id '{o['id']}'", ["outputs"]))
        oids.add(o["id"])
        if o.get("type") not in OUTPUT_TYPES:
            fl.append(F("c1_schema", f"output '{o['id']}': type must be one of {sorted(OUTPUT_TYPES)}", ["outputs"]))

    ex = s.get("explorations")
    if not isinstance(ex, list) or len(ex) < 2:
        fl.append(F("c1_schema", "need exactly 2 explorations", ["explorations"]))
    else:
        for i, e in enumerate(ex[:2]):
            miss = [k for k in ("title", "predict", "observe", "why") if not (isinstance(e, dict) and _nonempty_str(e.get(k)))]
            if miss:
                fl.append(F("c1_schema", f"exploration {i+1} missing {miss}", ["explorations"]))
            if isinstance(e, dict) and not isinstance(e.get("set"), dict):
                fl.append(F("c1_schema", f"exploration {i+1}: 'set' must be an object of control values", ["explorations"]))
    return fl


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------
def defaults(s):
    return {c["id"]: copy.deepcopy(c.get("default")) for c in s.get("controls") or [] if isinstance(c, dict) and "id" in c}


def validate_value(c, v):
    t = c.get("type")
    if t == "slider":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and c["min"] - 1e-9 <= v <= c["max"] + 1e-9
    if t == "toggle":
        return isinstance(v, bool)
    if t == "select":
        return str(v) in [o["value"] for o in c.get("options") or []]
    if t == "vector":
        return isinstance(v, list) and len(v) == len(c.get("default") or []) and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and c["min"] - 1e-9 <= x <= c["max"] + 1e-9 for x in v)
    if t == "matrix":
        d = c.get("default") or [[]]
        return isinstance(v, list) and len(v) == len(d) and all(
            isinstance(r, list) and len(r) == len(d[0]) and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and c["min"] - 1e-9 <= x <= c["max"] + 1e-9 for x in r)
            for r in v)
    return False


def apply_set(s, setobj):
    """Return (inputs, errors) for defaults overridden by setobj."""
    inp = defaults(s)
    errs = []
    cmap = {c["id"]: c for c in s.get("controls") or [] if isinstance(c, dict) and "id" in c}
    for k, v in (setobj or {}).items():
        if k not in cmap:
            errs.append(f"unknown control '{k}'"); continue
        if cmap[k]["type"] == "select":
            v = str(v)
        if cmap[k]["type"] == "slider" and isinstance(v, (int, float)):
            v = float(v)
        if not validate_value(cmap[k], v):
            errs.append(f"value for '{k}' violates its type/length/range: {json.dumps(v)[:80]}")
        inp[k] = v
    return inp, errs


def edge_inputs(c):
    """Edge-case values for one control (check 4)."""
    t, out = c["type"], []
    if t == "slider":
        out = [c["min"], c["max"]] + ([0.0] if c["min"] < 0 < c["max"] else [])
    elif t == "toggle":
        out = [True, False]
    elif t == "select":
        out = [o["value"] for o in c["options"]]
    elif t == "vector":
        n, lo, hi = len(c["default"]), c["min"], c["max"]
        mid = (lo + hi) / 2
        out = [[lo] * n, [hi] * n, [mid] * n, [hi] + [lo] * (n - 1)]
        if lo <= 0 <= hi:
            out.append([0.0] * n)
    elif t == "matrix":
        r, k, lo, hi = c["rows"], c["cols"], c["min"], c["max"]
        mid = (lo + hi) / 2
        out = [[[v] * k for _ in range(r)] for v in (lo, hi, mid)]
        if lo <= 0 <= hi:
            out.append([[0.0] * k for _ in range(r)])
    return out


def perturbations(c):
    """Values that differ from the default (check 5)."""
    t, d = c["type"], c["default"]
    if t == "slider":
        cands = [c["min"], c["max"], (c["min"] + c["max"]) / 2, d + c["step"], d - c["step"]]
        return [v for v in cands if c["min"] <= v <= c["max"] and abs(v - d) > 1e-12]
    if t == "toggle":
        return [not d]
    if t == "select":
        return [o["value"] for o in c["options"] if o["value"] != d]
    if t == "vector":
        res = []
        for i in range(len(d)):
            for v in (c["max"], c["min"], (c["max"] + c["min"]) / 2):
                if abs(v - d[i]) > 1e-12:
                    x = list(d); x[i] = v; res.append(x); break
        return res
    if t == "matrix":
        res = []
        for i in range(len(d)):
            for j in range(len(d[0])):
                for v in (c["max"], c["min"]):
                    if abs(v - d[i][j]) > 1e-12:
                        x = copy.deepcopy(d); x[i][j] = v; res.append(x); break
        return res[:MAX_CELLS]
    return []


# ---------------------------------------------------------------------------
# Output shape validation (part of check 3)
# ---------------------------------------------------------------------------
def _isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def shape_error(o, v):
    t = o.get("type")
    if t == "scalar":
        return None if _isnum(v) else "scalar must be a number"
    if t == "vector":
        return None if isinstance(v, list) and v and all(_isnum(x) for x in v) else "vector must be a non-empty number[]"
    if t == "matrix":
        ok = isinstance(v, list) and v and all(isinstance(r, list) and r and len(r) == len(v[0]) and all(_isnum(x) for x in r) for r in v)
        return None if ok else "matrix must be a rectangular number[][]"
    if t == "series":
        if not isinstance(v, dict) or not isinstance(v.get("x"), list) or not isinstance(v.get("lines"), list) or not v["lines"]:
            return "series must be {x: number[], lines: [{name, y: number[]}]}"
        for ln in v["lines"]:
            if not isinstance(ln, dict) or not isinstance(ln.get("y"), list) or len(ln["y"]) != len(v["x"]):
                return "each series line needs y with the same length as x"
        return None
    if t == "table":
        ok = isinstance(v, dict) and isinstance(v.get("columns"), list) and isinstance(v.get("rows"), list) and all(
            isinstance(r, list) and len(r) == len(v["columns"]) for r in v["rows"])
        return None if ok else "table must be {columns: [...], rows: [[...]]} with matching row lengths"
    if t == "flow":
        ok = isinstance(v, dict) and isinstance(v.get("nodes"), list) and v["nodes"] and isinstance(v.get("edges"), list)
        if ok:
            ids = {n.get("id") for n in v["nodes"] if isinstance(n, dict)}
            ok = all(isinstance(e, dict) and e.get("from") in ids and e.get("to") in ids for e in v["edges"])
        return None if ok else "flow must be {nodes:[{id,label}], edges:[{from,to}]} referencing node ids"
    return "unknown output type"


def _close(a, b):
    if _isnum(a) and _isnum(b):
        return abs(a - b) <= 1e-9 * max(1, abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    return a == b


def _declared(res, s):
    return {o["id"]: res.get(o["id"]) for o in s.get("outputs") or [] if isinstance(o, dict) and "id" in o}


def _round(v):
    if _isnum(v):
        return float(f"{v:.4g}")
    if isinstance(v, list):
        return [_round(x) for x in v]
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    return v


def _explain(expr, out, inp, base):
    """Show the values an assertion refers to, so a repair can see why it is false."""
    parts, arrays = [], False
    seen = set()
    for src, name in re.findall(r"\b(out|base|inp)\.([A-Za-z_$][A-Za-z0-9_$]*)", expr) + \
            [("out", n) for n in re.findall(r"(?<![.\w$])([A-Za-z_$][A-Za-z0-9_$]*)\b", expr) if n in (out or {})]:
        if (src, name) in seen:
            continue
        seen.add((src, name))
        d = {"out": out, "base": base, "inp": inp}[src] or {}
        if name in d:
            v = d[name]
            depth = 0
            x = v
            while isinstance(x, list) and x:
                depth += 1; x = x[0]
            for m in re.finditer(r"\b" + re.escape(name) + r"((?:\[[^\]]*\])*)", expr):
                after = expr[m.end():].lstrip()
                before = expr[:m.start()].rstrip()
                if before.endswith((".", "out.", "base.", "inp.")):
                    before = re.sub(r"(out|base|inp)\.$", "", before).rstrip()
                direct = after[:1] in ("<", ">") or before[-1:] in ("<", ">")
                if depth > m.group(1).count("[") and direct and not after.startswith((".", "(")):
                    arrays = True  # an array used directly with < or > (string comparison)
            parts.append(f"{src}.{name}={json.dumps(_round(v))[:140]}")
    msg = "; ".join(parts[:5]) or "(no referenced values found)"
    if arrays and re.search(r"[<>]", expr):
        msg += ". NOTE: '<'/'>' on arrays compares them as strings; index down to numbers, e.g. out.W[0][0] < base.W[0][0]"
    return msg


# ---------------------------------------------------------------------------
# Checks 2-8 (need JS)
# ---------------------------------------------------------------------------
FORBIDDEN_JS = [r"\bfetch\s*\(", r"XMLHttpRequest", r"\bimport\s*\(", r"^\s*import\s", r"\bdocument\.", r"\bwindow\.",
                r"\beval\s*\(", r"\bnew\s+Function\b", r"localStorage", r"\brequire\s*\(", r"https?://"]

EQ_TOKENS = [  # (latex regex, js regex, name)
    (r"\\sqrt", r"Math\.sqrt|\*\*\s*\(?\s*0?\.5|Math\.pow\([^)]*,\s*0?\.5\s*\)|Math\.hypot", "square root"),
    (r"\\log|\\ln\b", r"Math\.log", "logarithm"),
    (r"\\exp|e\^\{|e\^|softmax", r"Math\.exp|Math\.E\b|Math\.pow\(\s*Math\.E", "exponential"),
    (r"\\sum|\\prod", r"\bfor\b|reduce|forEach|\.map\(|\bwhile\b", "sum/product"),
    (r"\\sin", r"Math\.sin", "sine"),
    (r"\\cos", r"Math\.cos", "cosine"),
]


class CheckRun:
    def __init__(self, spec, case):
        self.s = spec
        self.case = case
        self.failures = []
        self.stats = {}
        self.base = None
        self.sb = None

    def add(self, f):
        self.failures.append(f)

    # -- check 3: compute runs at defaults, finite, declared outputs well-shaped
    def c3_compute(self):
        s = self.s
        src = s.get("compute") or ""
        for pat in FORBIDDEN_JS:
            if re.search(pat, src, re.M):
                self.add(F("c3_compute", f"compute uses forbidden construct /{pat}/ (must be pure JS, no DOM/network)", ["compute"]))
        if not HAVE_QJS:
            self.add(F("c3_compute", "quickjs unavailable: compute not executed", ["compute"], "minor"))
            return False
        try:
            self.sb = Sandbox(src)
        except JSError as e:
            self.add(F("c3_compute", str(e), ["compute"])); return False
        inp = defaults(s)
        try:
            res = self.sb.run(inp)
        except JSError as e:
            self.add(F("c3_compute", f"compute(defaults) threw: {e}", ["compute"])); return False
        bad = nonfinite_paths(_declared(res, s))
        if bad:
            self.add(F("c3_compute", f"compute(defaults) produced NaN/Infinity/undefined: {bad[:4]}", ["compute"]))
        if res.get("warning"):
            declared_ok = not bad and all(isinstance(o, dict) and o.get("id") in res and res[o["id"]] is not None
                                          for o in s.get("outputs") or [])
            self.add(F("c3_compute", f"compute(defaults) returned warning '{str(res['warning'])[:80]}'; 'warning' is only for "
                       f"invalid input, put informational text in 'insight'", ["compute", "controls"],
                       "minor" if declared_ok else "major"))
        for o in s.get("outputs") or []:
            if not isinstance(o, dict) or "id" not in o:
                continue
            if o["id"] not in res:
                self.add(F("c3_compute", f"declared output '{o['id']}' not returned by compute (returned keys: {sorted(res)[:12]})", ["compute", "outputs"]))
                continue
            err = shape_error(o, res[o["id"]])
            if err:
                self.add(F("c3_compute", f"output '{o['id']}' ({o.get('type')}): {err}; got {json.dumps(res[o['id']])[:120]}", ["compute", "outputs"]))
        if not isinstance(res.get("insight"), str):
            self.add(F("c3_compute", "compute should return an 'insight' string", ["compute"], "minor"))
        self.base = _declared(res, s)
        self.stats["_base_values"] = self.base
        self.stats["default_outputs"] = {k: v for k, v in self.base.items() if _isnum(v)}
        return True

    # -- check 4: edge cases
    def c4_edges(self):
        s, n, errs = self.s, 0, []
        for c in s.get("controls") or []:
            if not isinstance(c, dict) or c.get("type") not in CONTROL_TYPES:
                continue
            for v in edge_inputs(c):
                inp = defaults(s); inp[c["id"]] = v; n += 1
                try:
                    res = self.sb.run(inp)
                except JSError as e:
                    errs.append(f"{c['id']}={json.dumps(v)[:60]} threw {e}"); continue
                bad = nonfinite_paths(_declared(res, s))
                if bad and not res.get("warning"):
                    errs.append(f"{c['id']}={json.dumps(v)[:60]} gave {bad[:2]} (return finite values or set 'warning')")
                elif not bad and not res.get("warning"):
                    for o in s.get("outputs") or []:
                        if isinstance(o, dict) and o.get("id") in res and res[o["id"]] is not None:
                            e2 = shape_error(o, res[o["id"]])
                            if e2:
                                errs.append(f"{c['id']}={json.dumps(v)[:60]} output '{o['id']}': {e2}"); break
        self.stats["edge_runs"] = n
        if errs:
            self.add(F("c4_edges", f"{len(errs)} edge-case failures, e.g. " + "; ".join(errs[:3]), ["compute"]))

    # -- check 5: every control changes an output
    def c5_sensitivity(self):
        s, dead = self.s, []
        for c in s.get("controls") or []:
            if not isinstance(c, dict) or c.get("type") not in CONTROL_TYPES:
                continue
            changed = False
            for v in perturbations(c):
                inp = defaults(s); inp[c["id"]] = v
                try:
                    res = _declared(self.sb.run(inp), s)
                except JSError:
                    continue
                if not _close(res, self.base):
                    changed = True; break
            if not changed and self._matters_with_others(c):
                changed = True
                self.stats.setdefault("interaction_controls", []).append(c["id"])
            if not changed:
                dead.append(c["id"])
        self.stats["dead_controls"] = dead
        if dead:
            self.add(F("c5_sensitivity", f"controls {dead} never change any declared output", ["controls", "compute"]))

    def _matters_with_others(self, c):
        """A control may only matter in combination (e.g. 'skew' only when mode='skewed'). Try it on top of
        other controls' perturbed values."""
        s = self.s
        for d in s.get("controls") or []:
            if not isinstance(d, dict) or d is c or d.get("type") not in CONTROL_TYPES:
                continue
            for pd in perturbations(d)[:4]:
                inp = defaults(s); inp[d["id"]] = pd
                try:
                    ref = _declared(self.sb.run(inp), s)
                except JSError:
                    continue
                for v in perturbations(c)[:4]:
                    inp2 = dict(inp); inp2[c["id"]] = v
                    try:
                        if not _close(_declared(self.sb.run(inp2), s), ref):
                            return True
                    except JSError:
                        continue
        return False

    def _assert(self, label, setobj, expr, fields):
        inp, errs = apply_set(self.s, setobj)
        if errs:
            return False, f"{label}: bad 'set': {errs[:2]}"
        try:
            res = self.sb.run(inp)
        except JSError as e:
            return False, f"{label}: compute threw {e}"
        out = _declared(res, self.s)
        ok, err = self.sb.check(expr, out, inp, self.base)
        if err:
            return False, f"{label}: expect '{expr}' error: {err}"
        if not ok:
            hint = ""
            if re.search(r"\bbase\b", expr):
                hint += f" (out = state after set {json.dumps(setobj)[:100]}; base = state at the defaults: check the direction of the comparison)"
            if re.search(r"[<>]\s*-?\d|approx\(", expr):
                hint += (" If the effect is real but weaker than asserted, make 'set' more extreme or relax the threshold/"
                         "tolerance to what the computed values can reach.")
            return False, f"{label}: assert '{expr}' is false; actual values: {_explain(expr, out, inp, self.base)}.{hint}"
        return True, None

    # -- check 6: self-tests
    def c6_selftests(self):
        res = []
        for i, t in enumerate(self.s.get("self_tests") or []):
            if not isinstance(t, dict):
                continue
            name = t.get("name") or f"test {i+1}"
            ok, err = self._assert(f"self_test '{name}'", t.get("set") or {}, t.get("expect"), ["self_tests"])
            res.append({"name": name, "pass": ok})
            if not ok:
                self.add(F("c6_selftests", err, ["self_tests", "compute"]))
        self.stats["self_tests"] = res

    # -- check 7: explorations really happen
    def c7_explorations(self):
        res = []
        for i, e in enumerate((self.s.get("explorations") or [])[:2]):
            if not isinstance(e, dict):
                continue
            label = f"exploration {i+1}"
            if not _nonempty_str(e.get("expect")):
                self.add(F("c7_explorations", f"{label}: add an 'assert' JS boolean expression", ["explorations"], "minor"))
                res.append({"i": i + 1, "pass": None}); continue
            same = False
            inp, errs = apply_set(self.s, e.get("set") or {})
            if not errs:
                try:
                    same = _close(_declared(self.sb.run(inp), self.s), self.base)
                except JSError:
                    pass
            if same and not getattr(self, "_same_used", False):
                ok0, _ = self._assert(label, e.get("set") or {}, e["expect"], ["explorations"])
                if ok0:  # a single "look at the starting point" exploration is acceptable teaching
                    self._same_used = True
                    self.add(F("c7_explorations", f"{label}: 'set' equals the defaults (observes the starting state)",
                               ["explorations"], "minor"))
                    res.append({"i": i + 1, "pass": True}); continue
            if same:
                cur = {k: defaults(self.s).get(k) for k in (e.get("set") or {})}
                self.add(F("c7_explorations", f"{label}: its 'set' {json.dumps(e.get('set'))[:120]} produces exactly the "
                           f"default state (defaults: {json.dumps(cur)[:120]}), so the learner sees no change. Choose set "
                           f"values that differ from the defaults and still show the effect, and keep the assert consistent.",
                           ["explorations"]))
                res.append({"i": i + 1, "pass": False}); continue
            ok, err = self._assert(label, e.get("set") or {}, e["expect"], ["explorations"])
            res.append({"i": i + 1, "pass": ok})
            if not ok:
                self.add(F("c7_explorations", err, ["explorations"]))
        self.stats["explorations"] = res

    # -- check 8: equation vs code
    def c8_equation(self):
        s = self.s
        eq = ((s.get("equation") or {}).get("latex") or "") + " " + " ".join(s.get("steps") or [])
        src = s.get("compute") or ""
        for lat, js, name in EQ_TOKENS:
            if re.search(lat, eq) and not re.search(js, src):
                self.add(F("c8_equation", f"equation uses {name} but compute has no matching operation", ["compute", "equation"], "minor"))
        outs = {o.get("id"): o.get("type") for o in s.get("outputs") or [] if isinstance(o, dict)}
        ctl = {c.get("id"): c.get("type") for c in s.get("controls") or [] if isinstance(c, dict)}
        for st in s.get("steps") or []:
            for ph in re.findall(r"\{\{\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\}\}", st):
                if not (outs.get(ph) in ("scalar", "vector") or ctl.get(ph) in ("slider", "toggle", "select", "vector")):
                    self.add(F("c8_equation", f"step placeholder {{{{{ph}}}}} is not a scalar output or slider/toggle/select", ["steps"], "minor"))

    # -- check 2: outcomes from focus covered
    def c2_outcomes(self):
        s = self.s
        oc = s.get("outcomes")
        if not isinstance(oc, list) or not oc:
            self.add(F("c2_outcomes", "'outcomes' missing: restate each learning outcome in focus and what covers it", ["outcomes"])); return
        ex_n = len(s.get("explorations") or [])
        valid = {f"output:{o.get('id')}" for o in s.get("outputs") or [] if isinstance(o, dict)}
        valid |= {f"control:{c.get('id')}" for c in s.get("controls") or [] if isinstance(c, dict)}
        valid |= {f"exploration:{i+1}" for i in range(ex_n)}
        valid |= {"compute", "steps", "equation", "symbols", "self_tests", "explorations", "limitation", "misconception"}
        for o in oc:
            if not isinstance(o, dict):
                continue
            refs = o.get("covered_by") or []
            if not refs:
                self.add(F("c2_outcomes", f"outcome '{str(o.get('outcome'))[:60]}' is not covered by anything", ["outcomes"]))
            bad = [r for r in refs if r not in valid and str(r).split(":")[0] not in valid]
            if bad:
                self.add(F("c2_outcomes", f"outcome refs {bad[:3]} do not exist", ["outcomes"], "minor"))
        # keyword coverage of the focus text
        focus = self.case.get("focus", "").lower()
        words = {w for w in re.findall(r"[a-z][a-z\-]{4,}", focus)} - STOP
        blob = json.dumps({k: v for k, v in s.items() if not k.startswith("_")}).lower()
        hit = [w for w in words if w[:6] in blob]
        cov = len(hit) / max(1, len(words))
        self.stats["focus_keyword_coverage"] = round(cov, 2)
        if words and cov < 0.5:
            missing = sorted(words - set(hit))[:8]
            self.add(F("c2_outcomes", f"focus terms not addressed: {missing}", ["outcomes", "explorations"], "minor"))

    def run(self):
        self.failures += check_schema(self.s)
        self.c2_outcomes()
        js_ok = self.c3_compute()
        if js_ok and self.base is not None:
            self.c4_edges()
            self.c5_sensitivity()
            self.c6_selftests()
            self.c7_explorations()
        self.c8_equation()
        self.failures += check_latex(self.s)
        return self


STOP = set("""about above after again against because before being below between could doing during each
further having other should their there these those through under until where which while would learner
learners should understand explain section students student their using value values small given show shows
first second third whether paper excerpt concept outcomes required""".split())


# ---------------------------------------------------------------------------
# Check 9: LaTeX converts + page safety
# ---------------------------------------------------------------------------
def latex_fields(s):
    out = []
    eq = s.get("equation")
    if isinstance(eq, dict) and isinstance(eq.get("latex"), str):
        out.append(("equation", eq["latex"]))
    for sym in s.get("symbols") or []:
        if isinstance(sym, dict) and isinstance(sym.get("symbol"), str):
            out.append(("symbols", sym["symbol"]))
    for st in s.get("steps") or []:
        out.append(("steps", re.sub(r"\{\{\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\}\}", "0", st)))
    return out


def check_latex(s):
    try:
        from latex2mathml.converter import convert
    except Exception:
        return [F("c9_latex", "latex2mathml unavailable", [], "minor")]
    fl = []
    for field, tex in latex_fields(s):
        try:
            convert(tex)
        except Exception as e:  # noqa: BLE001
            fl.append(F("c9_latex", f"LaTeX in {field} does not convert ({type(e).__name__}): {tex[:80]}", [field], "minor"))
    return fl


EXTERNAL_LOAD = [
    (r"<script[^>]+\bsrc\s*=", "external script tag"),
    (r"<link[^>]+\bhref\s*=\s*[\"']?https?:", "external stylesheet/link"),
    (r"<(img|iframe|video|audio|source|embed|object)[^>]+\b(src|data)\s*=\s*[\"']?(https?:)?//", "remote media"),
    (r"url\(\s*[\"']?(https?:)?//", "remote CSS url()"),
    (r"@import", "CSS @import"),
    (r"\bfetch\s*\(", "fetch()"),
    (r"XMLHttpRequest|WebSocket|EventSource", "network API"),
    (r"sk-or-[A-Za-z0-9]", "API key pattern"),
]


def check_page(html, case=None):
    """Check 9 on the rendered HTML. Returns failure list (major = unsafe)."""
    fl = []
    if not isinstance(html, str) or len(html) < 200:
        return [F("c9_page", "rendered page is empty or tiny", ["render"])]
    low = html.lower()
    for pat, name in EXTERNAL_LOAD:
        if re.search(pat, html, re.I):
            fl.append(F("c9_page", f"page contains {name}", ["render"]))
    import os
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and key in html:
        fl.append(F("c9_page", "API key leaked into page", ["render"]))
    # every <script> opened must be closed exactly once -> detects unescaped </script> in embedded JSON
    if low.count("<script") != low.count("</script>"):
        fl.append(F("c9_page", "unbalanced <script> tags (unescaped </script> inside embedded data?)", ["render"]))
    if "<math" not in low and "\\u003cmath" not in low:
        fl.append(F("c9_page", "no MathML found: equations not converted", ["render"], "minor"))
    if "<!doctype html" not in low[:200]:
        fl.append(F("c9_page", "missing <!doctype html>", ["render"], "minor"))
    return fl


# ---------------------------------------------------------------------------
def run_checks(spec, case):
    """Return report dict. spec should already be normalized."""
    cr = CheckRun(spec, case).run()
    majors = [f for f in cr.failures if f["severity"] == "major"]
    return {
        "pass": not majors,
        "n_major": len(majors),
        "n_minor": len(cr.failures) - len(majors),
        "failures": cr.failures,
        "stats": cr.stats,
        "usable": cr.base is not None and bool(cr.base),
    }
