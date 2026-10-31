"""Small GitHub client with bounded pagination and no credential logging."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

AUTO_MERGE_QUERY = "mutation($id: ID!, $head: GitObjectID!) { enablePullRequestAutoMerge(input: {pullRequestId: $id, expectedHeadOid: $head, mergeMethod: SQUASH}) { pullRequest { number } } }"


def reviewer_mutation(path, method, data):
    """The review controller cannot create PRs or publish source changes."""
    repo = r"/repos/[^/]+/[^/]+/"
    return (
        method == "POST"
        and bool(
            re.fullmatch(repo + r"(check-runs|pulls/[0-9]+/reviews)", path)
        )
        or method == "PUT"
        and bool(re.fullmatch(repo + r"pulls/[0-9]+/merge", path))
        or method == "POST"
        and path == "/graphql"
        and isinstance(data, dict)
        and data.get("query") == AUTO_MERGE_QUERY
    )


class GitHubError(RuntimeError):
    def __init__(self, status, method):
        self.status = status
        super().__init__(f"GitHub {method} failed ({status})")


class GitHub:
    def __init__(self, token, checks_token=None):
        self.token = token
        self.checks_token = checks_token

    def request(self, path, data=None, method=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("GitHub paths must be relative to api.github.com")
        operation = method or ("GET" if data is None else "POST")
        if operation != "GET" and not reviewer_mutation(path, operation, data):
            raise ValueError(
                "Reviewer mutations are limited to checks, reviews and merging existing PRs"
            )
        token = self.token
        if operation == "POST" and path.endswith("/check-runs"):
            if not self.checks_token:
                raise ValueError(
                    "Required checks must be signed by the GitHub App"
                )
            token = self.checks_token
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=body,
            method=operation,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "pluto-pr-steward",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            raise GitHubError(error.code, method or "GET") from None

    def pages(self, path, key=None):
        result = []
        for page in range(1, 101):
            separator = "&" if "?" in path else "?"
            payload = self.request(
                path
                + separator
                + urllib.parse.urlencode({"per_page": 100, "page": page})
            )
            items = payload[key] if key else payload
            if not isinstance(items, list):
                raise ValueError("Unexpected GitHub list response")
            result.extend(items)
            if len(items) < 100:
                return result
        raise ValueError("GitHub pagination exceeded safety limit")
