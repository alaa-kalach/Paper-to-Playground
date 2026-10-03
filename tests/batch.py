"""Run every case N times against the live model and print a results table.

    python tests/batch.py --model deepseek/deepseek-v4.1-flash [--runs 2] [--cases cases cases/practice]
Outputs go to runs/<case>_<i>/ ; table also saved to runs/results.csv
"""
import argparse, csv, json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def summarize(out):
    ev = [json.loads(l) for l in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    end = [e for e in ev if e["action"] == "exit"]
    kind = [e.get("kind", "") for e in ev if e["action"] == "write_index_html"]
    fails = sorted({e["action"].split(":")[1] for e in ev if e["action"].startswith("check:c") and e["result"] == "fail"})
    t = end[-1]["totals"] if end else {}
    return {"exit": end[-1]["exit_code"] if end else "?", "kind": kind[-1] if kind else "none",
            "calls": t.get("calls"), "tokens": t.get("total_tokens"), "reasoning": t.get("reasoning_tokens"),
            "seconds": t.get("elapsed_s"), "failed_checks": " ".join(fails)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--cases", nargs="+", default=["cases"])
    a = ap.parse_args()
    files = sorted(p for d in a.cases for p in (ROOT / d).glob("*.json"))
    rows = []
    for f in files:
        for i in range(1, a.runs + 1):
            out = ROOT / "runs" / f"{f.stem}_{i}"
            t = time.time()
            subprocess.run([sys.executable, "agent.py", "--input", str(f), "--output", str(out), "--model", a.model],
                           cwd=ROOT, timeout=660)
            r = {"case": f.stem, "run": i, **summarize(out), "wall_s": round(time.time() - t, 1)}
            rows.append(r)
            print(f"{r['case']:22s} #{i} exit={r['exit']} {r['kind']:18s} calls={r['calls']} tokens={r['tokens']} "
                  f"{r['seconds']}s  {r['failed_checks']}", flush=True)
    with open(ROOT / "runs" / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    ok = [r for r in rows if r["exit"] == 0]
    ver = [r for r in rows if str(r["kind"]).startswith("verified")]
    toks = [r["tokens"] for r in ok if r["tokens"]]
    print(f"\n{len(ok)}/{len(rows)} exit 0 | {len(ver)}/{len(rows)} verified | "
          f"avg tokens {sum(toks)/max(1,len(toks)):.0f} | max seconds {max(r['seconds'] or 0 for r in rows)}")


if __name__ == "__main__":
    main()
