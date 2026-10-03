import json, os, sys, time, requests

case_path, model = sys.argv[1], sys.argv[2]
case = json.load(open(case_path, encoding="utf-8"))
system = open("prompts/system.txt", encoding="utf-8").read()

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
ctx.eval(spec["compute"])
defaults = {c["id"]: c.get("default") for c in spec["controls"]}

def run(over, expr):
    x = {**defaults, **over}
    return ctx.eval(f"(function(){{var o=compute({json.dumps(x)}); return ({expr});}})()")

print("default output:", run({}, "JSON.stringify(o)")[:300])
for e in spec["explorations"]:
    print("exploration", repr(e["title"]), "->", run(e["set"], e["expect"]))
for s in spec["selftests"]:
    print("selftest", repr(s["why"]), "->", run(s["set"], s["assert"]))