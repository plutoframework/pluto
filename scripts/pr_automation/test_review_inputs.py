"""Offline GitHub SDK-shaped snapshots; no provider or model requests."""

import base64
import copy
import difflib
import hashlib
import json
from types import SimpleNamespace as Obj
import unittest

from review_inputs import (
    attest_provider,
    inventory_digest,
    validate_input_evidence,
)

HEAD, BASE, MERGE = "a" * 40, "b" * 40, "c" * 40
REPO = "plutoframework/pluto"


def blob_sha(data):
    return hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\0" + data
    ).hexdigest()


def patch(before, after):
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True)
        )
    )
    return "".join(lines[2:])


class Repo:
    full_name = REPO

    def __init__(self, before, after):
        self.before, self.after = before, after
        self.truncated = False
        self.blobs = {
            blob_sha(data): data for data in [*before.values(), *after.values()]
        }
        self.blob_reads = 0
        self.modes = {}

    def compare(self, base, head):
        assert (base, head) == (BASE, HEAD)
        return Obj(merge_base_commit=Obj(sha=MERGE))

    def get_git_commit(self, revision):
        assert revision in (MERGE, HEAD)
        return Obj(sha=revision, tree=Obj(sha=revision))

    def get_git_tree(self, revision, recursive):
        assert recursive is True
        contents = self.before if revision == MERGE else self.after
        entries = [
            Obj(
                path=path,
                type="blob",
                mode=self.modes.get(path, "100644"),
                sha=blob_sha(data),
            )
            for path, data in contents.items()
        ]
        return Obj(sha=revision, truncated=self.truncated, tree=entries)

    def get_git_blob(self, sha):
        self.blob_reads += 1
        return Obj(
            sha=sha,
            encoding="base64",
            content=base64.b64encode(self.blobs[sha]).decode(),
        )


class Provider:
    repo, pr_num = REPO, 7

    def __init__(self, before=None, after=None, renamed=None, missing=False):
        before = before if before is not None else {"file.go": b"old\n"}
        after = after if after is not None else {"file.go": b"new\n"}
        renamed = renamed or {}
        self.repo_obj = Repo(before, after)
        self.files, self.diff = [], []
        self.filtered_diff_file_names = []
        for name in sorted(set(before) | set(after)):
            if name in renamed.values():
                continue
            old_name = renamed.get(name, name)
            old, new = before.get(old_name, b""), after.get(name, b"")
            if name in renamed:
                status = "renamed"
            elif name not in before:
                status = "added"
            elif name not in after:
                status = "removed"
            elif before[name] == after[name]:
                continue
            else:
                status = "modified"
            old_text = old.decode("utf8", errors="replace")
            new_text = new.decode("utf8", errors="replace")
            text_patch = patch(old_text, new_text)
            self.files.append(
                Obj(
                    filename=name,
                    status=status,
                    previous_filename=renamed.get(name),
                    sha=blob_sha(new if name in after else old),
                    patch=None if missing else text_patch,
                )
            )
            edit = {
                "added": "ADDED",
                "modified": "MODIFIED",
                "removed": "DELETED",
                "renamed": "RENAMED",
            }[status]
            self.diff.append(
                Obj(
                    filename=name,
                    old_filename=renamed.get(name),
                    base_file=old_text,
                    head_file=new_text,
                    patch=text_patch,
                    edit_type=Obj(name=edit),
                    content_fetch_failed=False,
                    head_file_is_complete=True,
                )
            )
        self.pr = Obj(
            number=7,
            changed_files=len(self.files),
            head=Obj(sha=HEAD),
            base=Obj(sha=BASE, repo=Obj(full_name=REPO)),
        )

    def get_files(self):
        return self.files

    def get_diff_files(self):
        return self.diff

    def _get_pr(self):
        return self.pr


