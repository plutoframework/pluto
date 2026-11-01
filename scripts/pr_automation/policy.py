"""Deterministic gates for independent Steward review and merging."""

from pathlib import PurePosixPath
import re

ORG = "plutoframework"
REPOSITORIES = {
    "pluto": [
        ".github/workflows/readme-examples.yml",
        ".github/workflows/automation-test.yml",
    ],
    "pluto-core": [".github/workflows/check.yml"],
    "pluto-auth": [".github/workflows/check.yml"],
    "pluto-rbac": [".github/workflows/ci.yml"],
    "pluto-jobs": [".github/workflows/ci.yml"],
    "pluto-agent": [".github/workflows/check.yml"],
    "cli": [".github/workflows/ci.yml"],
    "pluto-frontend": [".github/workflows/ci.yml"],
    "pluto-example": [".github/workflows/ci.yml"],
    "pluto-site": [".github/workflows/ci.yml"],
    "pluto-skills": [".github/workflows/check.yml"],
}
SECURITY_WORKFLOW = ".github/workflows/security.yml"
REVIEW_CHECK = "Pluto review"
GATE_CHECK = "Pluto merge policy"
CHECK_SCHEMA = 2


def human_reasons(repository, files):
    """Classify sensitive changes, without requiring a separate human approval.

    The legacy name remains for callers. Validate both rename paths and reject
    incomplete or suspicious inventories regardless of risk classification.
    """
    if repository not in REPOSITORIES or not files:
        raise ValueError("Unknown repository or empty changed-file inventory")
    reasons = set()
    for entry in files:
        for field in ("filename", "previous_filename"):
            path = entry.get(field)
            if path is None and field == "previous_filename":
                continue
            if not isinstance(path, str) or not path or "\\" in path:
                raise ValueError("Invalid changed-file path")
            parts = PurePosixPath(path).parts
            if (
                path.startswith("/")
                or ".." in parts
                or any(ord(c) < 32 for c in path)
            ):
                raise ValueError("Invalid changed-file path")
            lower = path.lower()
            name = parts[-1].lower()
            if repository in {"pluto-auth", "pluto-rbac"}:
                reasons.add("authentication or authorization repository")
            if (
                lower.startswith((".github/", "scripts/", ".githooks/"))
                or name
                in {
                    "agents.md",
                    "claude.md",
                    "codeowners",
                    "makefile",
                    "mise.toml",
                    "pluto.toml",
                    ".golangci.yml",
                    ".pr_agent.toml",
                    "dockerfile",
                }
                or lower.startswith("skills/")
            ):
                reasons.add("automation, policy or build configuration")
            if (
                name
                in {
                    "go.mod",
                    "go.sum",
                    "go.work",
                    "go.work.sum",
                    "package.json",
                    "package-lock.json",
                    "npm-shrinkwrap.json",
                    "yarn.lock",
                    "pnpm-lock.yaml",
                    "pyproject.toml",
                    "uv.lock",
                    "cargo.toml",
                    "cargo.lock",
                    "gemfile",
                    "gemfile.lock",
                }
                or name in {".gitmodules", ".npmrc", ".yarnrc", ".yarnrc.yml"}
                or name.startswith("requirements")
                or name.endswith(".csproj")
            ):
                reasons.add("dependencies")
            if "migration" in lower or name.endswith(".sql"):
                reasons.add("database migrations or SQL")
            if re.search(
                r"(^|[/_.-])(auth\w*|rbac|crypto\w*|session\w*|permission\w*|token\w*|password\w*|credential\w*|tenant\w*|oidc|totp|csrf)([/_.-]|$)",
                lower,
            ):
                reasons.add("security-sensitive code")
    return sorted(reasons)


def review_value(value):
    """PR-Agent YAML block scalars may retain a terminal newline in JSON."""
    return value.strip() if isinstance(value, str) else None


def review_blockers(result, maintainer=False):
    """Require an explicit clean decision; approvals cannot waive any blocker.

    ``maintainer`` is retained for compatibility, but never changes the result.
    Intrinsic risk is recorded separately from findings and security concerns.
    """
    if not isinstance(result, dict):
        return ["Missing structured review"]
    review = result.get("review")
    coverage = result.get("coverage")
    if not isinstance(review, dict) or not isinstance(coverage, dict):
        return ["Malformed review or missing coverage"]
    blockers = []
    for key in ("files_not_reviewed", "files_partially_reviewed"):
        if coverage.get(key) != []:
            blockers.append("Incomplete review coverage: " + key)
    if review_value(review.get("risk_level")) not in {"low", "medium", "high"}:
        blockers.append("Review risk is missing or invalid")
    if review_value(review.get("merge_recommendation")) != "no_concerns_found":
        blockers.append("Review does not explicitly recommend merging")
    if review.get("key_issues_to_review") != []:
        blockers.append("Review has findings or a missing findings list")
    if review_value(review.get("security_concerns")) != "No":
        blockers.append("Security concerns are present or unknown")
    return blockers


def human_approval(reviews, head, author, allowed_reviewers):
    """Ignore bots, stale approvals and the PR author's own review."""
    latest = {}
    for review in sorted(reviews, key=lambda item: item.get("id", 0)):
        user = review.get("user") or {}
        login = user.get("login")
        if review.get("state") != "COMMENTED" and login:
            latest[login] = review
    for login, review in latest.items():
        if login == author or login not in allowed_reviewers:
            continue
        if (review.get("user") or {}).get("type") != "User":
            continue
        if (
            review.get("state") == "APPROVED"
            and review.get("commit_id") == head
        ):
            return login
    return None
