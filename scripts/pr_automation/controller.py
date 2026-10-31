"""Trusted review controller; never checks out or executes pull-request code."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile

from github import AUTO_MERGE_QUERY, GitHub, GitHubError
from policy import (
    CHECK_SCHEMA,
    GATE_CHECK,
    ORG,
    REPOSITORIES,
    REVIEW_CHECK,
    SECURITY_WORKFLOW,
    human_approval,
    human_reasons,
    review_blockers,
    review_value,
)
from review import run as run_review


def check(api, prefix, name, head, conclusion, summary, evidence=None):
    output = {"title": name, "summary": summary[:60000]}
    if evidence is not None:
        output["text"] = json.dumps(evidence)
    return api.request(
        prefix + "/check-runs",
        {
            "name": name,
            "head_sha": head,
            "status": "completed",
            "conclusion": conclusion,
            "output": output,
        },
    )


def inventory(api, prefix, pr):
    files = api.pages(f"{prefix}/pulls/{pr['number']}/files")
    if len(files) != pr["changed_files"]:
        raise ValueError("Incomplete pull-request file inventory")
    return files


def workflows_ready(api, prefix, pr, repository):
    """Require successful current-head runs of every registered CI workflow."""
    reasons = []
    for path in REPOSITORIES[repository] + [SECURITY_WORKFLOW]:
        runs = api.pages(
            f"{prefix}/actions/workflows/{path.rsplit('/', 1)[1]}/runs?event=pull_request&head_sha={pr['head']['sha']}",
            "workflow_runs",
        )
        runs = [
            r
            for r in runs
            if r.get("event") == "pull_request"
            and r.get("head_sha") == pr["head"]["sha"]
            and r.get("path", "").split("@")[0] == path
            and r.get("repository", {}).get("full_name")
            == pr["base"]["repo"]["full_name"]
        ]
        latest = max(
            runs, key=lambda r: (r["id"], r.get("run_attempt", 1)), default=None
        )
        if latest is None or latest.get("conclusion") != "success":
            reasons.append("Waiting for successful " + path)
    return reasons


REVIEW_UNAVAILABLE = object()


def review_scope(pr):
    return {
        "schema": CHECK_SCHEMA,
        "repository": pr["base"]["repo"]["full_name"],
        "number": pr["number"],
        "head": pr["head"]["sha"],
        "base": pr["base"]["sha"],
        "policy": os.environ["POLICY_SHA"],
        "reviewer": os.environ["BOT_LOGIN"],
    }


def previous_result(api, prefix, pr, app_id):
    checks = api.pages(
        f"{prefix}/commits/{pr['head']['sha']}/check-runs?filter=all",
        "check_runs",
    )
    for item in sorted(checks, key=lambda c: c["id"], reverse=True):
        if (
            item["name"] != REVIEW_CHECK
            or item.get("app", {}).get("id") != app_id
        ):
            continue
        try:
            evidence = json.loads(item.get("output", {}).get("text", ""))
        except (TypeError, ValueError):
            return None
        if isinstance(evidence, dict) and all(
            evidence.get(key) == value
            for key, value in review_scope(pr).items()
        ):
            if item.get("conclusion") != "success":
                return REVIEW_UNAVAILABLE
            result = evidence.get("result")
            return result if isinstance(result, dict) else REVIEW_UNAVAILABLE
        return None
    return None


def steward_review_state(reviews, head):
    for review in sorted(reviews, key=lambda r: r["id"], reverse=True):
        if (
            review["user"]["login"] == os.environ["BOT_LOGIN"]
            and review.get("commit_id") == head
            and review["state"] in {"APPROVED", "CHANGES_REQUESTED"}
        ):
            return review["state"]
    return None


def requests_changes(result):
    if not isinstance(result, dict):
        return False
    review = result.get("review")
    if not isinstance(review, dict):
        return False
    issues = review.get("key_issues_to_review")
    security = review_value(review.get("security_concerns"))
    return (
        isinstance(issues, list)
        and bool(issues)
        or review_value(review.get("merge_recommendation"))
        == "changes_required"
        or bool(security)
        and security != "No"
    )


def qualified_reviewers(api, prefix, reviews):
    allowed = set()
    for login in sorted(
        {(r.get("user") or {}).get("login") for r in reviews} - {None}
    ):
        if login == os.environ["BOT_LOGIN"]:
            continue
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,38}", login):
            continue
        permission = api.request(f"{prefix}/collaborators/{login}/permission")[
            "permission"
        ]
        if permission in {"admin", "maintain"}:
            allowed.add(login)
    return allowed


def process(api, repository, number, fresh=False):
    prefix = f"/repos/{ORG}/{repository}"
    pr = api.request(f"{prefix}/pulls/{number}")
    if pr["state"] != "open" or pr["base"]["ref"] != "main":
        return
    if pr.get("draft"):
        return
    head, base = pr["head"]["sha"], pr["base"]["sha"]
    app_id = int(os.environ["PLUTO_REVIEW_APP_ID"])
    check(
        api,
        prefix,
        GATE_CHECK,
        head,
        "action_required",
        "Reevaluating all policy gates for the current revision.",
    )
    if pr["user"]["login"] == os.environ["BOT_LOGIN"]:
        check(
            api,
            prefix,
            REVIEW_CHECK,
            head,
            "skipped",
            "Steward must not author PRs. A contributor must submit this change; no model call was made.",
        )
        check(
            api,
            prefix,
            GATE_CHECK,
            head,
            "action_required",
            "PRs authored by Steward are outside the reviewer's role and cannot be approved or merged by it.",
        )
        return
    files = inventory(api, prefix, pr)
    reasons = human_reasons(repository, files)
    if any(not file.get("patch") for file in files):
        reasons.append("binary or omitted file diff")
    result = None if fresh else previous_result(api, prefix, pr, app_id)
    if result is REVIEW_UNAVAILABLE:
        check(
            api,
            prefix,
            GATE_CHECK,
            head,
            "action_required",
            "A review attempt for this revision is running, interrupted or failed. Push a new revision or explicitly retry the model review; CI notifications do not spend more review tokens.",
        )
        return
    scope = review_scope(pr)
    if result is None:
        check(
            api,
            prefix,
            REVIEW_CHECK,
            head,
            "action_required",
            "Review is running; no merge is permitted yet.",
            scope,
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                result = run_review(
                    pr["html_url"], Path(directory) / "review.json"
                )
        except Exception:
            check(
                api,
                prefix,
                REVIEW_CHECK,
                head,
                "failure",
                "Astra review failed or returned incomplete output. Retry the workflow; automatic merging remains blocked.",
                scope,
            )
            check(
                api,
                prefix,
                GATE_CHECK,
                head,
                "failure",
                "No complete review is available.",
            )
            return
        current = api.request(f"{prefix}/pulls/{number}")
        if current["head"]["sha"] != head or current["base"]["sha"] != base:
            check(
                api,
                prefix,
                REVIEW_CHECK,
                head,
                "failure",
                "PR changed during review. A fresh review is required.",
                scope,
            )
            return
        evidence = {**scope, "result": result}
        if len(json.dumps(evidence)) > 60000:
            check(
                api,
                prefix,
                REVIEW_CHECK,
                head,
                "failure",
                "Structured review exceeds the evidence limit; maintainer investigation required.",
                scope,
            )
            return
        # Success here means review completed, not that findings were absent.
        check(
            api,
            prefix,
            REVIEW_CHECK,
            head,
            "success",
            "Astra review completed. The separate merge-policy check evaluates its findings.",
            evidence,
        )
    reviews = api.pages(f"{prefix}/pulls/{number}/reviews")
    approver = human_approval(
        reviews,
        head,
        pr["user"]["login"],
        qualified_reviewers(api, prefix, reviews),
    )
    blockers = review_blockers(result, maintainer=bool(approver))
    if reasons and not approver:
        blockers.append("Maintainer approval required: " + ", ".join(reasons))
    blockers.extend(workflows_ready(api, prefix, pr, repository))
    # Never dismiss or ignore a human's outstanding request for changes.
    latest = {}
    for item in sorted(reviews, key=lambda r: r["id"]):
        if item["state"] != "COMMENTED":
            latest[item["user"]["login"]] = item
    if any(
        r["state"] == "CHANGES_REQUESTED"
        and r["user"]["type"] == "User"
        and r["user"]["login"] != os.environ["BOT_LOGIN"]
        for r in latest.values()
    ):
        blockers.append("A maintainer has requested changes")
    current = api.request(f"{prefix}/pulls/{number}")
    if current["head"]["sha"] != head or current["base"]["sha"] != base:
        blockers.append("PR revision changed; fresh review required")
    if current.get("mergeable") is not True:
        blockers.append("PR mergeability is unknown or conflicted")
    if (
        current["head"]["sha"] == head
        and current["base"]["sha"] == base
        and requests_changes(result)
        and steward_review_state(reviews, head) != "CHANGES_REQUESTED"
    ):
        api.request(
            f"{prefix}/pulls/{number}/reviews",
            {
                "event": "REQUEST_CHANGES",
                "commit_id": head,
                "body": "Astra identified unresolved findings or security concerns for this commit. See the PR review feedback and Pluto merge-policy check, then push fixes for a fresh review.",
            },
        )
    if blockers:
        check(
            api,
            prefix,
            GATE_CHECK,
            head,
            "action_required",
            "\n".join("- " + b for b in blockers),
        )
        return
    if os.environ.get("ENABLE_AUTO_MERGE") != "true":
        check(
            api,
            prefix,
            GATE_CHECK,
            head,
            "action_required",
            "All policy gates passed. Rollout is in review-only mode.",
        )
        return
    check(
        api,
        prefix,
        GATE_CHECK,
        head,
        "success",
        "All checks passed; "
        + (
            "maintainer approved."
            if approver
            else "routine change qualifies for automatic approval."
        ),
    )
    if steward_review_state(reviews, head) != "APPROVED":
        api.request(
            f"{prefix}/pulls/{number}/reviews",
            {
                "event": "APPROVE",
                "commit_id": head,
                "body": "Pluto policy passed for this commit: complete Astra review, CI/security checks, and required maintainer approval.",
            },
        )

    # Merge a fully ready PR atomically. GitHub rejects a racing head or any
    # unmet branch rule. A 405 means readiness is pending, so queue auto-merge.
    try:
        merged = api.request(
            f"{prefix}/pulls/{number}/merge",
            {"sha": head, "merge_method": "squash"},
            method="PUT",
        )
    except GitHubError as error:
        if error.status != 405:
            check(
                api,
                prefix,
                GATE_CHECK,
                head,
                "action_required",
                "GitHub rejected the merge precondition. Fresh review or maintainer investigation is required.",
            )
            raise
    else:
        if merged.get("merged") is not True:
            check(
                api,
                prefix,
                GATE_CHECK,
                head,
                "action_required",
                "GitHub did not confirm that the PR merged.",
            )
            raise RuntimeError("Merge was not confirmed")
        return

    # Native auto-merge waits for CodeQL, conversation resolution and all other
    # branch rules. expectedHeadOid rejects a push during this transition.
    response = api.request(
        "/graphql",
        {
            "query": AUTO_MERGE_QUERY,
            "variables": {"id": pr["node_id"], "head": head},
        },
    )
    if response.get("errors"):
        check(
            api,
            prefix,
            GATE_CHECK,
            head,
            "action_required",
            "GitHub rejected automatic merging. A maintainer must investigate the repository settings.",
        )
        raise RuntimeError("GitHub rejected auto-merge")


def main():
    full_name = os.environ["TARGET_REPOSITORY"]
    if full_name not in {f"{ORG}/{name}" for name in REPOSITORIES}:
        raise ValueError("Repository is outside the protected Pluto inventory")
    repository = full_name.split("/")[1]
    api = GitHub(os.environ["BOT_TOKEN"], os.environ["CHECK_TOKEN"])
    if api.request("/user").get("login") != os.environ["BOT_LOGIN"]:
        raise ValueError(
            "Reviewer credential does not match the Steward identity"
        )
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if os.environ["GITHUB_EVENT_NAME"] == "pull_request_target":
        number = event["pull_request"]["number"]
        process(api, repository, number)
    elif os.environ["GITHUB_EVENT_NAME"] == "workflow_run":
        run = event["workflow_run"]
        # Payload metadata can trigger reevaluation, never authorize a merge.
        for pr in api.pages(f"/repos/{full_name}/pulls?state=open&base=main"):
            if pr["head"]["sha"] == run["head_sha"]:
                process(api, repository, pr["number"])
    elif os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        process(
            api,
            repository,
            int(event["inputs"]["pr_number"]),
            fresh=str(event["inputs"].get("retry_review", "false")).lower()
            == "true",
        )
    else:
        raise ValueError("Unsupported privileged workflow event")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"PR automation stopped safely ({type(error).__name__}); no bypass attempted.",
            file=sys.stderr,
        )
        sys.exit(1)
