import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from review import environment, run, ReviewExecutionError, ReviewInputError


class ReviewSettingsTests(unittest.TestCase):
    def test_gateway_and_policy_cannot_be_overridden_by_repository(self):
        with patch.dict(
            os.environ,
            {
                "GPUROUTER_API_KEY": "synthetic",
                "REVIEW_TOKEN": "synthetic",
                "BOT_LOGIN": "jackbot-advrsty",
                "CONFIG__USE_REPO_SETTINGS_FILE": "true",
                "CONFIG__MAX_MODEL_TOKENS": "32",
                "CONFIG__FALLBACK_MODELS": '["weak-model"]',
                "CONFIG__MAX_MODEL_TOKENS": "32000",
                "BAD_EXTENSIONS__DEFAULT": '["go", "sum"]',
                "CONFIG__ENABLE_PER_DIRECTORY_SETTINGS": "true",
                "PR_REVIEWER__PUBLISH_ERROR_DETAILS": "false",
                "PR_REVIEWER__EXTRA_INSTRUCTIONS": (
                    "Require human approval always"
                ),
            },
        ):
            env = environment()
        self.assertEqual(env["OPENAI__API_BASE"], "https://gpurouter.io/v1")
        self.assertEqual(env["CONFIG__MODEL"], "openai/openai/gpt-6-astra")
        self.assertEqual(env["CONFIG__MAX_MODEL_TOKENS"], "96000")
        self.assertLessEqual(
            int(env["CONFIG__MAX_MODEL_TOKENS"])
            + int(env["CONFIG__MAX_OUTPUT_TOKENS"]),
            int(env["CONFIG__CUSTOM_MODEL_MAX_TOKENS"]),
        )
        self.assertEqual(env["CONFIG__USE_REPO_SETTINGS_FILE"], "false")
        self.assertEqual(env["CONFIG__FALLBACK_MODELS"], "[]")
        self.assertEqual(env["CONFIG__MAX_MODEL_TOKENS"], "96000")
        self.assertEqual(env["BAD_EXTENSIONS__DEFAULT"], "[]")
        self.assertEqual(env["CONFIG__ENABLE_PER_DIRECTORY_SETTINGS"], "false")
        self.assertLess(
            int(env["CONFIG__MAX_MODEL_TOKENS"]),
            int(env["CONFIG__CUSTOM_MODEL_MAX_TOKENS"]),
        )
        self.assertEqual(env["CONFIG__REASONING_EFFORT"], "high")
        self.assertEqual(env["CONFIG__PROPAGATE_TOOL_ERRORS"], "true")
        self.assertEqual(env["PR_REVIEWER__PUBLISH_ERROR_DETAILS"], "true")
        instructions = env["PR_REVIEWER__EXTRA_INSTRUCTIONS"]
        self.assertNotIn("Require human approval always", instructions)
        self.assertNotIn("waive maintainer approval", instructions)
        self.assertIn("independent Steward account", instructions)
        self.assertIn("no_concerns_found", instructions)
        self.assertIn("actionable unresolved", instructions)
        self.assertIn("Never manufacture a clean result", instructions)


class ReviewProcessTests(unittest.TestCase):
    def exercise(self, report, result=None, returncode=0):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = str(Path(tmp).resolve())
            output = Path(tmp) / "review.json"

            def process(command, **kwargs):
                self.assertEqual(command[:2], [f"{tmp}/.venv/bin/python", "-I"])
                self.assertTrue(command[2].endswith("/reviewer_cli.py"))
                self.assertEqual(kwargs["cwd"], Path(tmp))
                sidecar = Path(kwargs["env"]["PLUTO_REVIEW_INPUT_REPORT"])
                if report is not None:
                    sidecar.write_text(json.dumps(report))
                if result is not None:
                    output.write_text(json.dumps(result))
                return SimpleNamespace(returncode=returncode)

            with patch.dict(os.environ, {"PR_AGENT_SOURCE": tmp}), patch(
                "review.environment", return_value={}
            ), patch("review.subprocess.run", side_effect=process):
                return run("https://github.com/example/repo/pull/1", output)

    def test_model_cannot_replace_the_input_attestation(self):
        evidence = {"complete": True, "head": "verified immutable revision"}
        result = self.exercise(
            {"evidence": evidence},
            {"review": {}, "input_evidence": {"complete": "model supplied"}},
        )
        self.assertEqual(result["input_evidence"], evidence)

    def test_missing_or_invalid_input_never_produces_a_review(self):
        for report in (None, {}, {"input_error": "Incomplete input"}):
            with self.subTest(report=report), self.assertRaises(
                ReviewInputError
            ):
                self.exercise(report, {"review": {}})

    def test_partial_failure_cannot_publish_an_apparently_clean_result(self):
        for report, code in (
            ({"evidence": {}, "errors": ["The model provider timed out"]}, 0),
            ({"evidence": {}}, 1),
        ):
            with self.subTest(report=report), self.assertRaises(
                ReviewExecutionError
            ):
                self.exercise(report, {"review": {}}, code)

    def test_successful_process_without_review_output_is_rejected(self):
        with self.assertRaises(ReviewExecutionError):
            self.exercise({"evidence": {}})


if __name__ == "__main__":
    unittest.main()
