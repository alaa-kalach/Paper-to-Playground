"""Paper to Playground generator.

    python agent.py --input case.json --output out --model MODEL_ID

Writes out/index.html (self-contained) and out/trace.jsonl. Exit 0 on success, nonzero on failure.
"""
import argparse
import sys

from core.pipeline import main


def cli():
    ap = argparse.ArgumentParser(description="Turn a paper excerpt brief into an interactive explanation.")
    ap.add_argument("--input", required=True, help="path to case.json (source_url, focus, audience)")
    ap.add_argument("--output", required=True, help="output directory")
    ap.add_argument("--model", required=True, help="OpenRouter model id")
    a = ap.parse_args()
    return main(a.input, a.output, a.model)


if __name__ == "__main__":
    sys.exit(cli())
