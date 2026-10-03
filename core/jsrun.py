"""Run spec.compute() and assertion expressions inside a sandboxed QuickJS context."""
import json
import re
import time

IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

try:
    import quickjs  # type: ignore
    HAVE_QJS = True
except Exception:  # pragma: no cover
    quickjs = None
    HAVE_QJS = False

PRELUDE = r"""
var approx = function(a, b, tol){ if (tol === undefined) tol = 1e-6;
  if (typeof a !== 'number' || typeof b !== 'number') return a === b;
  return Math.abs(a - b) <= tol * Math.max(1, Math.abs(a), Math.abs(b)); };
var sum = function(a){ var s = 0; for (var i = 0; i < a.length; i++) s += a[i]; return s; };
var max = function(a){ return Math.max.apply(null, a); };
var min = function(a){ return Math.min.apply(null, a); };
var all = function(a, f){ for (var i = 0; i < a.length; i++) if (!f(a[i], i)) return false; return true; };
var __seed = 12345;
Math.random = function(){ __seed = (__seed * 1103515245 + 12345) % 2147483648; return __seed / 2147483648; };
var __ser = function(v){ return JSON.stringify(v, function(k, x){
  if (typeof x === 'number' && !isFinite(x)) return {__nonfinite: String(x)};
  if (x === undefined) return {__undefined: true};
  return x; }); };
var __clone = function(o){ return JSON.parse(JSON.stringify(o)); };
"""


class JSError(Exception):
    pass


class Sandbox:
    def __init__(self, compute_src, time_limit=1, mem_mb=64, total_s=15):
        if not HAVE_QJS:
            raise JSError("quickjs not installed")
        self.total_s, self.used_s = total_s, 0.0
        self.ctx = quickjs.Context()
        self.ctx.set_memory_limit(mem_mb * 1024 * 1024)
        self.ctx.set_time_limit(time_limit)
        self.ctx.set_max_stack_size(1024 * 1024)
        self.ctx.eval(PRELUDE)
        src = compute_src if isinstance(compute_src, str) else ""
        try:
            self.ctx.eval(src + "\n;var __compute = (typeof compute === 'function') ? compute : null;")
        except Exception as e:
            raise JSError(f"compute source error: {_msg(e)}")
        if not self.ctx.eval("__compute !== null"):
            raise JSError("compute source does not define function compute(inputs)")

    def run(self, inputs):
        """Return the compute() result as a Python object (non-finite numbers marked)."""
        if self.used_s > self.total_s:
            raise JSError(f"check time budget ({self.total_s}s) exceeded: compute is too slow")
        js = f"__ser(__compute(__clone({json.dumps(inputs)})))"
        t = time.monotonic()
        try:
            out = self.ctx.eval(js)
        except Exception as e:
            m = _msg(e)
            raise JSError(m + (" (compute exceeded 1s; make it faster)" if "interrupted" in m else ""))
        finally:
            self.used_s += time.monotonic() - t
        if out is None:
            raise JSError("compute returned undefined")
        res = json.loads(out)
        if not isinstance(res, dict):
            raise JSError(f"compute must return an object, got {type(res).__name__}")
        return res

    def check(self, expr, out, inp, base):
        """Evaluate a boolean JS assertion expression. Returns (bool, error_or_None)."""
        if not isinstance(expr, str) or not expr.strip():
            return False, "empty expression"
        body = wrap_expect(expr, list(out or {}), list(inp or {}))
        js = ("(function(out, inp, base){ return !!(" + body + "); })("
              + json.dumps(out) + "," + json.dumps(inp) + "," + json.dumps(base) + ")")
        try:
            return bool(self.ctx.eval(js)), None
        except Exception as e:
            m = _msg(e)
            if "SyntaxError" in m:
                m += " (expect must be a JS boolean expression, e.g. approx(out.H, 0) or out.W[0][0] > 0.9, not English)"
            return False, m


_SIMPLE_EQ = re.compile(r"^\s*([^=!<>&|?]+?)\s*={1,3}\s*([^=!<>&|?]+?)\s*$")


_RESERVED = {"out", "inp", "base", "approx", "sum", "max", "min", "all", "Math", "true", "false", "null",
             "inputs", "o", "x", "undefined", "NaN", "Infinity"}


def wrap_expect(expr, out_keys, inp_keys):
    """Canonical, self-contained assertion usable both in QuickJS and in the page's evaluator
    (which provides out, inp, base, approx, sum, max, min, all). Bare names like H become out["H"]."""
    e = normalize_expect(expr)
    decl, seen = "", set()
    for src, keys in (("out", out_keys), ("inp", inp_keys)):
        for k in keys:
            if IDENT_RE.match(k) and k not in _RESERVED and k not in seen and re.search(r"(?<![.\w$])" + re.escape(k) + r"\b", e):
                seen.add(k)
                decl += f"var {k} = {src}[{json.dumps(k)}]; "
    if not decl:
        return e
    return "(function(){ " + decl + "return (" + e + "); })()"


def normalize_expect(expr):
    """Models write 'H = 0' (assignment!) or 'H == 2' (exact float compare). Rewrite simple equalities
    to approx(a, b) and stray single '=' to '=='."""
    e = expr.strip().rstrip(";")
    m = _SIMPLE_EQ.match(e)
    if m:
        return f"approx({m.group(1)}, {m.group(2)})"
    return re.sub(r"(?<![=!<>])=(?![=>])", "==", e)


def _msg(e):
    s = str(e).strip().splitlines()
    return (s[0] if s else type(e).__name__)[:300]


def nonfinite_paths(v, path="out"):
    """List paths of NaN/Infinity/undefined values inside a compute result."""
    bad = []
    if isinstance(v, dict):
        if "__nonfinite" in v:
            return [f"{path}={v['__nonfinite']}"]
        if "__undefined" in v:
            return [f"{path}=undefined"]
        for k, x in v.items():
            bad += nonfinite_paths(x, f"{path}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            bad += nonfinite_paths(x, f"{path}[{i}]")
    return bad
