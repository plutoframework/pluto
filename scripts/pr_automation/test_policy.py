import copy
import unittest

from policy import human_approval, human_reasons, review_blockers

CLEAN = {
    "review": {
        "risk_level": "low",
        "merge_recommendation": "no_concerns_found",
        "key_issues_to_review": [],
        "security_concerns": "No",
    },
    "coverage": {"files_not_reviewed": [], "files_partially_reviewed": []},
}


class PolicyTests(unittest.TestCase):
    def test_routine_fix_is_candidate(self):
        self.assertEqual(
            human_reasons("pluto-core", [{"filename": "pagination/page.go"}]),
            [],
        )
        self.assertEqual(review_blockers(CLEAN), [])

    def test_sensitive_changes_need_human(self):
        for repo, path in [
            ("pluto-auth", "README.md"),
            ("pluto-rbac", "policy.go"),
            ("pluto-core", "crypto/key.go"),
            ("pluto-core", "go.sum"),
            ("pluto", ".github/workflows/ci.yml"),
            ("pluto-jobs", "stores/postgres/migrations/001.sql"),
            ("cli", "internal/authentication/login.go"),
            ("pluto", "scripts/pr_automation/policy.py"),
        ]:
            with self.subTest(path=path):
                self.assertTrue(human_reasons(repo, [{"filename": path}]))

    def test_rename_cannot_remove_human_requirement(self):
        self.assertTrue(
            human_reasons(
                "pluto-core",
                [{"filename": "misc.go", "previous_filename": "auth.go"}],
            )
        )

    def test_invalid_inventory_fails_closed(self):
        for files in [
            [],
            [{}],
            [{"filename": "../x.go"}],
            [{"filename": "/x.go"}],
            [{"filename": "a\\b.go"}],
        ]:
            with self.assertRaises(ValueError):
                human_reasons("pluto-core", files)
        with self.assertRaises(ValueError):
            human_reasons("unknown", [{"filename": "x.go"}])

    def test_incomplete_or_ambiguous_review_blocks(self):
        for section, field, value in [
            ("coverage", "files_not_reviewed", ["x.go"]),
            ("coverage", "files_partially_reviewed", ["x.go"]),
            ("review", "risk_level", "medium"),
            ("review", "merge_recommendation", "needs_review"),
            ("review", "key_issues_to_review", [{"issue": "bug"}]),
            ("review", "security_concerns", "No concerns maybe"),
        ]:
            result = copy.deepcopy(CLEAN)
            result[section][field] = value
            self.assertTrue(review_blockers(result))
        for value in [None, {}, {"review": {}}, {"review": {}, "coverage": {}}]:
            self.assertTrue(review_blockers(value))

    def test_only_current_independent_human_approval_counts(self):
        review = {
            "id": 1,
            "user": {"login": "owner", "type": "User"},
            "state": "APPROVED",
            "commit_id": "head",
        }
        self.assertEqual(
            human_approval([review], "head", "author", {"owner"}), "owner"
        )
        self.assertIsNone(human_approval([review], "next", "author", {"owner"}))
        self.assertIsNone(human_approval([review], "head", "owner", {"owner"}))
        bot = copy.deepcopy(review)
        bot["user"]["type"] = "Bot"
        self.assertIsNone(human_approval([bot], "head", "author", {"owner"}))
        rejected = {**review, "id": 2, "state": "CHANGES_REQUESTED"}
        self.assertIsNone(
            human_approval([review, rejected], "head", "author", {"owner"})
        )
        dismissed = {**review, "id": 2, "state": "DISMISSED"}
        self.assertIsNone(
            human_approval([review, dismissed], "head", "author", {"owner"})
        )


if __name__ == "__main__":
    unittest.main()
