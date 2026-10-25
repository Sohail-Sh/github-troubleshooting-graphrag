"""Minimal GitHub REST + GraphQL client: pagination, rate-limit backoff, retries and an on-disk cache
so an interrupted ingestion can resume without re-spending API quota."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Iterator

import requests

log = logging.getLogger(__name__)

API = "https://api.github.com"


class GitHubError(RuntimeError):
    def __init__(self, status: int, url: str, message: str):
        super().__init__(f"GitHub {status} for {url}: {message}")
        self.status = status
        self.url = url


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        cache_dir: Path | None = None,
        session: requests.Session | None = None,
        max_retries: int = 4,
        sleep=time.sleep,
    ):
        self.token = token
        self.cache_dir = cache_dir
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.sleep = sleep
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-troubleshooting-graphrag",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------ cache
    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def _cache_get(self, key: str) -> Any | None:
        p = self._cache_path(key)
        if p and p.exists():
            return json.loads(p.read_text())
        return None

    def _cache_put(self, key: str, value: Any) -> None:
        p = self._cache_path(key)
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(value))

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
            except requests.ConnectionError as exc:
                if attempt == self.max_retries:
                    raise GitHubError(0, url, str(exc)) from exc
                self.sleep(2**attempt)
                continue
            if resp.status_code in (403, 429) and (
                resp.headers.get("X-RateLimit-Remaining") == "0" or "retry-after" in {k.lower() for k in resp.headers}
            ):
                reset = resp.headers.get("Retry-After") or (
                    max(0, int(resp.headers.get("X-RateLimit-Reset", "0")) - int(time.time()))
                )
                wait = min(int(reset or 60), 3600)
                log.warning("GitHub rate limit hit; sleeping %ss", wait)
                if attempt == self.max_retries:
                    raise GitHubError(resp.status_code, url, "rate limited")
                self.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < self.max_retries:
                self.sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                try:
                    message = resp.json().get("message", resp.text)
                except ValueError:
                    message = resp.text
                raise GitHubError(resp.status_code, url, message[:300])
            return resp
        raise GitHubError(0, url, "exhausted retries")  # pragma: no cover

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("http") else f"{API}{path}"
        key = f"GET {url} {json.dumps(params or {}, sort_keys=True)}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        data = self._request("GET", url, params=params).json()
        self._cache_put(key, data)
        return data

    def paginate(self, path: str, params: dict[str, Any] | None = None, max_items: int | None = None) -> Iterator[Any]:
        """Follow ``Link: rel=next`` headers. Each page is cached individually."""
        url = path if path.startswith("http") else f"{API}{path}"
        params = {"per_page": 100, **(params or {})}
        count = 0
        while url:
            key = f"PAGE {url} {json.dumps(params, sort_keys=True)}"
            cached = self._cache_get(key)
            if cached is None:
                resp = self._request("GET", url, params=params)
                cached = {"items": resp.json(), "next": resp.links.get("next", {}).get("url")}
                self._cache_put(key, cached)
            for item in cached["items"]:
                yield item
                count += 1
                if max_items and count >= max_items:
                    return
            url, params = cached["next"], {}

    def graphql(self, query: str, variables: dict[str, Any]) -> Any:
        if not self.token:
            raise GitHubError(401, f"{API}/graphql", "GraphQL requires GITHUB_TOKEN")
        key = f"GQL {query} {json.dumps(variables, sort_keys=True)}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        data = self._request("POST", f"{API}/graphql", json={"query": query, "variables": variables}).json()
        if data.get("errors"):
            raise GitHubError(200, f"{API}/graphql", json.dumps(data["errors"])[:300])
        self._cache_put(key, data["data"])
        return data["data"]


CLOSING_REFS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 25) { nodes { number repository { nameWithOwner } } }
    }
  }
}
"""
