import copy
import json
import unittest

from github import GitHubError
from open_pr import submit


class SubmitAPI:
    def __init__(self):
        self.calls = []
        self.behind = False
        self.source_changes = False
        self.reads = 0
        self.colliding_ref = False
        self.ref = None
        self.prs = []
        self.checks = []
        self.commits = {}
        self.fail_pr_once = False
        self.fail_check = False
        self.source_sha = "source"
        self.tree = "proposed-tree"

    def pages(self, path, key=None):
        if "/check-runs?" in path:
            assert key == "check_runs"
            return self.checks
        return self.prs

    def request(self, path, data=None, method=None):
        if data is not None:
            self.calls.append((path, data, method))
            if path.endswith("/git/commits"):
                sha = "app-commit-" + str(len(self.commits) + 1)
                self.commits[sha] = {"tree": {"sha": data["tree"]}}
                return {"sha": sha}
            if path.endswith("/check-runs"):
                if self.fail_check:
                    raise GitHubError(503, "POST")
                self.checks.append(
                    {**copy.deepcopy(data), "app": {"slug": "pluto-pr-steward"}}
                )
            if path.endswith("/git/refs") or method == "PATCH":
                self.ref = data["sha"]
            if path.endswith("/pulls"):
                if self.fail_pr_once:
                    self.fail_pr_once = False
                    raise GitHubError(503, "POST")
                pr = {
                    "html_url": "https://github.com/plutoframework/pluto/pull/9",
                    "user": {"login": "pluto-pr-steward[bot]"},
                }
                self.prs.append(pr)
                return pr
            return {}
        if "/commits/feature" in path:
            self.reads += 1
            return {
                "sha": "changed"
                if self.source_changes and self.reads > 1
                else self.source_sha,
                "commit": {"tree": {"sha": self.tree}},
                "html_url": "https://github.com/plutoframework/pluto/commit/source",
            }
        if path.endswith("/branches/main"):
            return {"commit": {"sha": "base"}}
        if "/compare/" in path:
            return {
                "status": "ahead",
                "merge_base_commit": {
                    "sha": "old-base" if self.behind else "base"
                },
            }
        if "/git/ref/" in path:
            if self.colliding_ref:
                return {"object": {"sha": "someone-elses-branch"}}
            if self.ref:
                return {"object": {"sha": self.ref}}
            raise GitHubError(404, "GET")
        if "/git/commits/" in path:
            return self.commits[path.rsplit("/", 1)[1]]
        raise AssertionError(path)


class SubmissionTests(unittest.TestCase):
    def test_app_publishes_actual_proposed_tree_on_separate_branch(self):
        api = SubmitAPI()
        result = submit(api, "plutoframework/pluto", "feature", "Fix the issue")
        self.assertTrue(result["html_url"].endswith("/9"))
        commit = next(
            data for path, data, _ in api.calls if path.endswith("/git/commits")
        )
        self.assertEqual(commit["tree"], "proposed-tree")
        self.assertEqual(commit["parents"], ["base"])
        ref = next(
            data for path, data, _ in api.calls if path.endswith("/git/refs")
        )
        self.assertEqual(ref["ref"], "refs/heads/review/feature")
        self.assertFalse(any(method == "PATCH" for _, _, method in api.calls))
        check_index = next(
            i
            for i, (path, _, _) in enumerate(api.calls)
            if path.endswith("/check-runs")
        )
        ref_index = next(
            i
            for i, (path, _, _) in enumerate(api.calls)
            if path.endswith("/git/refs")
        )
        self.assertLess(check_index, ref_index)
        self.assertEqual(api.checks[0]["head_sha"], api.ref)

    def test_behind_source_is_rejected_before_any_mutation(self):
        api = SubmitAPI()
        api.behind = True
        with self.assertRaises(ValueError):
            submit(api, "plutoframework/pluto", "feature", "Fix")
        self.assertEqual(api.calls, [])

    def test_moving_source_is_rejected_before_any_mutation(self):
        api = SubmitAPI()
        api.source_changes = True
        with self.assertRaises(ValueError):
            submit(api, "plutoframework/pluto", "feature", "Fix")
        self.assertEqual(api.calls, [])

    def test_existing_unowned_branch_is_not_overwritten(self):
        api = SubmitAPI()
        api.colliding_ref = True
        with self.assertRaises(ValueError):
            submit(api, "plutoframework/pluto", "feature", "Fix")
        self.assertEqual(api.calls, [])

    def interrupted(self):
        api = SubmitAPI()
        api.fail_pr_once = True
        with self.assertRaises(GitHubError):
            submit(api, "plutoframework/pluto", "feature", "Fix")
        self.assertIsNotNone(api.ref)
        self.assertEqual(api.prs, [])
        return api

    def test_interrupted_creation_recovers_without_rewriting_branch(self):
        api = self.interrupted()
        original = api.ref
        api.calls.clear()
        submit(api, "plutoframework/pluto", "feature", "Fix")
        self.assertEqual(api.ref, original)
        self.assertEqual(len(api.prs), 1)
        self.assertEqual(len(api.calls), 1)
        self.assertTrue(api.calls[0][0].endswith("/pulls"))

    def test_recovery_can_publish_a_revised_source(self):
        api = self.interrupted()
        original = api.ref
        api.source_sha, api.tree = "source-v2", "revised-tree"
        submit(api, "plutoframework/pluto", "feature", "Fix")
        self.assertNotEqual(api.ref, original)
        self.assertEqual(api.commits[api.ref]["tree"]["sha"], "revised-tree")
        updates = [data for _, data, method in api.calls if method == "PATCH"]
        self.assertEqual(updates, [{"sha": api.ref, "force": False}])
        self.assertEqual(len(api.prs), 1)

    def test_spoofed_malformed_or_replayed_provenance_cannot_recover(self):
        for mutation in ["app", "head", "branch", "repository", "malformed"]:
            with self.subTest(mutation=mutation):
                api = self.interrupted()
                if mutation == "app":
                    api.checks[0]["app"]["slug"] = "github-actions"
                elif mutation == "head":
                    api.checks[0]["head_sha"] = "different-commit"
                else:
                    evidence = json.loads(api.checks[0]["output"]["text"])
                    evidence[mutation] = "unrelated"
                    api.checks[0]["output"]["text"] = (
                        "invalid"
                        if mutation == "malformed"
                        else json.dumps(evidence)
                    )
                api.calls.clear()
                with self.assertRaises(ValueError):
                    submit(api, "plutoframework/pluto", "feature", "Fix")
                self.assertEqual(api.calls, [])

    def test_failed_attestation_does_not_publish_a_branch(self):
        api = SubmitAPI()
        api.fail_check = True
        with self.assertRaises(GitHubError):
            submit(api, "plutoframework/pluto", "feature", "Fix")
        self.assertIsNone(api.ref)
        self.assertEqual(api.prs, [])


if __name__ == "__main__":
    unittest.main()
