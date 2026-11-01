import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from operation_conformance.runner import REQUIRED, run, validate_results


class ResultTests(unittest.TestCase):
    def output(self, names=REQUIRED):
        return "\n".join(json.dumps({"Action": "pass", "Test": name}) for name in names)

    def test_required_tests_execute(self):
        self.assertEqual(validate_results(self.output()), sorted(REQUIRED))

    def test_missing_skipped_failed_invalid_reports_fail(self):
        for output in (
            "", "not JSON", "null", "[]", self.output(set()), self.output() + '\n{"Action":"skip"}',
            self.output() + '\n{"Action":"fail"}',
        ):
            with self.assertRaises(ValueError):
                validate_results(output)

    def test_owned_container_is_removed_after_test_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("core", "rbac"):
                (root / name).mkdir()
                (root / name / "go.mod").write_text("module candidate.invalid/" + name)
            def execute(arguments, **unused):
                if arguments[:2] == ["docker", "run"]:
                    return "owned-container\n"
                if arguments[:2] == ["docker", "port"]:
                    return "127.0.0.1:12345\n"
                if arguments[:2] == ["go", "test"]:
                    raise RuntimeError("synthetic test failure")
                return ""
            with patch("operation_conformance.runner.command", side_effect=execute) as commands:
                with patch("operation_conformance.runner.subprocess.run", return_value=SimpleNamespace(returncode=0)):
                    report = run(root / "core", root / "rbac")
            self.assertFalse(report["passed"])
            self.assertTrue(report["database"]["removed"])
            self.assertIn("synthetic test failure", report["errors"])
            self.assertTrue(any(call.args[0] == ["docker", "rm", "--force", "owned-container"]
                                for call in commands.call_args_list))

    def test_invalid_report_also_removes_owned_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("core", "rbac"):
                (root / name).mkdir()
                (root / name / "go.mod").write_text("module candidate.invalid/" + name)
            def execute(arguments, **unused):
                if arguments[:2] == ["docker", "run"]:
                    return "owned-container\n"
                if arguments[:2] == ["docker", "port"]:
                    return "127.0.0.1:12345\n"
                if arguments[:2] == ["go", "test"]:
                    return "not JSON"
                return ""
            with patch("operation_conformance.runner.command", side_effect=execute):
                with patch("operation_conformance.runner.subprocess.run", return_value=SimpleNamespace(returncode=0)):
                    report = run(root / "core", root / "rbac")
            self.assertFalse(report["passed"])
            self.assertTrue(report["database"]["removed"])
            self.assertTrue(report["errors"])

    def test_missing_module_fails_before_docker(self):
        with patch("operation_conformance.runner.command") as execute:
            report = run(Path("/missing/core"), Path("/missing/rbac"))
        self.assertFalse(report["passed"])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
