"""Summarize a run:  python tests/trace_summary.py out/trace.jsonl"""
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1] if len(sys.argv) > 1 else "out/trace.jsonl", encoding="utf-8")]
for e in ev:
    if e["action"] == "llm_call":
        print(f"CALL {e['stage']:9s} {e['result']:5s} prompt={e.get('prompt_tokens')} completion={e.get('completion_tokens')} "
              f"reasoning={e.get('reasoning_tokens')} {e.get('elapsed_s')}s finish={e.get('finish_reason')} {e.get('error','')}")
    elif e["action"].startswith("check:") and e["result"] != "pass":
        print(f"  {e['action']:22s} {e['result']}: {e.get('failures')}")
    elif e["action"] in ("checks_summary", "revision", "sanitize", "write_index_html", "attempt_failed", "skip", "exception", "timeout"):
        print(f"{e['stage']}/{e['action']}: {e['result']} " + json.dumps({k: v for k, v in e.items() if k in
              ('n_major', 'n_minor', 'majors_before', 'majors_after', 'notes', 'kind', 'error', 'reason')}))
end = [e for e in ev if e["action"] == "exit"]
if end:
    t = end[-1]["totals"]
    print(f"\nEXIT {end[-1]['exit_code']} | calls={t['calls']} total_tokens={t['total_tokens']} "
          f"(prompt {t['prompt_tokens']}, completion {t['completion_tokens']}, reasoning {t['reasoning_tokens']}) | {t['elapsed_s']}s")
