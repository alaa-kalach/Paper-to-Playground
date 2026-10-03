"""Offline regression tests for the agent (no API key needed).  python tests/run_tests.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.checks import normalize, run_checks  # noqa: E402

M = "tests/mock/"
SCENARIOS = [  # (name, mock files, case, expected exit, expected page kind prefix)
    ("entropy passes", "specs/entropy.json", "cases/example_b.json", 0, "verified"),
    ("attention passes", "specs/attention.json", "cases/example_a.json", 0, "verified"),
    ("broken -> repaired", M + "broken.txt," + M + "patch_ok.json", "cases/example_b.json", 0, "verified"),
    ("repair fails -> partial", M + "broken.txt," + M + "garbage.txt," + M + "garbage.txt", "cases/example_b.json", 0, "partial"),
    ("garbage -> emergency ok", M + "garbage.txt," + M + "garbage.txt,specs/entropy.json", "cases/example_b.json", 0, "emergency"),
    ("bare names + dead control", M + "live_like.json", "cases/example_b.json", 0, "verified"),
    ("control matters only w/ mode", M + "interaction.json", "cases/example_b.json", 0, "verified"),
    ("bool scalar + default expl.", M + "batch2.json", "cases/example_b.json", 0, "verified"),
    ("all garbage -> static", ",".join([M + "garbage.txt"] * 4), "cases/example_b.json", 1, "static"),
]


def run(name, mock, case, want_code, want_kind):
    out = Path(tempfile.mkdtemp())
    env = dict(os.environ, P2P_MOCK=mock)
    r = subprocess.run([sys.executable, "agent.py", "--input", case, "--output", str(out), "--model", "mock"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    ev = [json.loads(l) for l in (out / "trace.jsonl").read_text().splitlines()]
    kinds = [e.get("kind", "") for e in ev if e["action"] == "write_index_html"]
    ok = r.returncode == want_code and (out / "index.html").exists() and kinds and kinds[-1].startswith(want_kind)
    ok = ok and all(k in e for e in ev for k in ("stage", "action", "result"))
    print(f"{'PASS' if ok else 'FAIL'}  {name:28s} exit={r.returncode} kind={kinds[-1] if kinds else None}")
    if not ok:
        print(r.stderr[-800:])
    return ok


def spec_checks():
    ok = True
    for spec, case in (("specs/entropy.json", "cases/example_b.json"), ("specs/attention.json", "cases/example_a.json")):
        s, _ = normalize(json.load(open(ROOT / spec)), json.load(open(ROOT / case)))
        r = run_checks(s, json.load(open(ROOT / case)))
        print(f"{'PASS' if r['pass'] else 'FAIL'}  checks {spec}  majors={r['n_major']} minors={r['n_minor']}")
        ok &= r["pass"]
    return ok


if __name__ == "__main__":
    results = [spec_checks()] + [run(*s) for s in SCENARIOS]
    sys.exit(0 if all(results) else 1)
