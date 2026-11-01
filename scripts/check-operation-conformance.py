#!/usr/bin/env python3
"""Compose local Auth/RBAC/Core/Jobs candidates against disposable PostgreSQL only."""
import argparse
import json
from pathlib import Path

from operation_conformance.runner import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--rbac", type=Path, required=True)
    parser.add_argument("--auth", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--image", default="postgres:17-alpine")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run(args.core.resolve(), args.rbac.resolve(), args.auth.resolve(),
                 args.jobs.resolve(), args.image)
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.write_text(output)
    print(output, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
