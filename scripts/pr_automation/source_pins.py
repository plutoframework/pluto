"""Verify deployed automation pins against the committed reviewed tree."""

import json
from pathlib import Path
import re
import subprocess

from rollout import entrypoint, security

SHA = re.compile(r"[0-9a-f]{40}")
SOURCES = {
    "scripts/pr_automation": "tree",
    ".github/pr-review-job.yml": "blob",
}


def workflow_pin(review, security):
    """Accept the exact source checkout and policy locations we generate."""
    pattern = re.compile(
        r"(?m)^( +)repository: plutoframework/pluto\n\1ref: (\S+)$"
    )
    review_refs = [value for _, value in pattern.findall(review)]
    security_refs = [value for _, value in pattern.findall(security)]
    policy = re.findall(r"(?m)^ +POLICY_SHA: (\S+)$", review)
    if (len(review_refs) != 2 or len(security_refs) != 1 or len(policy) != 1):
        raise ValueError("Missing or unexpected root policy pin locations")
    pins = review_refs + security_refs + policy
    if any(SHA.fullmatch(pin) is None for pin in pins) or len(set(pins)) != 1:
        raise ValueError("Root workflows disagree on an immutable policy pin")
    return pins[0]


def verify(root, run=None):
    """Compare Git objects, never uncommitted worktree source or PR claims."""
    if run is None:
        def run(*args):
            value = subprocess.check_output(
                ["git", "-C", str(root), *args], text=True,
                stderr=subprocess.PIPE,
            )
            return value if args[0] == "show" else value.strip()
    head = run("rev-parse", "--verify", "HEAD^{commit}")
    if SHA.fullmatch(head) is None:
        raise ValueError("Reviewed HEAD is not an immutable commit")
    review = run("show", head + ":.github/workflows/pr-review.yml")
    scan = run("show", head + ":.github/workflows/security.yml")
    template = run("show", head + ":.github/pr-review-job.yml")
    pin = workflow_pin(review, scan)
    if (review != entrypoint("pluto", pin, job_template=template)
            or scan != security(pin, True, False)):
        raise ValueError("Root policy workflows are not canonical")
    if run("rev-parse", "--verify", pin + "^{commit}") != pin:
        raise ValueError("The policy pin is not an immutable commit object")
    receipt = {"head": head, "policy": pin, "sources": {}}
    for path, kind in SOURCES.items():
        objects = []
        for revision in (head, pin):
            reference = revision + ":" + path
            if run("cat-file", "-t", reference) != kind:
                raise ValueError("Policy source has an unexpected object type")
            identifier = run("rev-parse", "--verify", reference)
            if SHA.fullmatch(identifier) is None:
                raise ValueError("Policy source object is missing")
            objects.append(identifier)
        if objects[0] != objects[1]:
            raise ValueError("Pinned policy source differs from reviewed HEAD")
        receipt["sources"][path] = {"head": objects[0], "policy": objects[1]}
    return receipt


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    print(json.dumps(verify(root), sort_keys=True))
