"""Publish an App-authored change for independent maintainer approval."""

import json
import os
from urllib.parse import quote, urlencode

from github import GitHub, GitHubError
from policy import ORG, REPOSITORIES

APP_LOGIN = "pluto-pr-steward[bot]"
SUBMISSION_CHECK = "Pluto submission"


def owns_submission(api, prefix, repository, branch, head):
    """Only the App can attest a recoverable submission, before publishing it."""
    checks = api.pages(
        prefix + "/commits/" + head + "/check-runs?filter=all", "check_runs"
    )
    for check in checks:
        if (
            check.get("name") != SUBMISSION_CHECK
            or check.get("app", {}).get("slug") != "pluto-pr-steward"
            or check.get("head_sha") != head
            or check.get("conclusion") != "neutral"
        ):
            continue
        try:
            evidence = json.loads(check.get("output", {}).get("text", ""))
        except (TypeError, ValueError):
            continue
        if isinstance(evidence, dict) and all(
            evidence.get(key) == value
            for key, value in {
                "schema": 1,
                "repository": repository,
                "branch": branch,
            }.items()
        ):
            return True
    return False


def submit(api, repository, head, title):
    if repository not in {f"{ORG}/{name}" for name in REPOSITORIES}:
        raise ValueError("Repository is outside the Pluto inventory")
    if not head or len(head) > 180 or any(ord(c) < 32 for c in head):
        raise ValueError("Invalid source branch")
    if not title.strip() or len(title) > 256:
        raise ValueError("Invalid PR title")
    prefix = "/repos/" + repository
    source_path = prefix + "/commits/" + quote(head, safe="")
    source = api.request(source_path)
    base = api.request(prefix + "/branches/main")["commit"]["sha"]
    comparison = api.request(
        prefix + "/compare/" + base + "..." + source["sha"]
    )
    if comparison.get("merge_base_commit", {}).get("sha") != base:
        raise ValueError("Update the source branch with main before submission")
    if comparison.get("status") != "ahead":
        raise ValueError("Source branch has no changes ahead of main")
    branch = "review/" + head
    ref = prefix + "/git/refs/heads/" + quote(branch, safe="")
    query = urlencode(
        {"state": "open", "base": "main", "head": f"{ORG}:{branch}"}
    )
    existing = api.pages(prefix + "/pulls?" + query)
    if existing and (
        len(existing) != 1 or existing[0]["user"]["login"] != APP_LOGIN
    ):
        raise ValueError("Submission PR is not owned by the review App")
    try:
        current = api.request(
            prefix + "/git/ref/heads/" + quote(branch, safe="")
        )["object"]["sha"]
    except GitHubError as error:
        if error.status != 404:
            raise
        current = None
    if (
        current
        and not existing
        and not owns_submission(api, prefix, repository, branch, current)
    ):
        raise ValueError(
            "Submission branch already exists without an App PR; use a new source branch"
        )
    if existing and not current:
        raise ValueError(
            "Submission branch disappeared; retry after investigation"
        )
    desired_tree = source["commit"]["tree"]["sha"]
    unchanged = False
    if current:
        commit = api.request(prefix + "/git/commits/" + current)
        ancestry = api.request(prefix + "/compare/" + base + "..." + current)
        unchanged = (
            commit["tree"]["sha"] == desired_tree
            and ancestry.get("merge_base_commit", {}).get("sha") == base
        )
        if unchanged and existing:
            return existing[0]
    # Reject moving source/base references instead of overwriting newer work.
    if api.request(source_path)["sha"] != source["sha"]:
        raise ValueError("Source branch changed during submission; retry")
    if api.request(prefix + "/branches/main")["commit"]["sha"] != base:
        raise ValueError(
            "Main changed during submission; update the source branch"
        )
    if not unchanged:
        parents = [current, base] if current and current != base else [base]
        proposed = api.request(
            prefix + "/git/commits",
            {
                "message": title + "\n\nSource proposal: " + source["sha"],
                "tree": desired_tree,
                "parents": parents,
            },
        )
        # Write authenticated provenance before making the branch visible. A
        # retry can recover an interrupted PR creation without trusting forged
        # commit author fields or overwriting an unrelated branch.
        api.request(
            prefix + "/check-runs",
            {
                "name": SUBMISSION_CHECK,
                "head_sha": proposed["sha"],
                "status": "completed",
                "conclusion": "neutral",
                "output": {
                    "title": SUBMISSION_CHECK,
                    "summary": "App-owned submission prepared for independent review.",
                    "text": json.dumps(
                        {
                            "schema": 1,
                            "repository": repository,
                            "branch": branch,
                            "source": source["sha"],
                            "base": base,
                            "tree": desired_tree,
                        }
                    ),
                },
            },
        )
        if current:
            api.request(
                ref, {"sha": proposed["sha"], "force": False}, method="PATCH"
            )
        else:
            api.request(
                prefix + "/git/refs",
                {"ref": "refs/heads/" + branch, "sha": proposed["sha"]},
            )
    if existing:
        return existing[0]
    # The source branch remains intact. The App publishes the actual change on
    # a separate branch; this is not an empty commit to change push attribution.
    return api.request(
        prefix + "/pulls",
        {
            "base": "main",
            "head": branch,
            "title": title,
            "body": "App-authored submission for independent maintainer review.\n\n"
            + f"Source: [{source['sha']}]({source['html_url']}). "
            + "The source branch and its original history remain intact. "
            + "Update that branch and dispatch this workflow again to revise this PR.",
        },
    )


if __name__ == "__main__":
    result = submit(
        GitHub(os.environ["BOT_TOKEN"]),
        os.environ["TARGET_REPOSITORY"],
        os.environ["PR_HEAD"],
        os.environ["PR_TITLE"],
    )
    print(result["html_url"])
