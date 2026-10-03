# Paper to Playground

Turns a research-paper concept into an interactive, offline teaching page.

## Team
- Person A: agent loop, checks, trace
- Person B: HTML template and visuals
- Person C (Nanar): prompts, spec format, testing

## Model
MODEL_ID: deepseek/deepseek-v4.1-flash (via OpenRouter)

## Setup and run
    python -m pip install -r requirements.txt
    export OPENROUTER_API_KEY="..."
    python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash

Outputs: out/index.html (self-contained, works offline) and out/trace.jsonl.

## Architecture
1. The model writes a compact JSON spec (see SPEC.md): explanation, controls,
   a JavaScript compute() function, explorations, self-tests, and claims tagged paper/ours.
2. Python runs compute() in QuickJS and checks: schema, no NaN, edge cases,
   every control changes an output, self-tests, and that each exploration's
   promised result actually happens.
3. If checks fail, a targeted repair call fixes only the failing fields (max 2).
4. A generic, pre-built template renders the spec into index.html. Equations are
   LaTeX converted to MathML (latex2mathml).

Typical run: 1 call, about 3-4k tokens; 2 calls if a repair is needed.

## Example
Input: cases/example_b.json. Output: examples/entropy/index.html

## Credits
- quickjs (Python bindings to QuickJS): runs compute() for checks
- latex2mathml: LaTeX to MathML conversion
- requests: HTTP client