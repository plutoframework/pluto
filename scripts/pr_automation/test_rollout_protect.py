"""Owner rollout and native protections retain independent review and CI."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from policy import GATE_CHECK, REVIEW_CHECK
from protect import apply, protection_payload
from rollout import codeowners


class OwnerRolloutTests(unittest.TestCase):
    @patch("rollout.subprocess.check_output", return_value="state/state.go\0")
    def test_sensitive_patterns_allow_either_independent_owner(self, _files):
        for repository in ("pluto", "pluto-core", "pluto-jobs"):
            with self.subTest(repository=repository):
                lines = codeowners(repository, Path(".")).splitlines()
                rules = [line for line in lines if not line.startswith("#")]
                self.assertTrue(rules)
                self.assertTrue(
                    any(line.startswith("/.github/") for line in rules)
                )
                for rule in rules:
                    self.assertEqual(
                        rule.split()[1:],
                        ["@DavidHoenisch", "@jackbot-advrsty"],
                    )

    def test_authentication_and_authorization_keep_whole_repo_ownership(self):
        for repository in ("pluto-auth", "pluto-rbac"):
            self.assertIn(
                "\n* @DavidHoenisch @jackbot-advrsty\n",
                codeowners(repository, Path(".")),
            )


class ProtectionPayloadTests(unittest.TestCase):
    def test_existing_ci_and_exact_issuers_survive_policy_update(self):
        existing = {
            "required_status_checks": {
                "checks": [
                    {"context": "test", "app_id": 123},
                    {"context": REVIEW_CHECK, "app_id": 999},
                ],
                "contexts": ["test", "legacy scan", GATE_CHECK],
            }
        }
        payload = protection_payload(456, existing)
        self.assertEqual(
            payload["required_status_checks"],
            {
                "strict": True,
                "checks": [
                    {"context": "test", "app_id": 123},
                    {"context": "legacy scan", "app_id": -1},
                    {"context": REVIEW_CHECK, "app_id": 456},
                    {"context": GATE_CHECK, "app_id": 456},
                ],
            },
        )
        self.assertEqual(
            existing["required_status_checks"]["checks"][1]["app_id"], 999
        )

    @patch("protect.subprocess.run")
    @patch("protect.subprocess.check_output", side_effect=["{}", "[]"])
    def test_apply_keeps_scanning_active_without_bypass(self, _read, write):
        apply("pluto-core", 456)
        self.assertEqual(write.call_count, 3)
        scanning = json.loads(write.call_args_list[-1].kwargs["input"])
        self.assertEqual(scanning["enforcement"], "active")
        self.assertEqual(scanning["bypass_actors"], [])
        self.assertEqual(
            scanning["rules"][0]["parameters"]["code_scanning_tools"],
            [{
                "tool": "CodeQL",
                "alerts_threshold": "errors",
                "security_alerts_threshold": "high_or_higher",
            }],
        )

    @patch("protect.subprocess.run")
    @patch("protect.subprocess.check_output")
    def test_first_apply_requires_explicit_unprotected(self, read, write):
        response = {"message": "Branch not protected", "status": "404"}
        read.side_effect = [
            subprocess.CalledProcessError(
                1, "gh api", output=json.dumps(response)
            ),
            "[]",
        ]
        apply("pluto-core", 456)
        self.assertEqual(write.call_count, 3)
        payload = json.loads(write.call_args_list[0].kwargs["input"])
        self.assertEqual(payload, protection_payload(456, {}))

    @patch("protect.subprocess.run")
    @patch("protect.subprocess.check_output")
    def test_unknown_errors_fail_before_any_write(self, read, write):
        responses = [
            {"message": "Not Found", "status": "404"},
            {"message": "Resource not accessible", "status": "403"},
            {"message": "Branch not protected", "status": "403"},
            {"message": "Branch not protected"},
            ["Branch not protected", "404"],
            "Branch not protected (HTTP 404)",
            None,
        ]
        bodies = [json.dumps(response) for response in responses]
        bodies.extend(["not JSON", "", None])
        for body in bodies:
            with self.subTest(body=body):
                error = subprocess.CalledProcessError(
                    1, "gh api", output=body
                )
                read.side_effect = error
                with self.assertRaises(subprocess.CalledProcessError):
                    apply("pluto-core", 456)
                write.assert_not_called()

    def test_native_independence_and_security_requirements_remain(self):
        payload = protection_payload(456, {})
        self.assertEqual(
            payload["required_pull_request_reviews"],
            {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True,
                "required_approving_review_count": 1,
                "require_last_push_approval": True,
            },
        )
        self.assertTrue(payload["enforce_admins"])
        self.assertTrue(payload["required_conversation_resolution"])
        self.assertTrue(payload["required_linear_history"])
        self.assertFalse(payload["allow_force_pushes"])
        self.assertFalse(payload["allow_deletions"])


if __name__ == "__main__":
    unittest.main()
