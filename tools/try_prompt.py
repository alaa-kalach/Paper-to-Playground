import json, os, sys, time, requests

case_path, model = sys.argv[1], sys.argv[2]
case = json.load(open(case_path, encoding="utf-8"))
system = open("prompts/spec_system.txt", encoding="utf-8").read()

t = time.time()
r = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
    json={
        "model": model,
        "temperature": 0.2,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
        "reasoning": {"enabled": False},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(case)},
        ],
    },
    timeout=120,
)
if r.status_code != 200:
    print("API error:", r.status_code, r.text[:500])
    sys.exit(1)

data = r.json()
print("seconds:", round(time.time() - t, 1))
print("usage:", data.get("usage"))

text = data["choices"][0]["message"]["content"].strip()
if text.startswith("```"):
    text = text.strip("`").removeprefix("json").strip()
spec = json.loads(text)

os.makedirs("out_specs", exist_ok=True)
name = os.path.basename(case_path)
json.dump(spec, open(f"out_specs/{name}", "w", encoding="utf-8"), indent=2)
print("saved out_specs/" + name)

import quickjs
ctx = quickjs.Context()
ctx.eval("""
function approx(a,b,t){ if(t===undefined) t=1e-6; return Math.abs(a-b)<=t; }
function sum(a){ return a.reduce(function(s,v){return s+v;},0); }
function max(a){ return Math.max.apply(null,a); }
function min(a){ return Math.min.apply(null,a); }
function all(a,f){ return a.every(f); }
""")
ctx.eval(spec["compute"])
defaults = {c["id"]: c.get("default") for c in spec["controls"]}

def run(over, expr):
    inp = {**defaults, **over}
    js = ("(function(){var inp=%s; var out=compute(inp); var base=compute(%s); return (%s);})()"
          % (json.dumps(inp), json.dumps(defaults), expr))
    return ctx.eval(js)

print("default output:", run({}, "JSON.stringify(out)")[:300])
returned = json.loads(run({}, "JSON.stringify(Object.keys(out))"))
for o in spec["outputs"]:
    print("output", o["id"], "returned:", o["id"] in returned)
for e in spec["explorations"]:
    print("exploration", repr(e["title"]), "->", run(e["set"], e["expect"]))
for s in spec["self_tests"]:
    print("selftest", repr(s["name"]), "->", run(s["set"], s["expect"]))