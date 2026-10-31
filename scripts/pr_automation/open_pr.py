"""Open an App-authored PR so a sole maintainer can review independently."""

import os
from github import GitHub
from policy import ORG, REPOSITORIES

repository = os.environ["TARGET_REPOSITORY"]
if repository not in {f"{ORG}/{name}" for name in REPOSITORIES}:
    raise ValueError("Repository is outside the Pluto inventory")
head, title = os.environ["PR_HEAD"], os.environ["PR_TITLE"]
if not head or len(head) > 255 or any(ord(c) < 32 for c in head):
    raise ValueError("Invalid branch")
if not title.strip() or len(title) > 256:
    raise ValueError("Invalid PR title")
pr = GitHub(os.environ["BOT_TOKEN"]).request(
    f"/repos/{repository}/pulls",
    {
        "base": "main",
        "head": head,
        "title": title,
        "body": "Opened by Pluto PR Steward for independent maintainer review. See the commits and CI results for the implementation and validation evidence.",
    },
)
print(pr["html_url"])
