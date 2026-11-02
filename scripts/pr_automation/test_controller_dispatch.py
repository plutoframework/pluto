"""A resolved queue cannot review another PR or a later head."""

import copy
import json
import os
import unittest
from unittest.mock import patch

from controller import main, process, resolved_target
from test_controller import BASE, HEAD, FakeAPI, with_input
from test_policy import CLEAN


class ResolvedQueueTests(unittest.TestCase):
    def test_stale_queue_never_starts_review_or_changes_checks(self):
        api = FakeAPI()
        with patch("controller.run_review") as review:
            process(api, "pluto-core", 7, expected_head="d" * 40)
        review.assert_not_called()
        self.assertEqual(api.checks, [])
        self.assertEqual(api.reviews, [])
        self.assertFalse(api.merged)
        self.assertFalse(api.auto_enabled)

    def test_current_queue_retains_all_existing_merge_gates(self):
        api = FakeAPI()
        with patch.dict(os.environ, {
            "BOT_LOGIN": "bot", "POLICY_SHA": "policy",
            "PLUTO_REVIEW_APP_ID": "123", "ENABLE_AUTO_MERGE": "true",
        }), patch("controller.run_review", return_value=with_input(api, CLEAN)):
            process(api, "pluto-core", 7, expected_head=HEAD)
        self.assertTrue(api.merged)

    def target(self, name, event, number="7", head=HEAD):
        with patch.dict(os.environ, {
            "RESOLVED_PR_NUMBER": number, "RESOLVED_HEAD_SHA": head
        }):
            return resolved_target(name, event)

    def test_all_event_types_bind_to_the_resolved_queue(self):
        for name, event in (
            ("pull_request_target", {
                "pull_request": {"number": 7, "head": {"sha": HEAD}}
            }),
            ("workflow_run", {"workflow_run": {"head_sha": HEAD}}),
            ("workflow_dispatch", {"inputs": {"pr_number": "7"}}),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.target(name, event), (7, HEAD))

    def test_matrix_job_passes_immutable_queue_identity_to_the_controller(self):
        for retry, fresh in (("false", False), ("true", True)):
            event = {"inputs": {"pr_number": "7", "retry_review": retry}}
            with self.subTest(retry=retry), patch.dict(os.environ, {
                "TARGET_REPOSITORY": "plutoframework/pluto-core",
                "BOT_TOKEN": "synthetic", "CHECK_TOKEN": "synthetic",
                "BOT_LOGIN": "bot", "GITHUB_EVENT_PATH": "unused.json",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "RESOLVED_PR_NUMBER": "7", "RESOLVED_HEAD_SHA": HEAD,
            }, clear=True), patch("controller.GitHub") as factory, patch(
                "controller.Path.read_text", return_value=json.dumps(event)
            ), patch("controller.process") as handle:
                factory.return_value.request.return_value = {"login": "bot"}
                main()
                handle.assert_called_once_with(
                    factory.return_value, "pluto-core", 7,
                    fresh=fresh, expected_head=HEAD,
                )

    def test_mismatched_payloads_and_malformed_queues_are_rejected(self):
        cases = (
            ("pull_request_target", {
                "pull_request": {"number": 8, "head": {"sha": HEAD}}
            }, "7", HEAD),
            ("pull_request_target", {
                "pull_request": {"number": 7, "head": {"sha": BASE}}
            }, "7", HEAD),
            ("workflow_run", {"workflow_run": {"head_sha": BASE}}, "7", HEAD),
            ("workflow_dispatch", {"inputs": {"pr_number": "8"}}, "7", HEAD),
            ("unsupported", {}, "7", HEAD),
            ("workflow_dispatch", {}, "0", HEAD),
            ("workflow_dispatch", {}, "-1", HEAD),
            ("workflow_dispatch", {}, "07", HEAD),
            ("workflow_dispatch", {}, "7", "not-a-commit"),
            ("workflow_dispatch", {}, "7", ""),
        )
        for name, event, number, head in cases:
            with self.subTest(name=name, number=number, head=head):
                with self.assertRaises(ValueError):
                    self.target(name, copy.deepcopy(event), number, head)

    def test_legacy_entrypoints_keep_their_protected_event_handling(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(resolved_target("workflow_run", {}))

