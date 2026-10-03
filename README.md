# Paper to Playground

An agent that turns a focused research-paper brief into a self-contained, interactive
teaching page (`index.html`) for engineering undergraduates. The learner changes inputs,
sees every number recomputed live, and works through guided exercises. All numbers come
from executable code, never from the model's prose.

## Team

- Alaa Kalach
- Leen Itani
- Nanar Aintablian

## Model

```
MODEL_ID = deepseek/deepseek-v4.1-flash
```

All model calls go through OpenRouter (`https://openrouter.ai/api/v1/chat/completions`) and
read the key from `OPENROUTER_API_KEY`. No key is stored in the repository or the page.

## Setup and run

Python 3.11. No system packages, GPU or server are needed.

```
python -m pip install -r requirements.txt
export OPENROUTER_API_KEY="..."
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

`case.json` has three fields: `source_url`, `focus`, `audience`.

Outputs:
- `out/index.html`: one self-contained file (inline CSS, JS and SVG, equations as MathML). It needs no internet, CDN or fonts, and works opened as a file or served locally.
- `out/trace.jsonl`: one JSON event per line, with the stage, action and result. It records per-call prompt, completion, cached and reasoning tokens, the OpenRouter `generation_id`, elapsed seconds, every check, failures and revisions. The API key and hidden reasoning are never logged.

Exit codes: `0` interactive page written; `1` only a static fallback page; `2` invalid `case.json`.

## Architecture

```
case.json ──► 1 LLM call ──► JSON spec ──► checks in QuickJS ──► (repair ≤2) ──► template ──► index.html
                                                     │                                  │
                                                     └──────────── trace.jsonl ◄────────┘
```

1. **Generate (1 call).** The model writes a compact JSON spec, not HTML (format in `SPEC.md`). The spec holds:
   - the explanation: hook, context, intuition, symbols, equation and key takeaways
   - 2 or more controls and a plain JavaScript `compute(inputs)` function
   - typed outputs: scalar, vector, matrix, series, table or flow
   - worked steps with live `{{values}}`
   - two guided explorations, each with an executable `expect`
   - self-tests, a limitation, a misconception, and claims tagged `paper` or `ours`
2. **Check (`core/checks.py`).** `compute()` runs in a QuickJS sandbox. The checks cover:
   - schema
   - every learning outcome in the focus being covered
   - no NaN or Infinity
   - edge cases (min, max, zero, all-equal)
   - every control changing an output
   - self-tests
   - whether each exploration's promised result actually happens
   - LaTeX validity
3. **Repair.** Only on major failures: the failing fields plus the real computed values go back to the model. At most 2 repairs.
4. **Render (`template/render.py`).** A generic, topic-independent template turns the spec into the page:
   - LaTeX is converted to MathML.
   - Outputs are drawn by type: bar chart, heatmap with row sums, line plot, table or flow diagram.
   - Long sample vectors are drawn as line plots.
   - The page safety check (check 9) is run on the result.
5. **Fallback ladder.** The first rung that works is used:
   1. the verified spec
   2. a partial spec, with failing tests dropped and failing explorations flagged
   3. one emergency minimal spec
   4. a static page (exit 1)

   A watchdog guarantees a page before the 10-minute limit.

**Page layout** (lecture-handout style):
- **Front matter:** abstract; what you will learn (each outcome mapped to the control, figure or exercise that covers it); the idea; notation; the key equation, tagged *from the paper* or *our example*.
- **Playground:** live figures, a live insight sentence, and a table of all computed values.
- **Worked steps and exercises:** worked steps with highlighted live numbers; exercises with predict, set up, try it, observe, why and an automatic check. The exercises also show your prediction, what changed, and the previous state as a faint outline.
- **Closing sections:** key takeaways, remarks (limitation and common error), and sources, with each claim tagged and a summary of the checks that ran.
- **Note:** the page always states that the toy demo does not reproduce the paper's experiments.

**Budget (per case):**
- At most 5 API calls (the limit is 10) and 14k completion tokens (the limit is 30k).
- A 420 s soft deadline (the limit is 600 s).
- Reasoning is off and JSON mode is on.
- A typical run is **1 call, about 4.0–4.5k total tokens and 7–10 s**. A run that needs a repair uses 2 calls and about 8k tokens.

## Repository layout

```
agent.py              CLI entry point
core/                 pipeline, OpenRouter client, budget, trace, QuickJS checks, repair, fallback
prompts/              spec, repair and emergency system prompts
template/             page.html (generic template) and render.py (spec -> index.html)
cases/                practice inputs (attention, entropy, softmax temperature, gradient descent,
                      Nyquist, Bayes, PageRank, RC charging, Michaelis-Menten, ...)
specs/                hand-made reference specs used by the offline tests
tests/                offline tests with mocked model replies: python tests/run_tests.py
tools/                prompt and repair experiments
examples/entropy/     example input and generated output
SPEC.md               the spec contract between prompt, checks and template
```

## Example input and output

Input: `examples/entropy/case.json` (the same as `cases/example_b.json`)

```json
{
  "source_url": "https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf",
  "focus": "Section 6, discrete entropy. Use a small probability distribution. Let the learner change the distribution and the number of outcomes. Show probabilities, individual contributions and total entropy in bits. Compare a certain outcome with equally likely outcomes. Check that certainty gives zero bits and four equally likely outcomes give two bits; handle zero probabilities correctly.",
  "audience": "Engineering undergraduate"
}
```

Output: `examples/entropy/index.html` and `examples/entropy/trace.jsonl`. This example was generated once for showcasing; assessed outputs are generated afresh.

## Testing

```
python tests/run_tests.py                # offline, mocked model replies
python tests/trace_summary.py out/trace.jsonl
```

## Credits

- [QuickJS](https://bellard.org/quickjs/) via the `quickjs` Python package: sandboxed execution of `compute()` for the checks
- [latex2mathml](https://github.com/roniemartinez/latex2mathml): LaTeX to MathML conversion (rendered natively by Chromium)
- [requests](https://requests.readthedocs.io/): HTTP client for OpenRouter
- Papers used for the public practice examples: Vaswani et al., *Attention Is All You Need* (2017); C. E. Shannon, *A Mathematical Theory of Communication* (1948)
- AI coding assistants (Claude) were used during development, as permitted by the hackathon rules. All charts, layout and code are our own; no third-party UI assets, fonts or CDNs are used.
