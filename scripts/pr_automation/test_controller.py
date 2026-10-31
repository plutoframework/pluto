"""Exercise merge authorization independently of model and network calls."""

import copy
import json
import os
import unittest
from unittest.mock import patch

from controller import process, previous_result
from github import GitHubError
from policy import CHECK_SCHEMA, REVIEW_CHECK
from test_policy import CLEAN


class FakeAPI:
    def __init__(self):
        self.pr = {
            "number": 7,
            "node_id": "pr-node",
            "state": "open",
            "draft": False,
            "head": {"sha": "head"},
            "base": {
                "sha": "base",
                "ref": "main",
                "repo": {"full_name": "plutoframework/pluto-core"},
            },
            "user": {"login": "author"},
            "html_url": "https://github.com/plutoframework/pluto-core/pull/7",
            "changed_files": 1,
            "mergeable": True,
        }
        self.files = [{"filename": "pagination/page.go", "patch": "@@ code"}]
        self.reviews = []
        self.checks = []
        self.merged = False
        self.auto_enabled = False
        self.merge_status = None
        self.runs_succeed = True
        self.race = False
        self.reads = 0

    def request(self, path, data=None, method=None):
        if path.endswith("/pulls/7"):
            self.reads += 1
            value = copy.deepcopy(self.pr)
            if self.race and self.reads > 1:
                value["head"]["sha"] = "new-head"
            return value
        if path.endswith("/permission"):
            return {"permission": "admin"}
        if path.endswith("/check-runs"):
            self.checks.append(data)
            return {"id": len(self.checks)}
        if path.endswith("/reviews"):
            if self.pr["user"]["login"] == "bot":
                raise AssertionError("Steward cannot approve its own PR")
            self.reviews.append(
                {
                    "id": 100 + len(self.reviews),
                    "user": {"login": "bot", "type": "User"},
                    "state": {
                        "APPROVE": "APPROVED",
                        "REQUEST_CHANGES": "CHANGES_REQUESTED",
                    }[data["event"]],
                    "commit_id": data["commit_id"],
                }
            )
            return {}
        if path.endswith("/merge"):
            assert method == "PUT" and data["sha"] == self.pr["head"]["sha"]
            if self.merge_status:
                raise GitHubError(self.merge_status, "PUT")
            self.merged = True
            return {"merged": True}
        if path == "/graphql":
            assert data["variables"]["head"] == self.pr["head"]["sha"]
            self.auto_enabled = True
            return {
                "data": {
                    "enablePullRequestAutoMerge": {"pullRequest": {"number": 7}}
                }
            }
        raise AssertionError(path)

    def pages(self, path, key=None):
        if "/files" in path:
            return self.files
        if "/reviews" in path:
            return self.reviews
        if "/check-runs" in path:
            return [
                {**copy.deepcopy(c), "id": i + 1, "app": {"id": 123}}
                for i, c in enumerate(self.checks)
            ]
        if "/actions/workflows/" in path:
            file = path.split("/actions/workflows/")[1].split("/")[0]
            return [
                {
                    "id": 1,
                    "event": "pull_request",
                    "head_sha": self.pr["head"]["sha"],
                    "path": ".github/workflows/" + file,
                    "repository": {"full_name": "plutoframework/pluto-core"},
                    "conclusion": "success" if self.runs_succeed else "failure",
                }
            ]
        raise AssertionError(path)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "PLUTO_REVIEW_APP_ID": "123",
                "POLICY_SHA": "policy",
                "ENABLE_AUTO_MERGE": "true",
                "BOT_LOGIN": "bot",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.api = FakeAPI()

    def run_gate(self, review=None):
        with patch("controller.run_review", return_value=review or CLEAN):
            process(self.api, "pluto-core", 7)

    def test_clean_routine_change_merges(self):
        self.run_gate()
        self.assertTrue(self.api.merged)

    def test_pending_native_checks_queue_auto_merge(self):
        self.api.merge_status = 405
        self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertTrue(self.api.auto_enabled)

    def test_racing_merge_precondition_never_queues_new_head(self):
        self.api.merge_status = 409
        with self.assertRaises(GitHubError):
            self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertEqual(self.api.checks[-1]["conclusion"], "action_required")

    def test_permission_failure_does_not_attempt_another_merge_route(self):
        self.api.merge_status = 403
        with self.assertRaises(GitHubError):
            self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)

    def test_sensitive_file_does_not_merge_without_human(self):
        self.api.files = [{"filename": "crypto.go"}]
        self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertEqual(self.api.checks[-1]["conclusion"], "action_required")

    def test_sensitive_change_with_current_human_approval_merges(self):
        self.api.files = [{"filename": "crypto.go"}]
        self.api.reviews = [
            {
                "id": 1,
                "user": {"login": "owner", "type": "User"},
                "state": "APPROVED",
                "commit_id": "head",
            }
        ]
        result = copy.deepcopy(CLEAN)
        result["review"]["risk_level"] = "high"
        result["review"]["merge_recommendation"] = "needs_review"
        self.run_gate(result)
        self.assertTrue(self.api.merged)

    def test_steward_account_approval_never_counts_as_human_approval(self):
        self.api.files = [{"filename": "crypto.go", "patch": "@@ code"}]
        self.api.reviews = [
            {
                "id": 1,
                "user": {"login": "bot", "type": "User"},
                "state": "APPROVED",
                "commit_id": "head",
            }
        ]
        self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertIn(
            "Maintainer approval required",
            self.api.checks[-1]["output"]["summary"],
        )

    def test_human_change_request_remains_blocking(self):
        self.api.reviews = [
            {
                "id": 1,
                "user": {"login": "owner", "type": "User"},
                "state": "CHANGES_REQUESTED",
                "commit_id": "head",
            }
        ]
        self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)

    def test_racing_push_does_not_merge(self):
        self.api.race = True
        self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)

    def test_steward_authored_pr_is_rejected_even_with_human_approval(self):
        self.api.pr["user"]["login"] = "bot"
        self.api.reviews = [
            {
                "id": 1,
                "user": {"login": "owner", "type": "User"},
                "state": "APPROVED",
                "commit_id": "head",
            }
        ]
        with patch("controller.run_review") as model:
            process(self.api, "pluto-core", 7)
        model.assert_not_called()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertEqual(len(self.api.reviews), 1)

    def test_steward_authored_pr_never_spends_review_tokens(self):
        self.api.pr["user"]["login"] = "bot"
        with patch("controller.run_review") as model:
            process(self.api, "pluto-core", 7)
        model.assert_not_called()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertEqual(self.api.reviews, [])

    def test_failed_ci_or_review_does_not_merge(self):
        self.api.runs_succeed = False
        self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        with patch(
            "controller.run_review", side_effect=RuntimeError("private failure")
        ):
            process(self.api, "pluto-core", 7, fresh=True)
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertEqual(self.api.checks[-1]["conclusion"], "failure")
        self.assertNotIn("private failure", json.dumps(self.api.checks))

    def test_ci_completion_reuses_successful_review(self):
        self.api.runs_succeed = False
        self.run_gate()
        self.api.runs_succeed = True
        with patch("controller.run_review") as model:
            process(self.api, "pluto-core", 7)
        model.assert_not_called()
        self.assertTrue(self.api.merged)

    def test_failed_or_interrupted_review_is_not_automatically_retried(self):
        with patch("controller.run_review", side_effect=RuntimeError):
            process(self.api, "pluto-core", 7)
        for conclusion in ["failure", "action_required"]:
            with self.subTest(conclusion=conclusion):
                review_check = next(
                    c for c in self.api.checks if c["name"] == REVIEW_CHECK
                )
                self.api.checks = [review_check]
                review_check["conclusion"] = conclusion
                with patch("controller.run_review") as model:
                    process(self.api, "pluto-core", 7)
                model.assert_not_called()
                self.assertFalse(self.api.merged)
                self.assertFalse(self.api.auto_enabled)

    def test_explicit_retry_can_recover_failed_model_request(self):
        with patch("controller.run_review", side_effect=RuntimeError):
            process(self.api, "pluto-core", 7)
        with patch("controller.run_review", return_value=CLEAN) as model:
            process(self.api, "pluto-core", 7, fresh=True)
        model.assert_called_once()
        self.assertTrue(self.api.merged)

    def test_changed_revision_gets_one_new_review(self):
        self.api.runs_succeed = False
        self.run_gate()
        self.api.pr["base"]["sha"] = "new-base"
        with patch("controller.run_review", return_value=CLEAN) as model:
            process(self.api, "pluto-core", 7)
            process(self.api, "pluto-core", 7)
        model.assert_called_once()
        self.assertFalse(self.api.merged)

    def test_findings_request_changes_once_and_fixed_head_can_be_approved(self):
        result = copy.deepcopy(CLEAN)
        result["review"]["key_issues_to_review"] = [
            {"issue_content": "Incorrect result for an empty page"}
        ]
        result["review"]["merge_recommendation"] = "changes_required"
        self.run_gate(result)
        self.assertEqual(self.api.reviews[-1]["state"], "CHANGES_REQUESTED")
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        with patch("controller.run_review") as model:
            process(self.api, "pluto-core", 7)
        model.assert_not_called()
        self.assertEqual(len(self.api.reviews), 1)
        self.api.pr["head"]["sha"] = "fixed-head"
        with patch("controller.run_review", return_value=CLEAN):
            process(self.api, "pluto-core", 7)
        self.assertEqual(self.api.reviews[-1]["state"], "APPROVED")
        self.assertTrue(self.api.merged)

    def test_incomplete_inventory_cannot_merge(self):
        self.api.pr["changed_files"] = 2
        with self.assertRaises(ValueError):
            self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertEqual(self.api.checks[-1]["conclusion"], "action_required")

    def test_review_only_mode_never_merges(self):
        with patch.dict(os.environ, {"ENABLE_AUTO_MERGE": "false"}):
            self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)

    def test_spoofed_stale_or_wrong_base_review_is_not_reused(self):
        evidence = {
            "schema": CHECK_SCHEMA,
            "repository": "plutoframework/pluto-core",
            "number": 7,
            "head": "head",
            "base": "base",
            "policy": "policy",
            "reviewer": "bot",
            "result": CLEAN,
        }
        check = {
            "id": 1,
            "name": REVIEW_CHECK,
            "app": {"id": 123},
            "conclusion": "success",
            "output": {"text": json.dumps(evidence)},
        }
        with patch.object(self.api, "pages", return_value=[check]):
            self.assertEqual(
                previous_result(
                    self.api,
                    "/repos/plutoframework/pluto-core",
                    self.api.pr,
                    123,
                ),
                CLEAN,
            )
        for mutation in ["app", "head", "base", "policy", "reviewer"]:
            item, value = copy.deepcopy(check), copy.deepcopy(evidence)
            if mutation == "app":
                item["app"]["id"] = 456
            else:
                value[mutation] = "wrong"
                item["output"]["text"] = json.dumps(value)
            with patch.object(self.api, "pages", return_value=[item]):
                self.assertIsNone(
                    previous_result(
                        self.api,
                        "/repos/plutoframework/pluto-core",
                        self.api.pr,
                        123,
                    )
                )


if __name__ == "__main__":
    unittest.main()
