import json, glob, quickjs

HELP = "function approx(a,b,t){if(t===undefined)t=1e-6;return Math.abs(a-b)<=t}"

def variants(c):
    t, d = c["type"], c.get("default")
    lo, hi = c.get("min", 0), c.get("max", 1)
    if t == "slider":
        return [lo, hi]
    if t == "toggle":
        return [True, False]
    if t == "select":
        return [o["value"] for o in c.get("options", [])]
    if t == "vector":
        return [[0] * len(d), [lo] * len(d), [hi] * len(d)]
    if t == "matrix":
        return [[[0] * len(r) for r in d], [[lo] * len(r) for r in d], [[hi] * len(r) for r in d]]
    return []

for f in sorted(glob.glob("out_specs/*.json")):
    s = json.load(open(f))
    ctx = quickjs.Context(); ctx.eval(HELP); ctx.eval(s["compute"])
    base = {c["id"]: c.get("default") for c in s["controls"]}
    problems = []
    for c in s["controls"]:
        for v in variants(c):
            inp = {**base, c["id"]: v}
            try:
                out = ctx.eval("JSON.stringify(compute(%s))" % json.dumps(inp))
                if "null" in out or "NaN" in out or "Infinity" in out:
                    if '"warning"' not in out:
                        problems.append(f"{c['id']}={v}: bad value, no warning")
            except Exception as e:
                problems.append(f"{c['id']}={v}: CRASH {str(e)[:60]}")
    if "Math.random" in s["compute"]:
        problems.append("uses Math.random (not deterministic)")
    print(("OK  " if not problems else "FAIL"), f, *problems[:4], sep="\n   " if problems else " ")