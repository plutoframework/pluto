#!/usr/bin/env python3
"""Run framework-owned checks against the unchanged frozen negative baseline."""
import argparse
import json
from pathlib import Path

from architecture_baseline.runner import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    ledger = json.loads(Path(__file__).with_name("architecture-baseline.json").read_text())
    report = run(args.cli.resolve(), args.baseline.resolve(), ledger, args.require_complete)
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.write_text(output)
    print(output, end="")
    return 0 if report["expectedFailureChecksPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
