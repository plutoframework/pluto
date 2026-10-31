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
                raise AssertionError("The App cannot approve its own PR")
            self.reviews.append(
                {
                    "id": 10,
                    "user": {"login": "bot", "type": "Bot"},
                    "state": "APPROVED",
                    "commit_id": data["commit_id"],
                }
            )
            return {}
        if path.endswith("/merge"):
            assert method == "PUT" and data["sha"] == "head"
            if self.merge_status:
                raise GitHubError(self.merge_status, "PUT")
            self.merged = True
            return {"merged": True}
        if path == "/graphql":
            assert data["variables"]["head"] == "head"
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
            return []
        if "/actions/workflows/" in path:
            file = path.split("/actions/workflows/")[1].split("/")[0]
            return [
                {
                    "id": 1,
                    "event": "pull_request",
                    "head_sha": "head",
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

    def test_racing_push_does_not_merge(self):
        self.api.race = True
        self.run_gate()
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)

    def test_app_submission_merges_with_independent_approval(self):
        self.api.pr["user"]["login"] = "bot"
        self.api.reviews = [
            {
                "id": 1,
                "user": {"login": "owner", "type": "User"},
                "state": "APPROVED",
                "commit_id": "head",
            }
        ]
        self.run_gate()
        self.assertTrue(self.api.merged)
        self.assertEqual(len(self.api.reviews), 1)

    def test_app_submission_without_independent_approval_stays_blocked(self):
        self.api.pr["user"]["login"] = "bot"
        self.run_gate()
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
            process(self.api, "pluto-core", 7)
        self.assertFalse(self.api.merged)
        self.assertFalse(self.api.auto_enabled)
        self.assertEqual(self.api.checks[-1]["conclusion"], "failure")
        self.assertNotIn("private failure", json.dumps(self.api.checks))

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
            "result": CLEAN,
        }
        check = {
            "id": 1,
            "name": REVIEW_CHECK,
            "app": {"id": 123},
            "conclusion": "success",
            "output": {"text": json.dumps(evidence)},
        }
        for mutation in ["app", "head", "base", "policy"]:
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
