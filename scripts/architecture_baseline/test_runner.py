import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from architecture_baseline.runner import run, validate, EvidenceError


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.baseline = self.root / "baseline"
        self.baseline.mkdir()
        (self.baseline / "pluto.toml").write_text("schema = 1\n")
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                      "commit", "-qm", "baseline"]):
            subprocess.run(["git", "-C", str(self.baseline), *args], check=True)
        revision = subprocess.check_output(
            ["git", "-C", str(self.baseline), "rev-parse", "HEAD"], text=True,
        ).strip()
        self.ledger = {"revision": revision, "coverage": "targeted only", "expected": [
            {"id": "ARCH002", "file": "pluto.toml", "messageContains": "schema"},
        ]}
        self.payload = {
            "version": 1, "directory": str(self.baseline), "packages": [],
            "diagnostics": [{"id": "ARCH002", "file": "pluto.toml", "line": 1,
                             "severity": "error", "message": "legacy schema",
                             "repair": "migrate explicitly"}],
            "unassessed": ["semantic policies"], "passed": False,
        }

    def tool(self, payload=None, code=1, extra=""):
        path = self.root / "cli"
        output = json.dumps(self.payload if payload is None else payload)
        path.write_text("#!/usr/bin/env python3\nimport sys\n" + extra +
                        f"\nprint({output!r})\nsys.exit({code})\n")
        path.chmod(0o755)
        return path

    def test_negative_evidence_is_not_compliance(self):
        (self.baseline / ".DS_Store").write_text("ignored")
        report = run(self.tool(), self.baseline, self.ledger)
        self.assertTrue(report["expectedFailureChecksPassed"], report)
        self.assertFalse(report["targetCompliancePassed"])
        self.assertEqual(report["unassessed"], ["semantic policies"])
        self.assertFalse(run(self.tool(), self.baseline, self.ledger, True)
                         ["expectedFailureChecksPassed"])

    def test_invalid_reports_fail(self):
        for change in (
            lambda p: p.update(version=2),
            lambda p: p.update(passed=True),
            lambda p: p.pop("unassessed"),
            lambda p: p.update(unassessed=[{}]),
            lambda p: p.update(packages=[{}]),
            lambda p: p["diagnostics"][0].pop("repair"),
            lambda p: p.update(diagnostics=[]),
            lambda p: p["diagnostics"][0].update(id="OTHER"),
            lambda p: p["diagnostics"][0].update(message="wrong cause"),
            lambda p: p["diagnostics"][0].update(line=0),
            lambda p: p["diagnostics"][0].update(file="../pluto.toml"),
            lambda p: p.update(directory="elsewhere"),
        ):
            payload = copy.deepcopy(self.payload)
            change(payload)
            with self.assertRaises(EvidenceError):
                validate(payload, 1, self.baseline, self.ledger, False)
        for code in (0, 2, -9):
            self.assertFalse(run(self.tool(code=code), self.baseline, self.ledger)
                             ["expectedFailureChecksPassed"])

    def test_crash_invalid_json_and_missing_checker_fail(self):
        for extra in ("raise RuntimeError('crash')", "print('not json'); sys.exit(1)"):
            self.assertFalse(run(self.tool(extra=extra), self.baseline, self.ledger)
                             ["expectedFailureChecksPassed"])
        self.assertFalse(run(self.root / "missing", self.baseline, self.ledger)
                         ["expectedFailureChecksPassed"])

    def test_tampering_and_revision_fail(self):
        tool = self.tool()
        (self.baseline / "new.go").write_text("package forbidden\n")
        self.assertFalse(run(tool, self.baseline, self.ledger)
                         ["expectedFailureChecksPassed"])
        (self.baseline / "new.go").unlink()
        (self.baseline / "pluto.toml").write_text("schema = 8\n")
        self.assertFalse(run(tool, self.baseline, self.ledger)
                         ["expectedFailureChecksPassed"])
        (self.baseline / "pluto.toml").write_text("schema = 1\n")
        self.ledger["revision"] = "wrong"
        self.assertFalse(run(tool, self.baseline, self.ledger)
                         ["expectedFailureChecksPassed"])

    def test_ignored_source_is_rejected_but_build_artifacts_are_not(self):
        (self.baseline / ".git/info/exclude").write_text("local/\n")
        local = self.baseline / "local"
        local.mkdir()
        (local / "cert.pem").write_text("synthetic certificate")
        self.assertTrue(run(self.tool(), self.baseline, self.ledger)
                        ["expectedFailureChecksPassed"])
        (local / "hidden.go").write_text("package hidden\n")
        self.assertFalse(run(self.tool(), self.baseline, self.ledger)
                         ["expectedFailureChecksPassed"])

    def test_checker_cannot_modify_baseline(self):
        target = str(self.baseline / "pluto.toml")
        tool = self.tool(extra=f"open({target!r}, 'w').write('tampered')")
        report = run(tool, self.baseline, self.ledger)
        self.assertFalse(report["expectedFailureChecksPassed"])
        self.assertTrue(report["errors"])


if __name__ == "__main__":
    unittest.main()
