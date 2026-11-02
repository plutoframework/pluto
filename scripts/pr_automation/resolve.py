"""Resolve canonical review targets using only the built-in read credential."""

import json
import os
from pathlib import Path
import re
import sys

from github import GitHub
from policy import ORG, REPOSITORIES

SHA = re.compile(r"[0-9a-f]{40}\Z")


class ReadOnlyGitHub:
    def __init__(self, token):
        self._client = GitHub(token)

    def request(self, path):
        return self._client.request(path, method="GET")

    def pages(self, path):
        return self._client.pages(path)


def _number(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    raise ValueError("Invalid pull-request number")


def _head(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("Invalid pull-request head")
    return value


def _current(api, repository, number, hint=None, closed=False):
    pr = api.request(f"/repos/{repository}/pulls/{number}")
    if not isinstance(pr, dict) or type(pr.get("number")) is not int:
        raise ValueError("Invalid current pull-request response")
    if pr["number"] != number:
        raise ValueError("Current pull-request number does not match")
    if pr.get("base", {}).get("repo", {}).get("full_name") != repository:
        raise ValueError("Current pull-request repository does not match")
    if pr.get("base", {}).get("ref") != "main":
        return None
    head = _head(pr.get("head", {}).get("sha"))
    if pr.get("state") not in {"open", "closed"}:
        raise ValueError("Invalid current pull-request state")
    if (hint is not None and head != hint) or (
        not closed and pr["state"] != "open"
    ):
        return None
    return {"number": number, "head": head}


def resolve_targets(api, repository, event_name, event):
    """Event SHAs are hints; every selected target is re-read from GitHub."""
    if repository not in {f"{ORG}/{name}" for name in REPOSITORIES}:
        raise ValueError("Repository is outside the protected inventory")
    if not isinstance(event, dict):
        raise ValueError("Invalid triggering event")
    declared = event.get("repository", {}).get("full_name")
    if declared is not None and declared != repository:
        raise ValueError("Triggering repository does not match")
    if event_name == "pull_request_target":
        payload = event.get("pull_request", {})
        number = _number(payload.get("number"))
        if "number" in event and _number(event["number"]) != number:
            raise ValueError("Triggering pull-request numbers disagree")
        hint = _head(payload.get("head", {}).get("sha"))
        target = _current(api, repository, number, hint)
        return [target] if target else []
    if event_name == "workflow_dispatch":
        number = _number(event.get("inputs", {}).get("pr_number"))
        target = _current(api, repository, number, closed=True)
        return [target] if target else []
    if event_name != "workflow_run":
        raise ValueError("Unsupported review trigger")
    hint = _head(event.get("workflow_run", {}).get("head_sha"))
    # GitHub can supply an empty or partial event pull_requests collection.
    # Enumerate the repository instead, without trusting that payload list.
    listed = api.pages(f"/repos/{repository}/pulls?state=open&base=main")
    if not isinstance(listed, list):
        raise ValueError("Invalid open pull-request inventory")
    targets, seen = [], set()
    for pr in listed:
        if pr.get("base", {}).get("ref") != "main":
            continue
        number = _number(pr.get("number"))
        if _head(pr.get("head", {}).get("sha")) != hint or number in seen:
            continue
        seen.add(number)
        target = _current(api, repository, number, hint)
        if target:
            targets.append(target)
    if len(targets) > 256:
        raise ValueError("Review target matrix exceeds the supported limit")
    return sorted(targets, key=lambda target: target["number"])


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    api = ReadOnlyGitHub(os.environ["GITHUB_TOKEN"])
    targets = resolve_targets(
        api, repository, os.environ["GITHUB_EVENT_NAME"], event
    )
    matrix = json.dumps({"include": targets}, separators=(",", ":"))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("matrix=" + matrix + "\n")
        output.write("has_targets=" + str(bool(targets)).lower() + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"PR resolution stopped safely ({type(error).__name__}).",
            file=sys.stderr,
        )
        sys.exit(1)
