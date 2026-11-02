"""Attest the exact text snapshot consumed by the pinned GitHub reviewer."""

import base64
import hashlib
import json
from pathlib import PurePosixPath
import re

SCHEMA = 1
SHA = re.compile(r"[0-9a-f]{40}\Z")
STATUSES = {
    "added": "ADDED",
    "removed": "DELETED",
    "modified": "MODIFIED",
    "renamed": "RENAMED",
}
MODES = {"100644", "100755"}
MAX_EVIDENCE_BYTES = 16000
EVIDENCE_FIELDS = {
    "schema",
    "complete",
    "repository",
    "number",
    "head",
    "base",
    "merge_base",
    "inventory_digest",
    "file_count",
    "recovered",
    "empty",
}
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def _get(value, key, default=None):
    return (
        value.get(key, default)
        if isinstance(value, dict)
        else getattr(value, key, default)
    )


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("Invalid immutable Git object identity")
    return value


def _path(value):
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or value.startswith("/")
        or ".." in PurePosixPath(value).parts
        or str(PurePosixPath(value)) != value
        or value == "."
    ):
        raise ValueError("Invalid repository-relative input path")
    return value


def _inventory(files):
    if not isinstance(files, (list, tuple)):
        raise ValueError("Invalid changed-file inventory")
    result, seen = [], set()
    for item in files:
        name = _path(_get(item, "filename"))
        status = _get(item, "status")
        if status not in STATUSES or name in seen:
            raise ValueError("Unsupported or duplicate changed-file entry")
        previous = _get(item, "previous_filename")
        if status == "renamed":
            previous = _path(previous)
            if previous == name:
                raise ValueError("Invalid rename identity")
        elif previous not in (None, ""):
            raise ValueError("Unexpected previous filename")
        else:
            previous = None
        seen.add(name)
        result.append(
            {
                "filename": name,
                "status": status,
                "previous_filename": previous,
                "sha": _sha(_get(item, "sha")),
            }
        )
    return sorted(result, key=lambda entry: entry["filename"])


def inventory_digest(rawfiles):
    """Hash immutable inventory metadata, not API-variable patch truncation."""
    encoded = json.dumps(
        _inventory(rawfiles),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _scope(pr):
    number = _get(pr, "number")
    repo = _get(_get(_get(pr, "base"), "repo"), "full_name")
    if (
        isinstance(number, bool)
        or not isinstance(number, int)
        or number < 1
        or not isinstance(repo, str)
        or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo, re.ASCII)
    ):
        raise ValueError("Invalid pull-request identity")
    return {
        "repository": repo,
        "number": number,
        "head": _sha(_get(_get(pr, "head"), "sha")),
        "base": _sha(_get(_get(pr, "base"), "sha")),
    }


def validate_input_evidence(pr, rawfiles, evidence):
    """Return blockers for absent, malformed, stale or mismatched sidecars."""
    try:
        inventory = _inventory(rawfiles)
        if (
            not inventory
            or type(_get(pr, "changed_files")) is not int
            or _get(pr, "changed_files") != len(inventory)
        ):
            raise ValueError("Changed-file inventory is incomplete")
        if not isinstance(evidence, dict):
            raise ValueError("Missing verified provider input evidence")
        if set(evidence) != EVIDENCE_FIELDS:
            raise ValueError("Unknown or missing provider evidence fields")
        if (
            evidence.get("schema") != SCHEMA
            or type(evidence.get("schema")) is not int
            or type(evidence.get("number")) is not int
            or evidence.get("complete") is not True
        ):
            raise ValueError(
                "Invalid provider input evidence version/completeness"
            )
        if any(evidence.get(k) != v for k, v in _scope(pr).items()):
            raise ValueError(
                "Provider input evidence belongs to another revision"
            )
        _sha(evidence.get("merge_base"))
        if (
            evidence.get("inventory_digest") != inventory_digest(rawfiles)
            or type(evidence.get("file_count")) is not int
            or evidence.get("file_count") != len(inventory)
        ):
            raise ValueError("Provider input inventory proof does not match")
        names = {item["filename"] for item in inventory}
        for field in ("recovered", "empty"):
            paths = evidence.get(field)
            if (
                not isinstance(paths, list)
                or not all(isinstance(path, str) for path in paths)
                or paths != sorted(set(paths))
                or not set(paths) <= names
            ):
                raise ValueError("Invalid recovered/empty input inventory")
        by_name = {item["filename"]: item for item in inventory}
        if any(
            by_name[path]["status"] != "added"
            or by_name[path]["sha"] != EMPTY_BLOB
            for path in evidence["empty"]
        ):
            raise ValueError(
                "Empty input evidence is not an added zero-byte blob"
            )
        if set(evidence["recovered"]) & set(evidence["empty"]):
            raise ValueError("Recovered and empty input classes overlap")
        if len(json.dumps(evidence).encode()) > MAX_EVIDENCE_BYTES:
            raise ValueError("Provider input evidence is oversized")
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        return ["Unverified review inputs: " + str(error)]
    return []


