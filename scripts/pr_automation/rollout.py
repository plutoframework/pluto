"""Generate pinned repository entrypoints; apply protections only explicitly."""

import argparse
from pathlib import Path
import subprocess

from policy import ORG, REPOSITORIES, human_reasons

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = "d23441a48e516b6c34aea4fa41551a30e30af803"
GO = "924ae3a1cded613372ab5595356fb5720e22ba16"
NODE = "249970729cb0ef3589644e2896645e5dc5ba9c38"
PYTHON = "6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"


def entrypoint(repository, revision):
    import json

    names = {
        "pluto": ["Check ecosystem README examples", "Test PR automation"],
        "pluto-core": ["check"],
        "pluto-auth": ["Check"],
        "pluto-rbac": ["Pluto CI"],
        "pluto-jobs": ["checks"],
        "pluto-agent": ["check"],
        "cli": ["Pluto CI"],
        "pluto-frontend": ["Check frontend primitives"],
        "pluto-example": ["Pluto CI"],
        "pluto-site": ["Pluto CI"],
        "pluto-skills": ["Check skills"],
    }[repository] + ["Pluto security", "Notify PR review", "CodeQL"]
    return f"""name: Pluto PR review
on:
  pull_request_target:
    types: [opened, reopened, synchronize, ready_for_review]
  workflow_run:
    workflows: {json.dumps(names)}
    types: [completed]
  workflow_dispatch:
    inputs:
      pr_number:
        description: Pull request to reevaluate
        required: true
        type: string
permissions:
  contents: read
concurrency:
  group: pluto-review-${{{{ github.event.pull_request.head.ref || github.event.workflow_run.head_branch || inputs.pr_number }}}}
  cancel-in-progress: false
jobs:
  review:
    uses: {ORG}/pluto/.github/workflows/pr-review-shared.yml@{revision}
    with:
      policy_revision: {revision}
"""


RELAY = """name: Notify PR review
on:
  pull_request_review:
    types: [submitted, dismissed]
permissions: {}
jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - run: echo 'Review state changed. The protected workflow_run handler reevaluates GitHub state.'
"""


def security(revision, has_go, has_node):
    steps = f"""      - uses: actions/checkout@{CHECKOUT}
        with:
          persist-credentials: false
          path: source
      - uses: actions/checkout@{CHECKOUT}
        with:
          repository: {ORG}/pluto
          ref: {revision}
          path: policy
          persist-credentials: false
"""
    if has_go:
        steps += f"""      - uses: actions/setup-go@{GO}
        with:
          go-version: "1.26.0"
          cache: false
"""
    if has_node:
        steps += f"""      - uses: actions/setup-node@{NODE}
        with:
          node-version: "24.22.1"
          package-manager-cache: false
"""
    steps += """      - name: Check dependency vulnerabilities
        working-directory: source
        run: python3 ../policy/scripts/pr_automation/security.py
"""
    return (
        """name: Pluto security
on: [push, pull_request]
permissions:
  contents: read
jobs:
  dependencies:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
"""
        + steps
    )


def codeowners(repository, root):
    if repository in {"pluto-auth", "pluto-rbac"}:
        return "# Authentication and authorization always require a maintainer.\n* @DavidHoenisch\n"
    patterns = {
        "/.github/",
        "/scripts/",
        "/skills/",
        "AGENTS.md",
        "CLAUDE.md",
        "CODEOWNERS",
        "go.mod",
        "go.sum",
        "go.work",
        "go.work.sum",
        "package.json",
        "package-lock.json",
        "*.lock",
        "*.sql",
        "**/migrations/",
        "**/crypto/",
        "**/auth/",
        "**/rbac/",
        "requirements*",
        "pyproject.toml",
        "mise.toml",
        "Makefile",
        ".golangci.yml",
        "Dockerfile",
        ".pr_agent.toml",
        ".gitmodules",
        ".npmrc",
    }
    files = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root, text=True
    ).split("\0")
    for path in files:
        if path and human_reasons(repository, [{"filename": path}]):
            patterns.add("/" + path.replace(" ", "\\ "))
    return (
        "# Native owner approval complements the Pluto merge-policy check.\n"
        + "".join(p + " @DavidHoenisch\n" for p in sorted(patterns))
    )


def open_pr_workflow(revision):
    return f"""name: Open a maintainer-reviewable PR
on:
  workflow_dispatch:
    inputs:
      head:
        description: Existing branch to propose against main
        required: true
        type: string
      title:
        description: Pull request title
        required: true
        type: string
permissions:
  contents: read
jobs:
  open:
    runs-on: ubuntu-latest
    environment: pluto-review
    steps:
      - uses: actions/checkout@{CHECKOUT}
        with:
          repository: {ORG}/pluto
          ref: {revision}
          path: policy
          persist-credentials: false
      - uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510
        id: app
        with:
          app-id: ${{{{ vars.PLUTO_REVIEW_APP_ID }}}}
          private-key: ${{{{ secrets.PLUTO_REVIEW_APP_PRIVATE_KEY }}}}
          owner: ${{{{ github.repository_owner }}}}
          repositories: ${{{{ github.event.repository.name }}}}
      - name: Open PR as the App
        env:
          BOT_TOKEN: ${{{{ steps.app.outputs.token }}}}
          TARGET_REPOSITORY: ${{{{ github.repository }}}}
          PR_HEAD: ${{{{ inputs.head }}}}
          PR_TITLE: ${{{{ inputs.title }}}}
        run: python3 policy/scripts/pr_automation/open_pr.py
"""


def prepare(repository, root, revision):
    root = Path(root)
    directory = root / ".github/workflows"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pr-review.yml").write_text(entrypoint(repository, revision))
    (directory / "pr-review-events.yml").write_text(RELAY)
    (directory / "pr-open.yml").write_text(open_pr_workflow(revision))
    (directory / "security.yml").write_text(
        security(
            revision,
            any(root.rglob("go.mod")),
            any(root.rglob("package-lock.json")),
        )
    )
    owners = root / ".github/CODEOWNERS"
    if owners.exists():
        raise ValueError("Existing CODEOWNERS must be merged manually")
    owners.write_text(codeowners(repository, root))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", choices=REPOSITORIES)
    parser.add_argument("root", type=Path)
    parser.add_argument("revision")
    args = parser.parse_args()
    if len(args.revision) != 40 or any(
        c not in "0123456789abcdef" for c in args.revision
    ):
        raise ValueError("A full trusted policy commit SHA is required")
    prepare(args.repository, args.root, args.revision)
