"""Verify the reviewer cannot acquire contributor capabilities over its client."""

import unittest
import os
from unittest.mock import patch

from github import GitHub
from review import environment


class ReviewerBoundaryTests(unittest.TestCase):
    def test_check_signing_and_account_credentials_are_separate(self):
        api = GitHub("reviewer-token", "app-token")
        prefix = "/repos/plutoframework/pluto"
        with patch("github.urllib.request.urlopen") as network:
            network.return_value.__enter__.return_value.read.return_value = (
                b"{}"
            )
            api.request(prefix + "/check-runs", {"name": "gate"})
            self.assertEqual(
                network.call_args.args[0].get_header("Authorization"),
                "Bearer app-token",
            )
            api.request(prefix + "/pulls/7/reviews", {"event": "APPROVE"})
            self.assertEqual(
                network.call_args.args[0].get_header("Authorization"),
                "Bearer reviewer-token",
            )

    def test_check_write_without_app_credential_is_rejected(self):
        with patch("github.urllib.request.urlopen") as network:
            with self.assertRaises(ValueError):
                GitHub("reviewer-token").request(
                    "/repos/plutoframework/pluto/check-runs", {}
                )
            network.assert_not_called()

    def test_model_process_does_not_receive_check_signing_credential(self):
        with patch.dict(
            os.environ,
            {
                "GPUROUTER_API_KEY": "provider-key",
                "REVIEW_TOKEN": "reviewer-token",
                "BOT_TOKEN": "reviewer-token",
                "BOT_LOGIN": "jackbot-advrsty",
                "CHECK_TOKEN": "app-token",
                "GITHUB_TOKEN": "actions-token",
            },
        ):
            env = environment()
        for key in ["CHECK_TOKEN", "BOT_TOKEN", "GITHUB_TOKEN"]:
            self.assertNotIn(key, env)
        self.assertEqual(env["GITHUB__USER_TOKEN"], "reviewer-token")
        self.assertEqual(env["GITHUB__AGENT_LOGIN"], "jackbot-advrsty")

    def test_source_and_pr_creation_are_rejected_before_network_access(self):
        api = GitHub("test-token")
        prefix = "/repos/plutoframework/pluto"
        attempts = [
            (prefix + "/pulls", {"head": "feature"}, "POST"),
            (prefix + "/git/commits", {"tree": "tree"}, "POST"),
            (prefix + "/git/refs", {"ref": "refs/heads/feature"}, "POST"),
            (prefix + "/git/refs/heads/main", {"sha": "head"}, "PATCH"),
            (prefix + "/contents/file.go", {"content": "code"}, "PUT"),
            (
                "/graphql",
                {
                    "query": "mutation { createPullRequest(input: {}) { clientMutationId } }"
                },
                "POST",
            ),
        ]
        with patch("github.urllib.request.urlopen") as network:
            for path, data, method in attempts:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    api.request(path, data, method=method)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
