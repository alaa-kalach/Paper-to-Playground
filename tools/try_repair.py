import json, os, sys, time, requests, quickjs

spec_path, model = sys.argv[1], sys.argv[2]
spec = json.load(open(spec_path, encoding="utf-8"))
repair_prompt = open("prompts/repair.txt", encoding="utf-8").read()

HELPERS = """
function approx(a,b,t){ if(t===undefined) t=1e-6; return Math.abs(a-b)<=t; }
function sum(a){ return a.reduce(function(s,v){return s+v;},0); }
function max(a){ return Math.max.apply(null,a); }
function min(a){ return Math.min.apply(null,a); }
function all(a,f){ return a.every(f); }
"""

def check(spec):
    ctx = quickjs.Context()
    ctx.eval(HELPERS)
    ctx.eval(spec["compute"])
    defaults = {c["id"]: c.get("default") for c in spec["controls"]}
    errors = []
    for kind in ["explorations", "self_tests"]:
        for i, t in enumerate(spec[kind]):
            inp = {**defaults, **t["set"]}
            js = ("(function(){var inp=%s; var out=compute(inp); var base=compute(%s);"
                  "return JSON.stringify({ok: !!(%s), out: out});})()"
                  % (json.dumps(inp), json.dumps(defaults), t["expect"]))
            try:
                r = json.loads(ctx.eval(js))
                if not r["ok"]:
                    errors.append(f'{kind}[{i}] "{t.get("title", t.get("name"))}" failed. '
                                  f'set={json.dumps(t["set"])} expect={t["expect"]} '
                                  f'real outputs={json.dumps(r["out"])[:500]}')
            except Exception as e:
                errors.append(f'{kind}[{i}] crashed: {e}')
    return errors

errors = check(spec)
print("errors before:", len(errors))
for e in errors: print(" -", e[:200])
if not errors:
    sys.exit(0)

failing = {k: spec[k] for k in ["controls", "explorations", "self_tests", "compute"]}
user_msg = json.dumps({"failing_fields": failing, "errors": errors})

t = time.time()
r = requests.post("https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
    json={"model": model, "temperature": 0.2, "max_tokens": 3000,
          "response_format": {"type": "json_object"},
          "reasoning": {"enabled": False},
          "messages": [{"role": "system", "content": repair_prompt},
                       {"role": "user", "content": user_msg}]},
    timeout=120)
data = r.json()
print("seconds:", round(time.time() - t, 1), "| usage:", data["usage"]["total_tokens"], "tokens")

text = data["choices"][0]["message"]["content"].strip()
if text.startswith("```"):
    text = text.strip("`").removeprefix("json").strip()
patch = json.loads(text)
print("AI changed fields:", list(patch.keys()))
spec.update(patch)

errors = check(spec)
print("errors after:", len(errors))
for e in errors: print(" -", e[:200])