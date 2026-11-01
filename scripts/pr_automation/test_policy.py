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

    def test_sensitive_categories_are_classified_without_a_human_gate(self):
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

    def test_rename_cannot_remove_sensitive_classification(self):
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
            ("review", "risk_level", "unknown"),
            ("review", "merge_recommendation", "needs_review"),
            ("review", "key_issues_to_review", [{"issue": "bug"}]),
            ("review", "security_concerns", "No concerns maybe"),
        ]:
            result = copy.deepcopy(CLEAN)
            result[section][field] = value
            self.assertTrue(review_blockers(result))
        for value in [None, {}, {"review": {}}, {"review": {}, "coverage": {}}]:
            self.assertTrue(review_blockers(value))

    def test_clean_intrinsic_risk_never_requires_an_extra_approval(self):
        for risk in ("low", "medium", "high"):
            for maintainer in (False, True):
                with self.subTest(risk=risk, maintainer=maintainer):
                    result = copy.deepcopy(CLEAN)
                    result["review"]["risk_level"] = risk
                    self.assertEqual(
                        review_blockers(result, maintainer=maintainer), []
                    )

    def test_yaml_scalar_whitespace_is_not_a_new_decision(self):
        result = copy.deepcopy(CLEAN)
        result["review"].update(
            risk_level="high\n",
            merge_recommendation="no_concerns_found\n",
            security_concerns="No\n",
        )
        self.assertEqual(review_blockers(result), [])
        self.assertEqual(review_blockers(result, maintainer=True), [])

    def test_human_approval_never_waives_review_blockers(self):
        for section, field, value in (
            ("review", "merge_recommendation", "needs_review"),
            ("review", "merge_recommendation", "changes_required"),
            ("review", "key_issues_to_review", [{"issue": "bug"}]),
            ("review", "security_concerns", "Uncertain ownership check"),
            ("coverage", "files_not_reviewed", ["secret.go"]),
            ("coverage", "files_partially_reviewed", ["partial.go"]),
        ):
            result = copy.deepcopy(CLEAN)
            result[section][field] = value
            for maintainer in (False, True):
                with self.subTest(field=field, maintainer=maintainer):
                    self.assertTrue(
                        review_blockers(result, maintainer=maintainer)
                    )

    def test_non_string_or_unknown_decisions_stay_blocked(self):
        for field, value in [
            ("risk_level", True),
            ("risk_level", "LOW"),
            ("merge_recommendation", None),
            ("security_concerns", False),
            ("security_concerns", "No concerns maybe"),
        ]:
            result = copy.deepcopy(CLEAN)
            result["review"][field] = value
            self.assertTrue(review_blockers(result, maintainer=True))

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
