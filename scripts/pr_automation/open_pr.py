"""Publish an App-authored change for independent maintainer approval."""

import os
from urllib.parse import quote, urlencode

from github import GitHub, GitHubError
from policy import ORG, REPOSITORIES

APP_LOGIN = "pluto-pr-steward[bot]"


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
    if current and not existing:
        raise ValueError(
            "Submission branch already exists without an App PR; use a new source branch"
        )
    if existing and not current:
        raise ValueError(
            "Submission branch disappeared; retry after investigation"
        )
    desired_tree = source["commit"]["tree"]["sha"]
    if current:
        commit = api.request(prefix + "/git/commits/" + current)
        ancestry = api.request(prefix + "/compare/" + base + "..." + current)
        if (
            commit["tree"]["sha"] == desired_tree
            and ancestry.get("merge_base_commit", {}).get("sha") == base
        ):
            return existing[0]
    # Reject moving source/base references instead of overwriting newer work.
    if api.request(source_path)["sha"] != source["sha"]:
        raise ValueError("Source branch changed during submission; retry")
    if api.request(prefix + "/branches/main")["commit"]["sha"] != base:
        raise ValueError(
            "Main changed during submission; update the source branch"
        )
    parents = [current, base] if current and current != base else [base]
    proposed = api.request(
        prefix + "/git/commits",
        {
            "message": title + "\n\nSource proposal: " + source["sha"],
            "tree": desired_tree,
            "parents": parents,
        },
    )
    if current:
        api.request(
            ref, {"sha": proposed["sha"], "force": False}, method="PATCH"
        )
        return existing[0]
    api.request(
        prefix + "/git/refs",
        {"ref": "refs/heads/" + branch, "sha": proposed["sha"]},
    )
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
