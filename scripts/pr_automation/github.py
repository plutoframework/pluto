"""Small GitHub client with bounded pagination and no credential logging."""

import json
import urllib.error
import urllib.parse
import urllib.request


class GitHubError(RuntimeError):
    def __init__(self, status, method):
        self.status = status
        super().__init__(f"GitHub {method} failed ({status})")


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, path, data=None, method=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("GitHub paths must be relative to api.github.com")
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=body,
            method=method or ("GET" if body is None else "POST"),
            headers={
                "Authorization": "Bearer " + self.token,
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
