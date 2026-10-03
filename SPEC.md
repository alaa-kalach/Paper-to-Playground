# Spec format (frozen contract between A / B / C)

The model returns ONE JSON object. Python (A) checks it, B's `template/render.py` turns it into `index.html`.
Reference examples: `specs/entropy.json`, `specs/attention.json`.

```jsonc
{
  "title": "Discrete entropy",
  "paper": {"title": "...", "authors": "...|null", "year": 1948, "url": "<source_url>",
            "section": "Section 6|null", "equation": "Eq. (1)|null"},       // null when unsure
  "hook": "1-3 sentences: the idea and why it matters",
  "symbols": [{"symbol": "p_i", "meaning": "...", "source": "paper|ours"}],  // symbol = LaTeX
  "equation": {"latex": "H = -\\sum_i p_i \\log_2 p_i", "source": "paper|ours", "caption": "..."},

  "controls": [   // >= 2. id = JS identifier. Every control must change some output.
    {"id": "n",  "type": "slider", "label": "...", "min": 1, "max": 8, "step": 1, "default": 4, "symbol": "n"},
    {"id": "sc", "type": "toggle", "label": "...", "default": true},
    {"id": "m",  "type": "select", "label": "...", "options": [{"value": "a", "label": "A"}], "default": "a"},
    {"id": "p",  "type": "vector", "label": "...", "length": 4, "min": 0, "max": 1, "step": 0.05,
     "default": [0.25, 0.25, 0.25, 0.25], "labels": ["x1","x2","x3","x4"]},
    {"id": "Q",  "type": "matrix", "label": "...", "rows": 2, "cols": 3, "min": -3, "max": 3, "step": 0.5,
     "default": [[1,0,1],[0,1,0]], "row_labels": null, "col_labels": null}
  ],

  // Plain JS (no DOM, no fetch, deterministic). inputs = {controlId: value}
  // slider -> number, toggle -> boolean, select -> string, vector -> number[], matrix -> number[][]
  // Returns {<outputId>: value, ..., "insight": "one live sentence", "warning": "optional, for invalid input"}
  "compute": "function compute(inputs){ ... return {H: h, contrib: c, insight: '...'}; }",

  "outputs": [   // declares how to draw each returned value
    {"id": "H",       "type": "scalar", "label": "Entropy", "unit": "bits"},             // number
    {"id": "contrib", "type": "vector", "label": "...", "labels": ["x1","x2"]},          // number[]
    {"id": "W",       "type": "matrix", "label": "...", "row_labels": null, "col_labels": null}, // number[][]
    {"id": "curve",   "type": "series", "label": "...", "x_label": "...", "y_label": "..."},
          // value: {"x": number[], "lines": [{"name": "...", "y": number[]}], "marker": {"x": n, "y": n}?}
    {"id": "tab",     "type": "table",  "label": "..."},   // value: {"columns": [...], "rows": [[...]]}
    {"id": "fl",      "type": "flow",   "label": "..."}    // value: {"nodes":[{"id","label","value"?}], "edges":[{"from","to","label"?,"value"?}]}
  ],

  // LaTeX lines; {{id}} is replaced live by the current value of a scalar output or slider/toggle/select input
  "steps": ["H = -\\sum_i p_i\\log_2 p_i = {{H}}\\ \\text{bits}"],

  // "assert" (alias "expect") = JS boolean expressions over: out (outputs), inp (inputs), base (outputs at defaults)
  // helpers available: approx(a,b,tol=1e-6), sum(arr), max(arr), min(arr), all(arr, fn)
  "explorations": [   // exactly 2
    {"title": "...", "predict": "question to the learner", "set": {"p": [1,0,0,0]},
     "observe": "...", "why": "...", "assert": "approx(out.H, 0)"}
  ],
  "self_tests": [ {"name": "4 equal outcomes give 2 bits", "set": {"p": [0.25,0.25,0.25,0.25]}, "assert": "approx(out.H, 2)"} ],

  "limitation": "...",
  "misconception": "...",
  "claims": [{"text": "...", "source": "paper|ours"}],          // no invented quotes
  "outcomes": [{"outcome": "restated learning outcome from focus", "covered_by": ["output:H", "exploration:1", "control:p"]}]
}
```

Rules
- `set` objects only name control ids; unspecified controls stay at default. Values must respect control type/length/range.
- `compute` must handle edge cases (all zeros, min/max) without throwing: return finite numbers, or set `warning` and return nulls.
- No external URLs except `paper.url` (rendered as a plain link).
- Renderer interface (B): `template/render.py: render(spec: dict) -> str` (full HTML). Optional: `render_report` dict
  attribute is not required. If missing, A falls back to `core/render_stub.py`.
