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

    def pages(self, path):
        return []

    def request(self, path, data=None, method=None):
        if data is not None:
            self.calls.append((path, data, method))
            if path.endswith("/git/commits"):
                return {"sha": "app-commit"}
            if path.endswith("/pulls"):
                return {
                    "html_url": "https://github.com/plutoframework/pluto/pull/9"
                }
            return {}
        if "/commits/feature" in path:
            self.reads += 1
            return {
                "sha": "changed"
                if self.source_changes and self.reads > 1
                else "source",
                "commit": {"tree": {"sha": "proposed-tree"}},
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
            raise GitHubError(404, "GET")
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


if __name__ == "__main__":
    unittest.main()