def _blob_sha(content):
    header = b"blob " + str(len(content)).encode() + b"\0"
    return hashlib.sha1(header + content).hexdigest()


def _tree(repo, revision):
    commit = repo.get_git_commit(revision)
    if _sha(_get(commit, "sha")) != revision:
        raise ValueError("Git commit lookup changed identity")
    tree_id = _sha(_get(_get(commit, "tree"), "sha"))
    tree = repo.get_git_tree(tree_id, recursive=True)
    if _get(tree, "sha") != tree_id or _get(tree, "truncated") is not False:
        raise ValueError("Git tree inventory is incomplete")
    found, seen = {}, set()
    for entry in _get(tree, "tree", []):
        path = _path(_get(entry, "path"))
        kind, mode = _get(entry, "type"), _get(entry, "mode")
        identity = _sha(_get(entry, "sha"))
        if path in seen:
            raise ValueError("Duplicate Git tree path")
        seen.add(path)
        if kind == "tree" and mode == "040000":
            continue
        if kind not in {"blob", "commit"} or path in found:
            raise ValueError("Unsupported or duplicate Git tree entry")
        found[path] = (identity, mode, kind)
    return found


def _regular(entry):
    if entry is not None and (entry[1] not in MODES or entry[2] != "blob"):
        raise ValueError("Changed file is not a regular Git blob")


def _content(repo, entry, text, required, cache):
    if not isinstance(text, str):
        raise ValueError("Provider text snapshot is unavailable")
    if entry is None:
        if text:
            raise ValueError("Provider supplied content for an absent side")
        return ""
    _regular(entry)
    encoded = text.encode("utf-8")
    if _blob_sha(encoded) == entry[0]:
        content = encoded
    elif text or required:
        raise ValueError("Provider reconstructed text differs from Git blob")
    else:
        if entry[0] not in cache:
            blob = repo.get_git_blob(entry[0])
            if _get(blob, "encoding") != "base64":
                raise ValueError("Unsupported Git blob encoding")
            raw = _get(blob, "content")
            if not isinstance(raw, str):
                raise ValueError("Git blob content is unavailable")
            cache[entry[0]] = base64.b64decode(
                "".join(raw.split()), validate=True
            )
        content = cache[entry[0]]
    if _blob_sha(content) != entry[0] or b"\0" in content:
        raise ValueError("Git blob content is binary or has wrong identity")
    return content.decode("utf-8", errors="strict")


def _lines(text):
    result = text.split("\n")
    return result[:-1] if result[-1] == "" else result


def _patch_matches(patch, before, after):
    """Check every hunk and every unchanged gap, rejecting truncated patches."""
    old, new = _lines(before), _lines(after)
    lines = patch.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    old_pos = new_pos = index = hunks = changes = 0
    while index < len(lines):
        match = re.fullmatch(
            r"@@ -(\d+)(?:,(\d+))? \+(\d+)" r"(?:,(\d+))? @@.*", lines[index]
        )
        if not match:
            raise ValueError("Provider patch has unsupported hunk syntax")
        a, count_a, b, count_b = (
            int(match[1]),
            int(match[2] or 1),
            int(match[3]),
            int(match[4] or 1),
        )
        a = a if count_a == 0 else a - 1
        b = b if count_b == 0 else b - 1
        if (
            a < old_pos
            or b < new_pos
            or a > len(old)
            or b > len(new)
            or count_a == count_b == 0
            or old[old_pos:a] != new[new_pos:b]
        ):
            raise ValueError("Provider patch omits changed content")
        old_pos, new_pos = a, b
        used_a = used_b = 0
        index += 1
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            index += 1
            if line == "\\ No newline at end of file":
                continue
            if not line or line[0] not in " +-":
                raise ValueError("Provider patch has unsupported body syntax")
            sign, value = line[0], line[1:]
            changes += sign in "+-"
            if sign in " -":
                if old_pos >= len(old) or old[old_pos] != value:
                    raise ValueError("Provider patch does not match base blob")
                old_pos += 1
                used_a += 1
            if sign in " +":
                if new_pos >= len(new) or new[new_pos] != value:
                    raise ValueError("Provider patch does not match head blob")
                new_pos += 1
                used_b += 1
        if (used_a, used_b) != (count_a, count_b):
            raise ValueError("Provider patch hunk counts are incomplete")
        hunks += 1
    if not hunks or not changes or old[old_pos:] != new[new_pos:]:
        raise ValueError("Provider patch is empty or truncated")


