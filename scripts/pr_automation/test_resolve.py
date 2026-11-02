"""Read-only target discovery and independent per-PR queues."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resolve import ReadOnlyGitHub, main, resolve_targets
from rollout import ROOT, entrypoint

REPOSITORY = "plutoframework/pluto"
HEAD, NEXT = "a" * 40, "b" * 40


def pr(number, head=HEAD, state="open"):
    return {
        "number": number,
        "state": state,
        "head": {"sha": head},
        "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
    }


class API:
    def __init__(self):
        self.current = {7: pr(7), 8: pr(8), 9: pr(9, NEXT)}
        self.listed = list(self.current.values())
        self.reads = []

    def request(self, path):
        self.reads.append(path)
        return copy.deepcopy(self.current[int(path.rsplit("/", 1)[1])])

    def pages(self, path):
        self.reads.append(path)
        return copy.deepcopy(self.listed)


class ResolveTests(unittest.TestCase):
    def test_workflow_payload_empty_or_partial_lists_do_not_starve_prs(self):
        for payload in ([], [{"number": 7}], [{"number": 99}]):
            api = API()
            event = {
                "workflow_run": {"head_sha": HEAD, "pull_requests": payload}
            }
            self.assertEqual(
                resolve_targets(api, REPOSITORY, "workflow_run", event),
                [{"number": 7, "head": HEAD}, {"number": 8, "head": HEAD}],
            )
            self.assertIn("/repos/" + REPOSITORY + "/pulls/8", api.reads)

    def test_push_completion_resolves_existing_main_prs(self):
        api = API()
        event = {"workflow_run": {"event": "push", "head_sha": HEAD}}
        self.assertEqual(
            resolve_targets(api, REPOSITORY, "workflow_run", event),
            [{"number": 7, "head": HEAD}, {"number": 8, "head": HEAD}],
        )
        self.assertIn("?state=open&base=main", api.reads[0])

    def test_non_main_prs_never_reach_privileged_jobs(self):
        api = API()
        api.listed[0]["base"]["ref"] = "development"
        result = resolve_targets(
            api,
            REPOSITORY,
            "workflow_run",
            {"workflow_run": {"head_sha": HEAD}},
        )
        self.assertEqual(result, [{"number": 8, "head": HEAD}])
        self.assertNotIn("/repos/" + REPOSITORY + "/pulls/7", api.reads)
        api.current[8]["base"]["ref"] = "development"
        self.assertEqual(
            resolve_targets(
                api,
                REPOSITORY,
                "workflow_dispatch",
                {"inputs": {"pr_number": "8"}},
            ),
            [],
        )

    def test_current_head_and_open_state_override_list_hints(self):
        api = API()
        api.current[7] = pr(7, NEXT)
        api.current[8] = pr(8, state="closed")
        self.assertEqual(
            resolve_targets(
                api,
                REPOSITORY,
                "workflow_run",
                {"workflow_run": {"head_sha": HEAD}},
            ),
            [],
        )
        api.listed = []
        self.assertEqual(
            resolve_targets(
                api,
                REPOSITORY,
                "workflow_run",
                {"workflow_run": {"head_sha": HEAD}},
            ),
            [],
        )

    def test_pull_request_event_is_bound_to_current_number_and_head(self):
        api = API()
        event = {"number": 7, "pull_request": pr(7)}
        self.assertEqual(
            resolve_targets(api, REPOSITORY, "pull_request_target", event),
            [{"number": 7, "head": HEAD}],
        )
        api.current[7] = pr(7, NEXT)
        self.assertEqual(
            resolve_targets(api, REPOSITORY, "pull_request_target", event), []
        )

    def test_dispatch_allows_closed_audit_and_uses_current_head(self):
        api = API()
        api.current[7] = pr(7, NEXT, state="closed")
        event = {"inputs": {"pr_number": "7"}}
        self.assertEqual(
            resolve_targets(api, REPOSITORY, "workflow_dispatch", event),
            [{"number": 7, "head": NEXT}],
        )

    def test_invalid_repository_numbers_heads_and_remote_identity_fail(self):
        invalid = [
            (
                "other/pluto",
                "workflow_dispatch",
                {"inputs": {"pr_number": "7"}},
            ),
            (
                REPOSITORY,
                "workflow_dispatch",
                {"inputs": {"pr_number": "7;evil"}},
            ),
            (REPOSITORY, "workflow_dispatch", {"inputs": {"pr_number": True}}),
            (REPOSITORY, "workflow_run", {"workflow_run": {"head_sha": "bad"}}),
            (REPOSITORY, "push", {}),
            (
                REPOSITORY,
                "pull_request_target",
                {"number": 8, "pull_request": pr(7)},
            ),
            (
                REPOSITORY,
                "workflow_dispatch",
                {
                    "repository": {"full_name": "other/repo"},
                    "inputs": {"pr_number": "7"},
                },
            ),
        ]
        for repository, event, payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                resolve_targets(API(), repository, event, payload)
        api = API()
        api.current[7]["base"]["repo"]["full_name"] = "other/repo"
        with self.assertRaises(ValueError):
            resolve_targets(
                api,
                REPOSITORY,
                "workflow_dispatch",
                {"inputs": {"pr_number": "7"}},
            )

    def test_distinct_prs_and_separate_heads_have_distinct_matrix_keys(self):
        api = API()
        first = resolve_targets(
            api,
            REPOSITORY,
            "workflow_run",
            {"workflow_run": {"head_sha": HEAD}},
        )
        second = resolve_targets(
            api,
            REPOSITORY,
            "workflow_run",
            {"workflow_run": {"head_sha": NEXT}},
        )
        keys = {
            f"{REPOSITORY}-{t['number']}-{t['head']}" for t in first + second
        }
        self.assertEqual(len(keys), 3)
        self.assertEqual(len({t["head"] for t in first}), 1)

    @patch("resolve.GitHub")
    def test_reader_can_only_issue_get_requests(self, client):
        reader = ReadOnlyGitHub("built-in-read-token")
        reader.request("/repos/plutoframework/pluto/pulls/7")
        client.assert_called_once_with("built-in-read-token")
        client.return_value.request.assert_called_once_with(
            "/repos/plutoframework/pluto/pulls/7", method="GET"
        )
        with self.assertRaises(TypeError):
            reader.request("/mutation", {"data": "forbidden"})

    def test_main_emits_typed_matrix_and_empty_indicator(self):
        for listed, expected in (([pr(7), pr(8)], True), ([], False)):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "event.json").write_text(
                    json.dumps({"workflow_run": {"head_sha": HEAD}})
                )
                api = API()
                api.listed = listed
                with patch.dict(
                    os.environ,
                    {
                        "GITHUB_REPOSITORY": REPOSITORY,
                        "GITHUB_EVENT_NAME": "workflow_run",
                        "GITHUB_EVENT_PATH": str(root / "event.json"),
                        "GITHUB_OUTPUT": str(root / "output"),
                        "GITHUB_TOKEN": "built-in-read-token",
                    },
                ), patch("resolve.ReadOnlyGitHub", return_value=api):
                    main()
                output = dict(
                    line.split("=", 1)
                    for line in (root / "output").read_text().splitlines()
                )
                self.assertEqual(output["has_targets"], str(expected).lower())
                self.assertEqual(
                    len(json.loads(output["matrix"])["include"]), len(listed)
                )

    def test_workflow_has_read_only_resolver_and_canonical_job_queue(self):
        generated = entrypoint("pluto", "f" * 40)
        self.assertNotIn("\nconcurrency:", generated)
        resolver, review = generated.split("  review:\n", 1)
        self.assertIn("pull-requests: read", resolver)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", resolver)
        self.assertNotIn("secrets.", resolver)
        self.assertNotIn("workflow_run.event", resolver)
        self.assertNotIn("    if:", resolver)
        self.assertNotIn("environment: pluto-review", resolver)
        self.assertNotIn("create-github-app-token", resolver)
        self.assertIn("needs: resolve", review)
        self.assertIn("if: needs.resolve.outputs.has_targets == 'true'", review)
        self.assertIn("fail-fast: false", review)
        self.assertIn(
            "${{ github.repository }}-${{ matrix.number }}-${{", review
        )
        self.assertIn("matrix.head }}", review)
        self.assertIn("RESOLVED_PR_NUMBER: ${{ matrix.number }}", review)
        self.assertIn("RESOLVED_HEAD_SHA: ${{ matrix.head }}", review)
        self.assertIn("environment: pluto-review", review)
        self.assertIn("ref: " + "f" * 40, resolver)
        self.assertIn("ref: " + "f" * 40, review)
        self.assertTrue((ROOT / ".github/pr-review-job.yml").is_file())


if __name__ == "__main__":
    unittest.main()
