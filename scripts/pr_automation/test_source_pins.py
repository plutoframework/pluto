"""Pin metadata may differ while executable automation must match exactly."""

import subprocess
import unittest
from pathlib import Path

from rollout import entrypoint, security

from source_pins import SOURCES, verify, workflow_pin

HEAD, PIN, TREE = "a" * 40, "b" * 40, "c" * 40


def workflow(pins):
    return "".join(
        "  repository: plutoframework/pluto\n  ref: " + pin + "\n"
        for pin in pins
    )


class SourcePinTests(unittest.TestCase):
    def runner(self, mutation=None):
        def run(*args):
            if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                return HEAD
            if args == ("show", HEAD + ":.github/workflows/pr-review.yml"):
                return entrypoint("pluto", PIN)
            if args == ("show", HEAD + ":.github/workflows/security.yml"):
                return security(PIN, True, False)
            if args == ("show", HEAD + ":.github/pr-review-job.yml"):
                return (Path(__file__).resolve().parents[2]
                        / ".github/pr-review-job.yml").read_text()
            if args == ("rev-parse", "--verify", PIN + "^{commit}"):
                if mutation == "missing":
                    raise subprocess.CalledProcessError(128, "git")
                return "d" * 40 if mutation == "tag" else PIN
            command, *_, reference = args
            path = reference.split(":", 1)[1]
            if command == "cat-file":
                return "blob" if mutation == "type" else SOURCES[path]
            if command == "rev-parse":
                if mutation == "empty":
                    return ""
                if mutation == "code" and reference.startswith(PIN + ":"):
                    return "d" * 40
                return TREE
            raise AssertionError(args)
        return run

    def test_workflow_metadata_changes_allow_identical_executable_sources(self):
        receipt = verify("unused", self.runner())
        self.assertEqual(receipt["head"], HEAD)
        self.assertEqual(receipt["policy"], PIN)
        self.assertNotEqual(HEAD, PIN)
        for source in receipt["sources"].values():
            self.assertEqual(source, {"head": TREE, "policy": TREE})

    def test_code_type_tag_missing_and_shallow_objects_fail_closed(self):
        for mutation in ("code", "type", "tag", "empty", "missing"):
            with self.subTest(mutation=mutation):
                with self.assertRaises((ValueError,
                                        subprocess.CalledProcessError)):
                    verify("unused", self.runner(mutation))

    def test_all_four_live_locations_require_the_same_full_commit(self):
        good = workflow([PIN, PIN]) + "  POLICY_SHA: " + PIN + "\n"
        security = workflow([PIN])
        self.assertEqual(workflow_pin(good, security), PIN)
        for review, scan in (
            (good.replace("POLICY_SHA: " + PIN, "POLICY_SHA: " + HEAD),
             security),
            (good, workflow([HEAD])),
            (good.replace(PIN, "main"), security),
            (good.replace(PIN, PIN.upper()), security),
            (good.replace(PIN, PIN[:12]), security),
            (workflow([PIN]), security),
            (good + "  POLICY_SHA: " + PIN + "\n", security),
            (good, ""),
        ):
            with self.subTest(review=review, security=scan):
                with self.assertRaises(ValueError):
                    workflow_pin(review, scan)


if __name__ == "__main__":
    unittest.main()
