"""Apply merge protections after the corresponding CI and review pilot passes."""

import argparse
import json
import subprocess
from policy import GATE_CHECK, ORG, REPOSITORIES, REVIEW_CHECK


def apply(repository, app_id):
    # App-specific required checks cannot be forged by a PR's Actions token.
    payload = {
        "required_status_checks": {
            "strict": True,
            "checks": [
                {"context": REVIEW_CHECK, "app_id": app_id},
                {"context": GATE_CHECK, "app_id": app_id},
            ],
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
            "required_approving_review_count": 1,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_conversation_resolution": True,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }
    base = f"repos/{ORG}/{repository}"
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            base + "/branches/main/protection",
            "--input",
            "-",
        ],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(
        ["gh", "api", "--method", "PATCH", base, "--input", "-"],
        input=json.dumps(
            {
                "allow_auto_merge": True,
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
            }
        ),
        text=True,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    scanning = {
        "name": "Pluto code scanning",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {"include": ["refs/heads/main"], "exclude": []}
        },
        "rules": [
            {
                "type": "code_scanning",
                "parameters": {
                    "code_scanning_tools": [
                        {
                            "tool": "CodeQL",
                            "alerts_threshold": "errors",
                            "security_alerts_threshold": "high_or_higher",
                        }
                    ]
                },
            }
        ],
    }
    existing = json.loads(
        subprocess.check_output(["gh", "api", base + "/rulesets"], text=True)
    )
    matching = next(
        (r for r in existing if r["name"] == scanning["name"]), None
    )
    path = base + "/rulesets" + ("/" + str(matching["id"]) if matching else "")
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "PUT" if matching else "POST",
            path,
            "--input",
            "-",
        ],
        input=json.dumps(scanning),
        text=True,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    print(
        repository
        + ": protected main; App checks and independent review required"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", choices=REPOSITORIES)
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reviewed protection settings",
    )
    args = parser.parse_args()
    if not args.apply:
        parser.error("Explicit --apply is required after a successful pilot")
    apply(args.repository, args.app_id)
