#!/usr/bin/env python3
"""Reject unwrapped conformance source, including Go stored as .go.txt."""

from pathlib import Path

from operation_conformance.style import check


errors = check(Path(__file__).resolve().parents[1])
if errors:
    raise SystemExit("\n".join(errors))
print("PASS: conformance Python and Go fixture source fit 80 columns")
