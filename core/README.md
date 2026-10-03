# core/ (Person A) — agent loop, budget, trace, checks, repair, fallback

Flow (`core/pipeline.py`): read case → 1 generate call → normalize (free auto-fixes) → checks 1–8 in QuickJS →
repair only on MAJOR failures (≤2, sends only failing fields + errors) → sanitize → render (B's
`template/render.py: render(spec) -> str`, falls back to `core/render_stub.py`) → check 9 on HTML → write.

Fallback ladder: verified spec → partial (failing self-tests dropped, failing explorations flagged
`verified:false`, broken outputs hidden) → one emergency call for a minimal spec → static page (exit 1).
A watchdog thread writes a page and exits before the 10-minute hard limit.

Exit codes: 0 interactive page written · 1 only static page · 2 invalid case.json.

What B gets in the spec: everything in SPEC.md plus `spec["_verification"]`
(`summary`, `self_tests`, `explorations`, `passed`, `open_issues`) for badges, and `explorations[i].verified`.
Tip for B: guard NaN/null in the page (partial specs may still produce them on odd inputs).

Env knobs (defaults in brackets): P2P_MAX_CALLS [5], P2P_MAX_COMPLETION [14000], P2P_DEADLINE_S [420],
P2P_REASONING [off|low|omit], P2P_JSON_MODE [1], P2P_TEMPERATURE [0.2], P2P_MOCK (comma-separated reply files, offline).

Tests (offline): `python tests/run_tests.py`
