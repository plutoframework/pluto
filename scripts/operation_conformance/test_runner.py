import json
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from operation_conformance.runner import (
    OWNER_LABEL, REQUIRED, remove_owned, run, validate_results,
)


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
                if arguments[:2] == ["docker", "inspect"] and len(arguments) == 3:
                    owner = arguments[2].removeprefix("pluto-operation-")
                    return json.dumps([{"Id": "owned-container", "Config": {"Labels": {OWNER_LABEL: owner}}}])
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
                if arguments[:2] == ["docker", "inspect"] and len(arguments) == 3:
                    owner = arguments[2].removeprefix("pluto-operation-")
                    return json.dumps([{"Id": "owned-container", "Config": {"Labels": {OWNER_LABEL: owner}}}])
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

    def test_failed_or_timed_out_launch_still_cleans_owned_name(self):
        for failure in (RuntimeError("launch failed"),
                        subprocess.TimeoutExpired("docker", 180)):
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    for name in ("core", "rbac"):
                        (root / name).mkdir()
                        (root / name / "go.mod").write_text("module candidate.invalid/" + name)
                    launched = []
                    def execute(arguments, **unused):
                        if arguments[:2] == ["docker", "run"]:
                            launched.append(arguments[arguments.index("--name") + 1])
                            raise failure
                        if arguments[:2] == ["docker", "inspect"]:
                            self.assertEqual(arguments[2], launched[0])
                            owner = launched[0].removeprefix("pluto-operation-")
                            return json.dumps([{"Id": "owned-id", "Config": {"Labels": {OWNER_LABEL: owner}}}])
                        return ""
                    with patch("operation_conformance.runner.command", side_effect=execute) as commands:
                        report = run(root / "core", root / "rbac")
                    self.assertFalse(report["passed"])
                    self.assertTrue(report["database"]["removed"])
                    self.assertTrue(any(call.args[0] == ["docker", "rm", "--force", "owned-id"]
                                        for call in commands.call_args_list))

    def test_cleanup_tolerates_absence_and_refuses_unrelated_container(self):
        with patch("operation_conformance.runner.command", side_effect=RuntimeError("No such object: owned")):
            remove_owned("owned", "token")
        unrelated = json.dumps([{"Id": "unrelated", "Config": {"Labels": {OWNER_LABEL: "other"}}}])
        with patch("operation_conformance.runner.command", return_value=unrelated) as execute:
            with self.assertRaisesRegex(ValueError, "ownership mismatch"):
                remove_owned("owned", "token")
            self.assertEqual(execute.call_count, 1)
        with patch("operation_conformance.runner.command", side_effect=RuntimeError("daemon unavailable")):
            with self.assertRaises(RuntimeError):
                remove_owned("owned", "token")

    def test_missing_module_fails_before_docker(self):
        with patch("operation_conformance.runner.command") as execute:
            report = run(Path("/missing/core"), Path("/missing/rbac"))
        self.assertFalse(report["passed"])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
