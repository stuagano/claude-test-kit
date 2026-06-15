"""
A DELIBERATELY broken version, to demonstrate the kit catching silent failures.

Bugs on purpose:
  * Swallows the read error (bare except: pass) -> swallowed exception.
  * Still prints a success-looking message and exits 0 -> "exit 0 but wrong output".
  * Writes an empty file -> "no output validation" would let this slide.

The tests in tests/test_catches_bugs.py prove the kit flags every one of these.
"""

import argparse
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    text = ""
    try:
        with open(args.input) as f:
            text = f.read()
    except Exception:  # noqa: BLE001 - intentionally swallowed for the demo
        pass

    # writes an empty file no matter what
    with open(args.out, "w") as f:
        f.write("")

    # lies about success
    print("Processed 0 words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
