"""Check source classes that the Go package formatter cannot discover."""

from pathlib import Path
import re


IMPORT = re.compile(r'\s*(?:[\w.]+\s+)?"[^"\s]+"\s*')


def violations(path, source):
    """Return overlong physical lines; Go import tokens cannot be split."""
    result = []
    for number, line in enumerate(source.splitlines(), 1):
        if len(line.expandtabs(4)) <= 80:
            continue
        if str(path).endswith(".go.txt") and IMPORT.fullmatch(line):
            continue
        result.append(number)
    return result


def check(root):
    scripts = Path(root) / "scripts"
    paths = [
        scripts / "check-architecture-baseline.py",
        scripts / "check-operation-conformance.py",
        scripts / "check-conformance-style.py",
        *sorted((scripts / "architecture_baseline").glob("*.py")),
        *sorted((scripts / "operation_conformance").glob("*.py")),
        *sorted((scripts / "operation_conformance").glob("*.go.txt")),
    ]
    errors = []
    for path in paths:
        errors.extend(
            f"{path.relative_to(root)}:{line}: exceeds 80 columns"
            for line in violations(path, path.read_text())
        )
    return errors
