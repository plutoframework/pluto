"""Exercise source-pin proofs against real isolated Git object databases."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import rollout
from source_pins import verify


class SourcePinGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pluto-source-pins-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Offline test")
        self.git("config", "user.email", "offline@example.invalid")
        self.template = (rollout.ROOT / ".github/pr-review-job.yml").read_text()
        self.write(".github/pr-review-job.yml", self.template)
        self.write("scripts/pr_automation/marker.py", "VALUE = 1\n")
        self.pin = self.commit("Commit policy code first")
        self.write_pins(self.pin)
        self.head = self.commit("Pin identical code in workflow metadata")

    def git(self, *args, input=None):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args],
            input=input,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def write_pins(self, pin):
        with patch.object(rollout, "ROOT", self.root):
            review = rollout.entrypoint("pluto", pin)
        self.write(".github/workflows/pr-review.yml", review)
        self.write(
            ".github/workflows/security.yml",
            rollout.security(pin, True, False),
        )

    def test_code_then_pin_commits_match_exact_source_objects(self):
        receipt = verify(self.root)
        self.assertEqual(receipt["head"], self.head)
        self.assertEqual(receipt["policy"], self.pin)
        self.assertNotEqual(self.head, self.pin)
        for source in receipt["sources"].values():
            self.assertEqual(source["head"], source["policy"])

    def test_dirty_source_template_and_workflows_do_not_replace_git_proof(self):
        self.write("scripts/pr_automation/marker.py", "VALUE = 2\n")
        self.write(".github/pr-review-job.yml", "dirty invalid template\n")
        self.write(".github/workflows/pr-review.yml", "dirty invalid pins\n")
        self.assertEqual(verify(self.root)["head"], self.head)

    def test_committed_stale_code_is_rejected(self):
        self.write("scripts/pr_automation/marker.py", "VALUE = 2\n")
        self.commit("Change source without updating pin")
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_committed_template_divergence_is_rejected(self):
        self.write(".github/pr-review-job.yml", self.template + "# changed\n")
        self.commit("Change canonical template without updating pin")
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_source_directory_replaced_by_blob_is_rejected(self):
        directory = self.root / "scripts/pr_automation"
        (directory / "marker.py").unlink()
        directory.rmdir()
        self.write("scripts/pr_automation", "not a source tree\n")
        self.commit("Replace the source tree with an unsupported object type")
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_missing_and_noncommit_policy_objects_are_rejected(self):
        blob = self.git("hash-object", "-w", "--stdin", input="not a commit")
        for pin in ("f" * 40, blob):
            with self.subTest(pin=pin):
                self.write_pins(pin)
                self.commit("Use unsupported policy object")
                with self.assertRaises((ValueError,
                                        subprocess.CalledProcessError)):
                    verify(self.root)

    def test_quoted_checkout_and_run_scalar_decoys_are_rejected(self):
        path = self.root / ".github/workflows/pr-review.yml"
        review = path.read_text().replace(
            "repository: plutoframework/pluto",
            'repository: "plutoframework/pluto"',
        ).replace("ref: " + self.pin, "ref: " + "e" * 40)
        review += "      - run: |\n" + "".join(
            "          repository: plutoframework/pluto\n"
            "          ref: " + self.pin + "\n"
            for _ in range(2)
        )
        self.write(".github/workflows/pr-review.yml", review)
        self.commit("Place decoy pin text outside actual checkout mappings")
        with self.assertRaises(ValueError):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()
