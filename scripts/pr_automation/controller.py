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
    human_reasons,
    review_blockers,
    review_value,
)
from review import ReviewExecutionError, ReviewInputError, run as run_review
from review_inputs import validate_input_evidence


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
    """Require canonical PR CI and any latest push duplicate to succeed."""
    reasons = []
    for path in REPOSITORIES[repository] + [SECURITY_WORKFLOW]:
        runs = api.pages(
            f"{prefix}/actions/workflows/{path.rsplit('/', 1)[1]}/runs"
            f"?head_sha={pr['head']['sha']}",
            "workflow_runs",
        )
        runs = [
            run for run in runs
            if run.get("event") in {"pull_request", "push"}
            and run.get("head_sha") == pr["head"]["sha"]
            and run.get("path", "").split("@")[0] == path
            and run.get("repository", {}).get("full_name")
            == pr["base"]["repo"]["full_name"]
        ]
        for event in ("pull_request", "push"):
            matching = [run for run in runs if run["event"] == event]
            latest = max(
                matching,
                key=lambda run: (run["id"], run.get("run_attempt", 1)),
                default=None,
            )
            if latest is None and event == "push":
                continue
            if (
                latest is None
                or latest.get("status") != "completed"
                or latest.get("conclusion") != "success"
            ):
                reasons.append("Waiting for successful " + event + " " + path)
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



def review_failed(api, prefix, head, scope, summary):
    check(api, prefix, REVIEW_CHECK, head, "failure", summary, scope)
    check(api, prefix, GATE_CHECK, head, "failure",
          "No complete review is available.")

