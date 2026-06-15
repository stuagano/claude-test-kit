"""
A tiny well-behaved example tool: count words in a file, write JSON result.

    python examples/word_count.py input.txt --out result.json
"""

import argparse
import json
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    try:
        with open(args.input) as f:
            text = f.read()
    except OSError as e:
        # Correct behavior: surface the error, non-zero exit. NOT swallowed.
        print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
        return 2

    words = text.split()
    result = {"ok": True, "words": len(words), "chars": len(text)}
    with open(args.out, "w") as f:
        json.dump(result, f)

    print(f"Processed {len(words)} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
