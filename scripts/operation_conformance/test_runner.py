import json
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from operation_conformance.runner import (
    OWNER_LABEL,
    REQUIRED,
    remove_owned,
    run,
    validate_results,
)
from operation_conformance.style import violations


class ResultTests(unittest.TestCase):
    def test_bad_fixture_style_fails_before_database_startup(self):
        with patch(
            "operation_conformance.runner.check",
            return_value=["consumer_test.go.txt:42: exceeds 80 columns"],
        ):
            with patch("operation_conformance.runner.command") as commands:
                report = run(
                    Path("core"), Path("rbac"), Path("auth"), Path("jobs")
                )
        self.assertFalse(report["passed"])
        self.assertIn("consumer_test.go.txt:42", report["errors"][0])
        commands.assert_not_called()

    def test_go_fixture_extension_does_not_hide_dense_callbacks(self):
        source = (
            "package conformance\n"
            "func sample() {\n"
            "\tresults <- manager.WithIdentity(ctx, token, "
            "func(ctx context.Context, identity *auth.Identity) error { "
            "return operation.execute(ctx, identity) })\n"
            "}\n"
        )
        self.assertEqual(violations("consumer_test.go.txt", source), [3])
        expanded = (
            "package conformance\n"
            "func sample() {\n"
            "\tresults <- manager.WithIdentity(\n"
            "\t\tctx, token,\n"
            "\t\tfunc(ctx context.Context, identity *auth.Identity) error {\n"
            "\t\t\treturn operation.execute(ctx, identity)\n"
            "\t\t},\n"
            "\t)\n"
            "}\n"
        )
        self.assertEqual(violations("consumer_test.go.txt", expanded), [])

    def test_unsplittable_import_does_not_waive_ordinary_strings(self):
        path = "example.test/" + "segment/" * 10 + "package"
        self.assertEqual(
            violations("consumer_test.go.txt", '\t"' + path + '"'), []
        )
        self.assertEqual(
            violations("consumer_test.go.txt", 'var path = "' + path + '"'),
            [1],
        )

    def output(self, names=REQUIRED):
        return "\n".join(
            json.dumps(
                {
                    "Action": "pass",
                    "Package": name.split("::", 1)[0],
                    "Test": name.split("::", 1)[1],
                }
            )
            for name in names
        )

    def test_required_tests_execute(self):
        self.assertEqual(validate_results(self.output()), sorted(REQUIRED))

    def test_missing_skipped_failed_invalid_reports_fail(self):
        for output in (
            "",
            "not JSON",
            "null",
            "[]",
            '{"Action":"pass","Test":"case","Package":1}',
            self.output(set()),
            self.output() + '\n{"Action":"skip","Test":"required_case"}',
            self.output() + '\n{"Action":"fail"}',
        ):
            with self.assertRaises(ValueError):
                validate_results(output)

    def test_owned_container_is_removed_after_test_failure(self):
        self.assert_failed_step_stops_run("test")

    def test_formatter_failure_prevents_runtime_evidence(self):
        self.assert_failed_step_stops_run("format")

    def test_linter_failure_prevents_runtime_evidence(self):
        self.assert_failed_step_stops_run("lint")

    def assert_failed_step_stops_run(self, step):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("core", "rbac", "auth", "jobs"):
                (root / name).mkdir()
                (root / name / "go.mod").write_text(
                    "module candidate.invalid/" + name
                )

            def execute(arguments, **unused):
                if arguments[:2] == ["docker", "run"]:
                    return "owned-container\n"
                if (
                    arguments[:2] == ["docker", "inspect"]
                    and len(arguments) == 3
                ):
                    owner = arguments[2].removeprefix("pluto-operation-")
                    return json.dumps(
                        [
                            {
                                "Id": "owned-container",
                                "Config": {"Labels": {OWNER_LABEL: owner}},
                            }
                        ]
                    )
                if arguments[:2] == ["docker", "port"]:
                    return "127.0.0.1:12345\n"
                selected = {
                    "test": arguments[:2] == ["go", "test"],
                    "format": arguments[:2] == ["go", "run"],
                    "lint": arguments[0].endswith("conformance-golangci.sh"),
                }
                if selected[step]:
                    raise RuntimeError(f"synthetic {step} failure")
                return ""

            with patch(
                "operation_conformance.runner.command", side_effect=execute
            ) as commands:
                with patch(
                    "operation_conformance.runner.subprocess.run",
                    return_value=SimpleNamespace(returncode=0),
                ):
                    report = run(
                        root / "core",
                        root / "rbac",
                        root / "auth",
                        root / "jobs",
                    )
            self.assertFalse(report["passed"])
            self.assertTrue(report["database"]["removed"])
            self.assertIn(f"synthetic {step} failure", report["errors"])
            if step != "test":
                self.assertFalse(
                    any(
                        call.args[0][:2] == ["go", "test"]
                        for call in commands.call_args_list
                    )
                )
            self.assertTrue(
                any(
                    call.args[0]
                    == ["docker", "rm", "--force", "owned-container"]
                    for call in commands.call_args_list
                )
            )

    def test_invalid_report_also_removes_owned_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("core", "rbac", "auth", "jobs"):
                (root / name).mkdir()
                (root / name / "go.mod").write_text(
                    "module candidate.invalid/" + name
                )

            def execute(arguments, **unused):
                if arguments[:2] == ["docker", "run"]:
                    return "owned-container\n"
                if (
                    arguments[:2] == ["docker", "inspect"]
                    and len(arguments) == 3
                ):
                    owner = arguments[2].removeprefix("pluto-operation-")
                    return json.dumps(
                        [
                            {
                                "Id": "owned-container",
                                "Config": {"Labels": {OWNER_LABEL: owner}},
                            }
                        ]
                    )
                if arguments[:2] == ["docker", "port"]:
                    return "127.0.0.1:12345\n"
                if arguments[:2] == ["go", "test"]:
                    return "not JSON"
                return ""

            with patch(
                "operation_conformance.runner.command", side_effect=execute
            ):
                with patch(
                    "operation_conformance.runner.subprocess.run",
                    return_value=SimpleNamespace(returncode=0),
                ):
                    report = run(
                        root / "core",
                        root / "rbac",
                        root / "auth",
                        root / "jobs",
                    )
            self.assertFalse(report["passed"])
            self.assertTrue(report["database"]["removed"])
            self.assertTrue(report["errors"])

    def test_failed_or_timed_out_launch_still_cleans_owned_name(self):
        for failure in (
            RuntimeError("launch failed"),
            subprocess.TimeoutExpired("docker", 180),
        ):
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    for name in ("core", "rbac", "auth", "jobs"):
                        (root / name).mkdir()
                        (root / name / "go.mod").write_text(
                            "module candidate.invalid/" + name
                        )
                    launched = []

                    def execute(arguments, **unused):
                        if arguments[:2] == ["docker", "run"]:
                            launched.append(
                                arguments[arguments.index("--name") + 1]
                            )
                            raise failure
                        if arguments[:2] == ["docker", "inspect"]:
                            self.assertEqual(arguments[2], launched[0])
                            owner = launched[0].removeprefix("pluto-operation-")
                            return json.dumps(
                                [
                                    {
                                        "Id": "owned-id",
                                        "Config": {
                                            "Labels": {OWNER_LABEL: owner}
                                        },
                                    }
                                ]
                            )
                        return ""

                    with patch(
                        "operation_conformance.runner.command",
                        side_effect=execute,
                    ) as commands:
                        report = run(
                            root / "core",
                            root / "rbac",
                            root / "auth",
                            root / "jobs",
                        )
                    self.assertFalse(report["passed"])
                    self.assertTrue(report["database"]["removed"])
                    self.assertTrue(
                        any(
                            call.args[0]
                            == ["docker", "rm", "--force", "owned-id"]
                            for call in commands.call_args_list
                        )
                    )

    def test_cleanup_tolerates_absence_and_refuses_unrelated_container(self):
        with patch(
            "operation_conformance.runner.command",
            side_effect=RuntimeError("No such object: owned"),
        ):
            remove_owned("owned", "token")
        unrelated = json.dumps(
            [{"Id": "unrelated", "Config": {"Labels": {OWNER_LABEL: "other"}}}]
        )
        with patch(
            "operation_conformance.runner.command", return_value=unrelated
        ) as execute:
            with self.assertRaisesRegex(ValueError, "ownership mismatch"):
                remove_owned("owned", "token")
            self.assertEqual(execute.call_count, 1)
        with patch(
            "operation_conformance.runner.command",
            side_effect=RuntimeError("daemon unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                remove_owned("owned", "token")

    def test_every_required_case_and_package_is_mandatory(self):
        for name in REQUIRED:
            with self.subTest(missing=name):
                with self.assertRaisesRegex(ValueError, "missing composition"):
                    validate_results(self.output(REQUIRED - {name}))
        with self.assertRaisesRegex(ValueError, "missing composition"):
            validate_results(
                self.output().replace(
                    "conformance.invalid/operation", "wrong/package"
                )
            )
        output = self.output() + '\n{"Action":"skip","Package":"empty/package"}'
        self.assertEqual(validate_results(output), sorted(REQUIRED))

    def test_all_four_source_directories_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for missing in ("core", "rbac", "auth", "jobs"):
                directories = {
                    name: root / (missing + "-" + name)
                    for name in ("core", "rbac", "auth", "jobs")
                }
                for name, directory in directories.items():
                    if name != missing:
                        directory.mkdir()
                        (directory / "go.mod").write_text(
                            "module candidate.invalid/" + name
                        )
                with patch("operation_conformance.runner.command") as execute:
                    report = run(
                        *(
                            directories[name]
                            for name in ("core", "rbac", "auth", "jobs")
                        )
                    )
                self.assertFalse(report["passed"])
                self.assertIn(
                    "missing " + missing + " module", report["errors"]
                )
                execute.assert_not_called()

    def test_cli_requires_auth_and_jobs_arguments(self):
        script = Path(__file__).parents[1] / "check-operation-conformance.py"
        result = subprocess.run(
            [
                "python3",
                str(script),
                "--core",
                "/missing",
                "--rbac",
                "/missing",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--auth", result.stderr)
        self.assertIn("--jobs", result.stderr)

    def test_candidate_profile_and_module_replacements_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("core", "rbac", "auth", "jobs"):
                (root / name).mkdir()
                (root / name / "go.mod").write_text(
                    "module candidate.invalid/" + name
                )

            def execute(arguments, **unused):
                if (
                    arguments[:2] == ["docker", "inspect"]
                    and len(arguments) == 3
                ):
                    owner = arguments[2].removeprefix("pluto-operation-")
                    return json.dumps(
                        [
                            {
                                "Id": "owned",
                                "Config": {"Labels": {OWNER_LABEL: owner}},
                            }
                        ]
                    )
                if arguments[:2] == ["docker", "port"]:
                    return "127.0.0.1:12345"
                if arguments[:2] == ["go", "test"]:
                    self.assertIn("plutocandidate", arguments)
                    self.assertIn("-race", arguments)
                    return self.output()
                return ""

            with patch(
                "operation_conformance.runner.command", side_effect=execute
            ) as commands:
                with patch(
                    "operation_conformance.runner.subprocess.run",
                    return_value=SimpleNamespace(returncode=0),
                ):
                    report = run(
                        *(
                            root / name
                            for name in ("core", "rbac", "auth", "jobs")
                        )
                    )
            self.assertTrue(report["passed"], report)
            arguments = [
                argument
                for call in commands.call_args_list
                for argument in call.args[0]
            ]
            for module in (
                "pluto-auth",
                "pluto-jobs",
                "pluto-jobs/stores/postgres",
                "pluto-rbac/identity",
                "pluto-core/stores/postgres",
            ):
                self.assertTrue(
                    any(
                        value.startswith(
                            "-replace=github.com/plutoframework/" + module + "="
                        )
                        for value in arguments
                    ),
                    module,
                )

    def test_missing_module_fails_before_docker(self):
        with patch("operation_conformance.runner.command") as execute:
            report = run(
                Path("/missing/core"),
                Path("/missing/rbac"),
                Path("/missing/auth"),
                Path("/missing/jobs"),
            )
        self.assertFalse(report["passed"])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
