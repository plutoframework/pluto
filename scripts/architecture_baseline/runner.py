"""External negative conformance evidence; never runs baseline-owned scripts."""

import hashlib
import json
from pathlib import Path
import subprocess


class EvidenceError(ValueError):
    pass


def git(directory, *args):
    result = subprocess.run(
        ["git", "-C", str(directory), *args],
        capture_output=True,
        check=True,
    )
    return result.stdout


def snapshot(directory, revision):
    head = git(directory, "rev-parse", "HEAD").decode().strip()
    if head != revision:
        raise EvidenceError("baseline revision differs from trusted ledger")
    if git(directory, "diff", "HEAD", "--"):
        raise EvidenceError("baseline tracked files changed")
    untracked = git(
        directory, "ls-files", "--others", "--exclude-standard", "-z"
    )
    ignored = git(
        directory,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
    )
    source_suffixes = {
        ".go",
        ".mod",
        ".sum",
        ".py",
        ".sh",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".sql",
        ".html",
        ".css",
        ".vue",
        ".svelte",
    }
    for name in untracked.decode().split("\0"):
        if name and Path(name).name != ".DS_Store":
            raise EvidenceError(f"untracked baseline file: {name}")
    for name in ignored.decode().split("\0"):
        if any(
            part
            in {
                "node_modules",
                "dist",
                ".next",
                "__pycache__",
                "test-results",
                "playwright-report",
                "coverage",
            }
            for part in Path(name).parts
        ):
            continue
        if name and (
            Path(name).suffix in source_suffixes
            or Path(name).name in {"Makefile", "Dockerfile", "AGENTS.md"}
        ):
            raise EvidenceError(f"ignored untracked baseline source: {name}")
    digest = hashlib.sha256()
    for name in git(directory, "ls-files", "-z").decode().split("\0"):
        if name:
            path = directory / name
            digest.update(name.encode() + b"\0")
            if path.is_symlink():
                raise EvidenceError(
                    "baseline contains unsupported tracked symlink"
                )
            digest.update(path.read_bytes())
    return {"revision": head, "trackedSHA256": digest.hexdigest()}


def validate(payload, exit_code, directory, ledger, require_complete):
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
    ):
        raise EvidenceError("invalid report version/schema")
    if payload.get("directory") != str(directory):
        raise EvidenceError("report directory does not match baseline")
    for key in ("packages", "diagnostics", "unassessed"):
        if not isinstance(payload.get(key), list):
            raise EvidenceError(f"missing/invalid {key}")
    if not all(
        isinstance(item, str) and item for item in payload["unassessed"]
    ):
        raise EvidenceError("invalid unassessed coverage")
    if payload.get("passed") is not False or exit_code != 1:
        raise EvidenceError("expected explicit failed compliance and exit 1")
    for package in payload["packages"]:
        if not isinstance(package, dict) or not all(
            isinstance(package.get(key), str) and package[key]
            for key in ("path", "role")
        ):
            raise EvidenceError("invalid package classification")
    for diagnostic in payload["diagnostics"]:
        if not isinstance(diagnostic, dict) or not all(
            isinstance(diagnostic.get(key), str) and diagnostic[key]
            for key in ("id", "file", "severity", "message", "repair")
        ):
            raise EvidenceError("invalid diagnostic schema")
        location = Path(diagnostic["file"])
        line = diagnostic.get("line")
        if location.is_absolute() or ".." in location.parts:
            raise EvidenceError("diagnostic escapes baseline")
        path = directory / location
        if (
            not path.is_file()
            or type(line) is not int
            or not (1 <= line <= len(path.read_bytes().splitlines()))
        ):
            raise EvidenceError("invalid diagnostic location")
    for expected in ledger["expected"]:
        if not any(
            diagnostic["id"] == expected["id"]
            and diagnostic["file"] == expected["file"]
            and diagnostic["severity"] == "error"
            and (
                "line" not in expected or diagnostic["line"] == expected["line"]
            )
            and expected["messageContains"].lower()
            in diagnostic["message"].lower()
            for diagnostic in payload["diagnostics"]
        ):
            raise EvidenceError(
                f"missing expected diagnostic: {expected['id']}"
            )
    if require_complete and payload["unassessed"]:
        raise EvidenceError("full milestone has unassessed boundaries")


def run(cli, directory, ledger, require_complete=False):
    report = {
        "version": 1,
        "coverage": ledger["coverage"],
        "expectedFailureChecksPassed": False,
        "targetCompliancePassed": False,
        "errors": [],
    }
    before = None
    try:
        before = snapshot(directory, ledger["revision"])
        report["baseline"] = before
        tool_bytes = cli.read_bytes()
        report["tool"] = {
            "path": str(cli),
            "sha256": hashlib.sha256(tool_bytes).hexdigest(),
        }
        result = subprocess.run(
            [
                str(cli),
                "architecture",
                "check",
                "--directory",
                str(directory),
                "--format",
                "json",
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
        report["tool"]["exitCode"] = result.returncode
        if cli.read_bytes() != tool_bytes:
            raise EvidenceError("checker executable changed during execution")
        payload = json.loads(result.stdout)
        report["unassessed"] = (
            payload.get("unassessed") if isinstance(payload, dict) else None
        )
        report["diagnostics"] = (
            payload.get("diagnostics") if isinstance(payload, dict) else None
        )
        validate(
            payload, result.returncode, directory, ledger, require_complete
        )
        report["expectedFailureChecksPassed"] = True
    except (
        EvidenceError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        report["errors"].append(str(error))
    finally:
        if before is not None:
            try:
                if snapshot(directory, ledger["revision"]) != before:
                    raise EvidenceError(
                        "baseline changed during checker execution"
                    )
            except (
                EvidenceError,
                OSError,
                subprocess.SubprocessError,
            ) as error:
                report["errors"].append(str(error))
                report["expectedFailureChecksPassed"] = False
    return report