def process(api, repository, number, fresh=False, expected_head=None):
    prefix = f"/repos/{ORG}/{repository}"
    pr = api.request(f"{prefix}/pulls/{number}")
    if expected_head is not None and pr["head"]["sha"] != expected_head:
        return
    if pr["base"]["ref"] != "main":
        return
    if pr["state"] != "open":
        audit_merged(api, prefix, pr, repository)
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
    author = pr.get("user")
    if not isinstance(author, dict):
        author = {}
    login = author.get("login")
    if (
        login == os.environ["BOT_LOGIN"]
        or not isinstance(login, str)
        or not login.strip()
        or login != login.strip()
        or author.get("type") not in {"User", "Bot"}
    ):
        check(
            api,
            prefix,
            REVIEW_CHECK,
            head,
            "skipped",
            "Steward-authored or unknown-author PRs cannot receive independent"
            " approval from this controller; no model call was made.",
        )
        check(
            api,
            prefix,
            GATE_CHECK,
            head,
            "action_required",
            "Steward-authored or unknown-author PRs cannot be approved or "
            "merged by this controller.",
        )
        return
    files = inventory(api, prefix, pr)
    # Validate the inventory; sensitivity alone needs no extra voter.
    human_reasons(repository, files)
    result = None if fresh else previous_result(api, prefix, pr, app_id)
    if result is REVIEW_UNAVAILABLE:
        check(
            api,
            prefix,
            GATE_CHECK,
            head,
            "action_required",
            "A review attempt for this revision is running, interrupted or "
            "failed. Push a new revision or explicitly retry the model review;"
            " CI notifications do not spend more review tokens.",
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
        except (ReviewInputError, ReviewExecutionError) as error:
            review_failed(api, prefix, head, scope, str(error))
            return
        except Exception:
            review_failed(
                api, prefix, head, scope,
                "Astra review failed or returned incomplete output. "
                "Automatic merging remains blocked.",
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
                "Structured review exceeds the evidence limit; maintainer "
                "investigation required.",
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
            "Astra review completed. The separate merge-policy check evaluates"
            " its findings.",
            evidence,
        )
    reviews = api.pages(f"{prefix}/pulls/{number}/reviews")
    blockers = review_blockers(result)
    blockers.extend(validate_input_evidence(
        pr, files, result.get("input_evidence") if isinstance(result, dict)
        else None,
    ))
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
                "body": (
                    "Astra identified unresolved findings or security concerns"
                    " for this commit. See the PR review feedback and Pluto "
                    "merge-policy check, then push fixes for a fresh review."
                ),
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
        "Complete clean Astra review and current CI/security proofs passed; "
        "Steward may independently approve and merge.",
    )
    if steward_review_state(reviews, head) != "APPROVED":
        api.request(
            f"{prefix}/pulls/{number}/reviews",
            {
                "event": "APPROVE",
                "commit_id": head,
                "body": (
                    "Pluto policy passed for this commit: complete clean Astra"
                    " review and current CI/security checks."
                ),
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
        if merged_receipt(api, prefix, number, head, base):
            return
        if error.status != 405:
            check(
                api,
                prefix,
                GATE_CHECK,
                head,
                "action_required",
                "GitHub rejected the merge precondition. Fresh review or "
                "maintainer investigation is required.",
            )
            raise
    else:
        if not isinstance(merged, dict) or merged.get("merged") is not True:
            if merged_receipt(api, prefix, number, head, base):
                return
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
    try:
        response = api.request(
            "/graphql",
            {
                "query": AUTO_MERGE_QUERY,
                "variables": {"id": pr["node_id"], "head": head},
            },
        )
        if response.get("errors"):
            raise RuntimeError("GitHub rejected auto-merge")
        queued = response.get("data", {}).get("enablePullRequestAutoMerge")
        if (
            not isinstance(queued, dict)
            or not isinstance(queued.get("pullRequest"), dict)
            or queued["pullRequest"].get("number") != number
        ):
            raise RuntimeError("GitHub did not confirm auto-merge")
    except (GitHubError, RuntimeError, AttributeError, TypeError):
        if merged_receipt(api, prefix, number, head, base):
            return
        check(
            api, prefix, GATE_CHECK, head, "action_required",
            "GitHub rejected automatic merging. A maintainer must investigate "
            "the repository settings.",
        )
        raise



HISTORICAL_AUDIT_POLICY = "c5b2a88b22579c05db82af17812ded02bab77d5d"
AUDIT_APP_ID = 5550724


def merged_receipt(api, prefix, number, head, base):
    """Verify an existing Steward squash receipt without another merge."""
    try:
        current = api.request(f"{prefix}/pulls/{number}")
        sha = current.get("merge_commit_sha")
        if (
            current.get("state") != "closed"
            or current.get("merged") is not True
            or (current.get("merged_by") or {}).get("login")
            != os.environ["BOT_LOGIN"]
            or current.get("head", {}).get("sha") != head
            or current.get("base", {}).get("ref") != "main"
            or not sha
        ):
            return False
        merged = api.request(f"{prefix}/git/commits/{sha}")
        source = api.request(f"{prefix}/git/commits/{head}")
        tree = source.get("tree", {}).get("sha")
        return (
            merged.get("sha") == sha
            and source.get("sha") == head
            and [parent.get("sha") for parent in merged.get("parents", [])]
            == [base]
            and bool(tree)
            and merged.get("tree", {}).get("sha") == tree
        )
    except (GitHubError, AttributeError, TypeError):
        return False


def audit_merged(api, prefix, pr, repository):
    """Repair a false-red receipt after checking every historical proof."""
    head = pr["head"]["sha"]
    app_id = int(os.environ["PLUTO_REVIEW_APP_ID"])
    author = pr.get("user") or {}
    if (
        app_id != AUDIT_APP_ID or pr.get("merged") is not True
        or pr.get("state") != "closed"
        or author.get("login") == os.environ["BOT_LOGIN"]
        or not author.get("login")
        or author.get("type") not in {"User", "Bot"}
    ):
        return
    reviews = api.pages(f"{prefix}/pulls/{pr['number']}/reviews")
    if steward_review_state(reviews, head) != "APPROVED":
        return
    files = inventory(api, prefix, pr)
    checks = api.pages(
        f"{prefix}/commits/{head}/check-runs?filter=all", "check_runs"
    )
    candidates = [
        item for item in checks
        if item.get("name") == REVIEW_CHECK
        and item.get("head_sha") == head
        and item.get("app", {}).get("id") == AUDIT_APP_ID
    ]
    if not candidates:
        return
    latest = max(candidates, key=lambda item: item["id"])
    if (latest.get("status") != "completed"
            or latest.get("conclusion") != "success"):
        return
    try:
        evidence = json.loads(latest.get("output", {}).get("text", ""))
    except (ValueError, TypeError):
        return
    if not isinstance(evidence, dict):
        return
    expected = {
        "schema": CHECK_SCHEMA,
        "repository": pr["base"]["repo"]["full_name"],
        "number": pr["number"], "head": head,
        "reviewer": os.environ["BOT_LOGIN"],
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        return
    policy = evidence.get("policy")
    if policy not in {os.environ["POLICY_SHA"], HISTORICAL_AUDIT_POLICY}:
        return
    result = evidence.get("result")
    if review_blockers(result):
        return
    if policy == HISTORICAL_AUDIT_POLICY:
        # This signed historical profile had no recovered-input attestation.
        # Admit only its complete positive review of ordinary provided patches.
        if any(not file.get("patch") for file in files):
            return
    else:
        historical_pr = {
            **pr, "base": {**pr["base"], "sha": evidence.get("base")}
        }
        if validate_input_evidence(
            historical_pr, files, result.get("input_evidence")
        ):
            return
    if workflows_ready(api, prefix, pr, repository):
        return
    if not merged_receipt(
        api, prefix, pr["number"], head, evidence.get("base")
    ):
        return
    check(
        api, prefix, GATE_CHECK, head, "success",
        "Post-merge audit verified the existing Steward approval, signed "
        "complete review, CI and exact squash receipt; no merge was attempted.",
        {"head": head, "base": evidence.get("base"), "policy": policy,
         "merge_commit": pr.get("merge_commit_sha"), "audited": True},
    )


def resolved_target(name, event):
    """Bind each matrix job to the event hint and immutable queue identity."""
    fields = ("RESOLVED_PR_NUMBER", "RESOLVED_HEAD_SHA")
    if not any(field in os.environ for field in fields):
        return None
    number, head = (os.environ.get(field, "") for field in fields)
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("Invalid resolved PR number")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Invalid resolved head identity")
    number = int(number)
    if name == "pull_request_target":
        pr = event["pull_request"]
        if pr["number"] != number or pr["head"]["sha"] != head:
            raise ValueError("Resolved PR does not match its triggering event")
    elif name == "workflow_run":
        if event["workflow_run"]["head_sha"] != head:
            raise ValueError("Resolved head does not match its triggering event")
    elif name == "workflow_dispatch":
        if str(event["inputs"]["pr_number"]) != str(number):
            raise ValueError("Resolved PR does not match manual dispatch")
    else:
        raise ValueError("Unsupported privileged workflow event")
    return number, head


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
    name = os.environ["GITHUB_EVENT_NAME"]
    target = resolved_target(name, event)
    if target is not None:
        number, head = target
        process(
            api, repository, number,
            fresh=name == "workflow_dispatch" and str(
                event["inputs"].get("retry_review", "false")
            ).lower() == "true",
            expected_head=head,
        )
        return
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
            f"PR automation stopped safely ({type(error).__name__}); "
            "no bypass attempted.",
            file=sys.stderr,
        )
        sys.exit(1)