def attest_provider(provider):
    """Verify actual provider inputs against immutable Git trees and blobs."""
    pr = provider.pr
    scope = _scope(pr)
    if (
        provider.repo != scope["repository"]
        or provider.pr_num != scope["number"]
    ):
        raise ValueError("Provider repository identity does not match PR")
    raw = list(provider.get_files())
    entries = _inventory(raw)
    if not entries or _get(pr, "changed_files") != len(entries):
        raise ValueError("Provider inventory is incomplete")
    repo = provider.repo_obj
    if _get(repo, "full_name") != scope["repository"]:
        raise ValueError("Provider Git repository identity does not match")
    comparison = repo.compare(scope["base"], scope["head"])
    merge_base = _sha(_get(_get(comparison, "merge_base_commit"), "sha"))
    old_tree, new_tree = _tree(repo, merge_base), _tree(repo, scope["head"])
    changed = {
        p
        for p in set(old_tree) | set(new_tree)
        if old_tree.get(p) != new_tree.get(p)
    }
    old_claims, new_claims = set(), set()
    for item in entries:
        path, status = item["filename"], item["status"]
        old_path = item["previous_filename"] if status == "renamed" else path
        old = None if status == "added" else old_tree.get(old_path)
        new = None if status == "removed" else new_tree.get(path)
        if (
            status != "added"
            and old is None
            or status != "removed"
            and new is None
        ):
            raise ValueError("Provider status differs from Git tree change")
        _regular(old)
        _regular(new)
        for claims, name, present in (
            (old_claims, old_path, old),
            (new_claims, path, new),
        ):
            if present is not None:
                if name in claims:
                    raise ValueError("Provider repeats a changed Git tree side")
                claims.add(name)
        if item["sha"] != (new or old)[0]:
            raise ValueError("Raw file SHA differs from immutable Git tree")
    if old_claims != changed & set(old_tree) or new_claims != changed & set(
        new_tree
    ):
        raise ValueError(
            "Provider file inventory differs from Git tree changes"
        )
    patches = list(provider.get_diff_files())
    if getattr(provider, "filtered_diff_file_names", None) != []:
        raise ValueError("Provider filtered files from the model snapshot")
    by_name = {_get(file, "filename"): file for file in patches}
    if len(by_name) != len(patches) or set(by_name) != {
        i["filename"] for i in entries
    }:
        raise ValueError("Provider diff snapshot skipped or duplicated files")
    original = {_get(file, "filename"): file for file in raw}
    files, recovered, empty, notes, cache = [], [], [], [], {}
    for item in entries:
        path, status = item["filename"], item["status"]
        diff = by_name[path]
        if (
            _get(diff, "content_fetch_failed") is not False
            or _get(diff, "head_file_is_complete") is not True
            or _get(_get(diff, "edit_type"), "name") != STATUSES[status]
        ):
            raise ValueError(
                "Provider diff snapshot is incomplete or unsupported"
            )
        previous = _get(diff, "old_filename")
        if (
            status == "renamed"
            and previous != item["previous_filename"]
            or status != "renamed"
            and previous not in (None, "")
        ):
            raise ValueError("Provider diff rename metadata differs")
        raw_patch = _get(original[path], "patch")
        if raw_patch is not None and not isinstance(raw_patch, str):
            raise ValueError("Raw provider patch is malformed")
        patch = _get(diff, "patch")
        if not isinstance(patch, str):
            raise ValueError("Provider patch is unavailable")
        if raw_patch and patch != raw_patch:
            raise ValueError("Provider changed the supplied GitHub patch")
        old_path = item["previous_filename"] if status == "renamed" else path
        old = None if status == "added" else old_tree.get(old_path)
        new = None if status == "removed" else new_tree.get(path)
        before = _content(
            repo, old, _get(diff, "base_file"), not bool(raw_patch), cache
        )
        after = _content(
            repo, new, _get(diff, "head_file"), not bool(raw_patch), cache
        )
        is_empty = status == "added" and after == "" and not patch
        if is_empty:
            empty.append(path)
            notes.append(
                "Verified empty added regular file "
                + json.dumps(path)
                + " (mode "
                + new[1]
                + "): zero bytes; no textual diff."
            )
        else:
            _patch_matches(patch, before, after)
            if not raw_patch:
                recovered.append(path)
        files.append(
            {
                **item,
                "base_blob": old[0] if old else None,
                "head_blob": new[0] if new else None,
                "base_mode": old[1] if old else None,
                "head_mode": new[1] if new else None,
                "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
                "reconstructed": not bool(raw_patch) and not is_empty,
                "empty_added": is_empty,
            }
        )
    if _scope(provider.pr) != scope:
        raise ValueError("Provider revision changed during input attestation")
    fresh = getattr(provider, "_get_pr", None)
    if callable(fresh) and _scope(fresh()) != scope:
        raise ValueError("PR revision changed during input attestation")
    evidence = {
        "schema": SCHEMA,
        "complete": True,
        **scope,
        "merge_base": merge_base,
        "inventory_digest": inventory_digest(raw),
        "file_count": len(entries),
        "recovered": recovered,
        "empty": empty,
    }
    blockers = validate_input_evidence(pr, raw, evidence)
    if blockers:
        raise ValueError("; ".join(blockers))
    return {
        "schema": SCHEMA,
        **scope,
        "merge_base": merge_base,
        "files": files,
        "evidence": evidence,
        "metadata_notes": notes,
    }