class EvidenceTests(unittest.TestCase):
    def test_digest_is_canonical_and_patch_independent(self):
        provider = Provider(before={}, after={"z": b"z\n", "a": b"a\n"})
        original = inventory_digest(provider.files)
        provider.files.reverse()
        provider.files[0].patch = None
        self.assertEqual(original, inventory_digest(provider.files))
        for field, value in (("sha", "d" * 40), ("status", "modified")):
            changed = copy.deepcopy(provider.files)
            setattr(changed[0], field, value)
            self.assertNotEqual(original, inventory_digest(changed))

    def test_complete_provided_and_recovered_snapshots(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                provider = Provider(missing=missing)
                result = attest_provider(provider)
                proof = result["evidence"]
                self.assertEqual(
                    validate_input_evidence(provider.pr, provider.files, proof),
                    [],
                )
                self.assertEqual(proof["merge_base"], MERGE)
                self.assertEqual(
                    proof["recovered"], ["file.go"] if missing else []
                )
                self.assertEqual(proof["empty"], [])
                self.assertNotIn("files", proof)
                self.assertEqual(
                    result["files"][0]["head_blob"], blob_sha(b"new\n")
                )

    def test_verified_empty_added_file_has_explicit_model_metadata(self):
        provider = Provider(before={}, after={"__init__.py": b""}, missing=True)
        result = attest_provider(provider)
        self.assertEqual(
            blob_sha(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        )
        self.assertEqual(result["evidence"]["empty"], ["__init__.py"])
        self.assertEqual(result["evidence"]["recovered"], [])
        self.assertIn(
            "zero bytes; no textual diff", result["metadata_notes"][0]
        )
        self.assertEqual(result["files"][0]["head_mode"], "100644")

    def test_added_removed_and_renamed_regular_text(self):
        cases = [
            Provider(before={}, after={"added": b"text\n"}, missing=True),
            Provider(before={"gone": b"text\n"}, after={}, missing=True),
            Provider(
                before={"old": b"old\n"},
                after={"new": b"new\n"},
                renamed={"new": "old"},
                missing=True,
            ),
        ]
        for provider in cases:
            result = attest_provider(provider)
            self.assertEqual(
                validate_input_evidence(
                    provider.pr, provider.files, result["evidence"]
                ),
                [],
            )

    def test_snapshot_recovery_never_substitutes_missing_provider_text(self):
        for field, value in (
            ("head_file", ""),
            ("base_file", ""),
            ("head_file", "forged\n"),
            ("content_fetch_failed", True),
            ("head_file_is_complete", False),
        ):
            provider = Provider(missing=True)
            setattr(provider.diff[0], field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                attest_provider(provider)

    def test_filtered_duplicate_and_missing_inventory_fail(self):
        for kind in ("filtered", "duplicate", "missing", "tree omission"):
            provider = Provider(before={}, after={"a": b"a\n", "b": b"b\n"})
            if kind == "filtered":
                provider.filtered_diff_file_names = ["b"]
            elif kind == "duplicate":
                provider.diff.append(provider.diff[0])
            elif kind == "missing":
                provider.diff.pop()
            else:
                provider.files.pop()
                provider.diff.pop()
                provider.pr.changed_files -= 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                attest_provider(provider)

    def test_bad_sha_status_rename_and_truncated_tree_fail(self):
        for kind in ("sha", "status", "rename", "tree", "fetch"):
            provider = Provider()
            if kind == "sha":
                provider.files[0].sha = "f" * 40
            elif kind == "status":
                provider.files[0].status = "copied"
            elif kind == "rename":
                provider.diff[0].old_filename = "another.go"
            elif kind == "tree":
                provider.repo_obj.truncated = True
            else:
                provider.repo_obj.get_git_commit = lambda _: (
                    _ for _ in ()
                ).throw(OSError("unreadable immutable tree"))
            with self.subTest(kind=kind), self.assertRaises(
                (ValueError, OSError)
            ):
                attest_provider(provider)

    def test_binary_non_utf8_and_nonregular_files_fail(self):
        for kind in ("nul", "utf8", "symlink", "gitlink"):
            provider = Provider(before={}, after={"bad": b"text\n"})
            if kind in ("nul", "utf8"):
                data = b"\0" if kind == "nul" else b"\xff"
                provider = Provider(before={}, after={"bad": data})
                provider.diff[0].head_file = ""
            else:
                provider.repo_obj.modes["bad"] = (
                    "120000" if kind == "symlink" else "160000"
                )
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                attest_provider(provider)

    def test_present_but_truncated_patch_fails(self):
        before = b"a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"
        after = b"A\nb\nc\nd\ne\nf\ng\nh\ni\nJ\n"
        provider = Provider(before={"a": before}, after={"a": after})
        first = provider.diff[0].patch.split("@@")[0]
        # Keep one valid hunk while omitting the last changed line.
        valid_prefix = "@@ -1,3 +1,3 @@\n-a\n+A\n b\n c\n"
        provider.files[0].patch = provider.diff[0].patch = valid_prefix
        self.assertEqual(first, "")
        with self.assertRaisesRegex(ValueError, "truncated"):
            attest_provider(provider)

    def test_context_omission_uses_verified_blob_fetch(self):
        provider = Provider()
        provider.diff[0].base_file = provider.diff[0].head_file = ""
        result = attest_provider(provider)
        self.assertTrue(result["evidence"]["complete"])
        self.assertEqual(provider.repo_obj.blob_reads, 2)

    def test_empty_file_cannot_hide_behind_a_noop_hunk(self):
        provider = Provider(before={}, after={"empty": b""}, missing=True)
        provider.diff[0].patch = "@@ -0,0 +0,0 @@\n"
        with self.assertRaises(ValueError):
            attest_provider(provider)

    def test_signed_summary_is_compact_for_197_files(self):
        contents = {
            f"internal/pkg/file_{n}.go": b"package p\n" for n in range(197)
        }
        provider = Provider(before={}, after=contents)
        for raw in provider.files[:9]:
            raw.patch = None
        result = attest_provider(provider)
        self.assertEqual(result["evidence"]["file_count"], 197)
        self.assertLess(len(json.dumps(result["evidence"]).encode()), 2000)
        self.assertEqual(len(result["evidence"]["recovered"]), 9)

    def test_bad_missing_and_stale_evidence_never_raises(self):
        provider = Provider()
        proof = attest_provider(provider)["evidence"]
        for key, value in (
            ("schema", True),
            ("complete", False),
            ("repository", "other/repo"),
            ("number", 8),
            ("head", "f" * 40),
            ("base", "f" * 40),
            ("merge_base", "bad"),
            ("inventory_digest", "bad"),
            ("file_count", True),
            ("recovered", ["unknown"]),
            ("empty", ["file.go", "file.go"]),
        ):
            invalid = copy.deepcopy(proof)
            invalid[key] = value
            with self.subTest(key=key):
                self.assertTrue(
                    validate_input_evidence(
                        provider.pr, provider.files, invalid
                    )
                )
        for invalid in (None, [], {}, "model supplied this"):
            self.assertTrue(
                validate_input_evidence(provider.pr, provider.files, invalid)
            )

    def test_bool_and_float_evidence_fields_are_not_integer_proofs(self):
        provider = Provider()
        provider.pr.number = provider.pr_num = 1
        proof = attest_provider(provider)["evidence"]
        for key, value in (
            ("schema", 1.0),
            ("number", True),
            ("number", 1.0),
            ("file_count", 1.0),
            ("file_count", True),
            ("files", []),
        ):
            invalid = {**proof, key: value}
            with self.subTest(key=key, value=value):
                self.assertTrue(
                    validate_input_evidence(
                        provider.pr, provider.files, invalid
                    )
                )

    def test_compact_empty_claim_requires_added_zero_blob(self):
        provider = Provider()
        proof = attest_provider(provider)["evidence"]
        proof["empty"] = ["file.go"]
        self.assertTrue(
            validate_input_evidence(provider.pr, provider.files, proof)
        )

    def test_patch_body_must_match_immutable_head(self):
        provider = Provider()
        provider.files[0].patch = provider.diff[0].patch = provider.diff[
            0
        ].patch.replace("+new", "+forged")
        with self.assertRaisesRegex(ValueError, "head blob"):
            attest_provider(provider)

    def test_revision_change_is_detected_before_model_use(self):
        for source in ("provider", "fresh"):
            provider = Provider()
            changed = copy.deepcopy(provider.pr)
            changed.head.sha = "f" * 40
            if source == "provider":

                def changed_diff():
                    provider.pr = changed
                    return provider.diff

                provider.get_diff_files = changed_diff
            else:
                provider._get_pr = lambda: changed
            with self.subTest(source=source), self.assertRaises(ValueError):
                attest_provider(provider)

    def test_patch_variation_does_not_invalidate_attested_snapshot(self):
        provider = Provider()
        proof = attest_provider(provider)["evidence"]
        provider.files[0].patch = None
        self.assertEqual(
            validate_input_evidence(provider.pr, provider.files, proof), []
        )


if __name__ == "__main__":
    unittest.main()
