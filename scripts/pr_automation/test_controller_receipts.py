"""Merge-race receipts and historical audits cannot authorize another write."""

import copy
import json
import os
import unittest
from unittest.mock import patch

from controller import (
    AUDIT_APP_ID,
    HISTORICAL_AUDIT_POLICY,
    process,
    audit_merged,
    workflows_ready,
)
from github import GitHubError
from policy import CHECK_SCHEMA, GATE_CHECK, REVIEW_CHECK
from test_controller import BASE, HEAD, FakeAPI, with_input
from test_policy import CLEAN


class ReceiptAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.receipt = {
            "sha": "merge", "tree": {"sha": "tree"},
            "parents": [{"sha": BASE, "url": "parent-metadata"}],
        }
        self.source = {"sha": HEAD, "tree": {"sha": "tree"}}
        self.history = []
        self.on_merge = None
        self.on_queue = None
        self.merge_calls = 0
        self.queue_calls = 0
        self.push = None

    def close(self):
        self.pr.update(
            state="closed", merged=True, merge_commit_sha="merge",
            merged_by={"login": "bot"},
        )

    def request(self, path, data=None, method=None):
        if "/git/commits/" in path:
            return copy.deepcopy(
                self.source if path.endswith("/" + HEAD) else self.receipt
            )
        if path.endswith("/merge"):
            self.merge_calls += 1
            if self.on_merge:
                self.on_merge()
        if path == "/graphql":
            self.queue_calls += 1
            if self.on_queue:
                return self.on_queue()
        return super().request(path, data, method)

    def pages(self, path, key=None):
        if "/check-runs" in path and self.history:
            return copy.deepcopy(self.history)
        result = super().pages(path, key)
        if "/actions/workflows/" in path and self.push is not None:
            result.append({**result[0], "event": "push", "id": 2,
                           "conclusion": self.push})
        return result


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "PLUTO_REVIEW_APP_ID": str(AUDIT_APP_ID),
            "POLICY_SHA": "current-policy", "ENABLE_AUTO_MERGE": "true",
            "BOT_LOGIN": "bot",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.api = ReceiptAPI()

    def run_open(self):
        with patch("controller.run_review",
                   return_value=with_input(self.api, CLEAN)):
            process(self.api, "pluto-core", 7)

    def historical(self):
        self.api.close()
        self.api.reviews = [{
            "id": 1, "user": {"login": "bot", "type": "User"},
            "state": "APPROVED", "commit_id": HEAD,
        }]
        evidence = {
            "schema": CHECK_SCHEMA, "repository": "plutoframework/pluto-core",
            "number": 7, "head": HEAD, "base": BASE,
            "policy": HISTORICAL_AUDIT_POLICY, "reviewer": "bot",
            "result": copy.deepcopy(CLEAN),
        }
        self.api.history = [{
            "id": 1, "name": REVIEW_CHECK, "app": {"id": AUDIT_APP_ID},
            "head_sha": HEAD,
            "status": "completed", "conclusion": "success",
            "output": {"text": json.dumps(evidence)},
        }]
        return evidence

    def test_rest_error_after_same_head_merge_is_idempotent(self):
        self.api.on_merge = self.api.close
        self.api.merge_status = 409
        self.run_open()
        self.assertEqual(self.api.merge_calls, 1)
        self.assertEqual(self.api.queue_calls, 0)
        self.assertEqual(self.api.checks[-1]["conclusion"], "success")

    def test_graphql_error_after_same_head_merge_is_idempotent(self):
        self.api.merge_status = 405
        def queue():
            self.api.close()
            return {"errors": [{"message": "already merged"}]}
        self.api.on_queue = queue
        self.run_open()
        self.assertEqual(self.api.merge_calls, 1)
        self.assertEqual(self.api.queue_calls, 1)
        self.assertEqual(self.api.checks[-1]["conclusion"], "success")

    def test_graphql_http_error_after_same_head_merge_is_idempotent(self):
        self.api.merge_status = 405
        def queue():
            self.api.close()
            raise GitHubError(403, "POST")
        self.api.on_queue = queue
        self.run_open()
        self.assertEqual(self.api.merge_calls, 1)
        self.assertEqual(self.api.queue_calls, 1)
        self.assertEqual(self.api.checks[-1]["conclusion"], "success")

    def test_race_receipt_does_not_accept_open_changed_or_unproven_merge(self):
        for mutation in ("open", "head", "tree", "parent", "actor", "missing"):
            with self.subTest(mutation=mutation):
                self.api = ReceiptAPI()
                self.api.merge_status = 405
                def queue():
                    self.api.close()
                    if mutation == "open":
                        self.api.pr["state"] = "open"
                    elif mutation == "head":
                        self.api.pr["head"]["sha"] = "new-head"
                    elif mutation == "tree":
                        self.api.receipt["tree"]["sha"] = "other"
                    elif mutation == "parent":
                        self.api.receipt["parents"] = [{"sha": "other"}]
                    elif mutation == "actor":
                        self.api.pr["merged_by"]["login"] = "admin"
                    else:
                        self.api.pr["merge_commit_sha"] = None
                    return {"errors": [{"message": "rejected"}]}
                self.api.on_queue = queue
                with self.assertRaises(RuntimeError):
                    self.run_open()
                self.assertEqual(self.api.checks[-1]["conclusion"],
                                 "action_required")

    def test_historical_audit_repairs_only_existing_verified_merge(self):
        self.historical()
        with patch("controller.run_review") as model:
            process(self.api, "pluto-core", 7)
        model.assert_not_called()
        self.assertEqual(self.api.merge_calls, 0)
        self.assertEqual(self.api.queue_calls, 0)
        self.assertEqual(len(self.api.checks), 1)
        self.assertEqual(self.api.checks[0]["name"], GATE_CHECK)
        self.assertEqual(self.api.checks[0]["conclusion"], "success")

    def test_current_policy_audit_requires_bound_input_attestation(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                self.api = ReceiptAPI()
                evidence = self.historical()
                evidence["policy"] = "current-policy"
                evidence["result"] = with_input(self.api, CLEAN)
                if not valid:
                    evidence["result"].pop("input_evidence")
                self.api.history[0]["output"]["text"] = json.dumps(evidence)
                self.api.pr["base"]["sha"] = "c" * 40
                with patch("controller.run_review") as model:
                    process(self.api, "pluto-core", 7)
                model.assert_not_called()
                self.assertEqual(len(self.api.checks), int(valid))
                self.assertEqual(self.api.merge_calls, 0)
                self.assertEqual(self.api.queue_calls, 0)

    def test_historical_audit_never_authorizes_an_open_pr(self):
        self.historical()
        self.api.pr["state"] = "open"
        audit_merged(self.api, "/repos/plutoframework/pluto-core",
                     self.api.pr, "pluto-core")
        self.assertEqual(self.api.checks, [])
        self.assertEqual(self.api.merge_calls, 0)
        self.assertEqual(self.api.queue_calls, 0)

    def test_historical_audit_bypasses_never_repair(self):
        for mutation in (
            "app", "policy", "head", "coverage", "finding", "approval",
            "reviewer", "actor", "tree", "parent", "ci", "missing_patch",
            "receipt", "author", "state", "review_status", "check_head",
        ):
            with self.subTest(mutation=mutation):
                self.api = ReceiptAPI()
                evidence = self.historical()
                if mutation == "app":
                    self.api.history[0]["app"]["id"] = 99
                elif mutation in {"policy", "head", "reviewer"}:
                    evidence[mutation] = "other"
                elif mutation == "coverage":
                    evidence["result"]["coverage"]["files_not_reviewed"] = ["x"]
                elif mutation == "finding":
                    evidence["result"]["review"]["key_issues_to_review"] = [{}]
                elif mutation == "approval":
                    self.api.reviews[0]["commit_id"] = "other"
                elif mutation == "actor":
                    self.api.pr["merged_by"]["login"] = "admin"
                elif mutation == "tree":
                    self.api.receipt["tree"]["sha"] = "other"
                elif mutation == "parent":
                    self.api.receipt["parents"] = [{"sha": "other"}]
                elif mutation == "ci":
                    self.api.push = "failure"
                elif mutation == "missing_patch":
                    self.api.files[0].pop("patch")
                elif mutation == "receipt":
                    self.api.pr["merge_commit_sha"] = None
                elif mutation == "author":
                    self.api.pr["user"]["login"] = "bot"
                elif mutation == "state":
                    self.api.pr["merged"] = False
                elif mutation == "review_status":
                    self.api.history[0]["status"] = "in_progress"
                elif mutation == "check_head":
                    self.api.history[0]["head_sha"] = "other"
                self.api.history[0]["output"]["text"] = json.dumps(evidence)
                with patch("controller.run_review") as model:
                    process(self.api, "pluto-core", 7)
                model.assert_not_called()
                self.assertEqual(self.api.checks, [])
                self.assertEqual(self.api.merge_calls, 0)
                self.assertEqual(self.api.queue_calls, 0)

    def test_duplicate_failed_or_pending_push_blocks_even_with_green_pr(self):
        for state in ("failure", None, "cancelled"):
            self.api.push = state
            if state is None:
                # Keep canonical PR CI green so only the queued push blocks.
                original = self.api.pages
                def runs(path, key=None):
                    values = original(path, key)
                    return values + [{
                        **values[0], "id": 2, "event": "push",
                        "status": "queued", "conclusion": None,
                    }]
                with patch.object(self.api, "pages", side_effect=runs):
                    blockers = workflows_ready(
                        self.api, "/repos/plutoframework/pluto-core",
                        self.api.pr, "pluto-core")
                    self.assertTrue(blockers)
                    self.assertTrue(
                        all("push" in reason for reason in blockers)
                    )
            else:
                self.assertTrue(workflows_ready(
                    self.api, "/repos/plutoframework/pluto-core",
                    self.api.pr, "pluto-core"))


class WorkflowIdentityTests(unittest.TestCase):
    def test_newest_failure_or_wrong_identity_cannot_use_older_success(self):
        api = FakeAPI()
        original = api.pages
        for mutation in ("latest_failure", "no_pr", "head", "repo", "path"):
            def runs(path, key=None):
                values = original(path, key)
                latest = {**values[0], "id": 2}
                if mutation == "latest_failure":
                    latest["conclusion"] = "failure"
                    return [values[0], latest]
                if mutation == "no_pr":
                    latest["event"] = "push"
                elif mutation == "head":
                    latest["head_sha"] = "other"
                elif mutation == "repo":
                    latest["repository"] = {"full_name": "other/repo"}
                elif mutation == "path":
                    latest["path"] = ".github/workflows/unrelated.yml"
                return [latest]
            with self.subTest(mutation=mutation), patch.object(
                api, "pages", side_effect=runs
            ):
                self.assertTrue(workflows_ready(
                    api, "/repos/plutoframework/pluto-core", api.pr,
                    "pluto-core",
                ))


if __name__ == "__main__":
    unittest.main()
