"""Stable identifiers. Re-running ingestion must produce the same IDs so MERGE never duplicates."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def id(self) -> str:
        return f"repo:{self.full_name.lower()}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.full_name}"

    def issue_url(self, number: int) -> str:
        return f"{self.html_url}/issues/{number}"

    def pr_url(self, number: int) -> str:
        return f"{self.html_url}/pull/{number}"

    def commit_url(self, sha: str) -> str:
        return f"{self.html_url}/commit/{sha}"

    def blob_url(self, ref: str, path: str, line: int | None = None) -> str:
        url = f"{self.html_url}/blob/{ref}/{path}"
        return f"{url}#L{line}" if line else url

    def compare_url(self, base: str, head: str) -> str:
        """``base...head`` reports "identical" or "behind" when head is an ancestor of base."""
        return f"{self.html_url}/compare/{base}...{head}"

    def release_url(self, tag: str) -> str:
        return f"{self.html_url}/releases/tag/{tag}"


def parse_repo_url(url: str) -> RepoRef:
    """Parse ``https://github.com/psf/requests`` (or ``psf/requests``) into a RepoRef."""
    url = url.strip()
    m = GITHUB_URL_RE.match(url)
    if not m and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
        owner, repo = url.split("/")
        return RepoRef(owner, repo.removesuffix(".git"))
    if not m:
        raise ValueError(f"Not a GitHub repository URL: {url!r}")
    return RepoRef(m.group("owner"), m.group("repo"))


def short_hash(*parts: object, length: int = 16) -> str:
    h = hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:length]


def issue_id(repo: RepoRef, number: int) -> str:
    return f"{repo.id}:issue:{number}"


def pr_id(repo: RepoRef, number: int) -> str:
    return f"{repo.id}:pr:{number}"


def commit_id(repo: RepoRef, sha: str) -> str:
    return f"{repo.id}:commit:{sha}"


def file_id(repo: RepoRef, path: str) -> str:
    return f"{repo.id}:file:{path}"


def file_revision_id(repo: RepoRef, path: str, sha: str) -> str:
    return f"{repo.id}:filerev:{sha}:{path}"


def release_id(repo: RepoRef, tag: str) -> str:
    return f"{repo.id}:release:{tag}"


def hunk_id(repo: RepoRef, pr_number: int, path: str, index: int) -> str:
    return f"{repo.id}:hunk:pr{pr_number}:{short_hash(path)}:{index}"


def comment_id(repo: RepoRef, github_comment_id: int | str) -> str:
    return f"{repo.id}:comment:{github_comment_id}"


def doc_id(repo: RepoRef, path: str) -> str:
    return f"{repo.id}:doc:{path}"


def doc_chunk_id(repo: RepoRef, path: str, revision: str, index: int) -> str:
    return f"{repo.id}:docchunk:{short_hash(path, revision)}:{index}"


def code_chunk_id(repo: RepoRef, path: str, revision: str, symbol: str) -> str:
    return f"{repo.id}:codechunk:{short_hash(path, revision, symbol)}"


def changelog_id(repo: RepoRef, tag: str, index: int) -> str:
    return f"{repo.id}:changelog:{tag}:{index}"


def error_signature_id(exception_type: str, message_pattern: str) -> str:
    return f"error:{exception_type}:{short_hash(message_pattern.lower())}"


def exception_type_id(name: str) -> str:
    return f"exception:{name}"


def stack_frame_id(file_path: str, function_name: str) -> str:
    # Line numbers drift between versions; identity is (file, function).
    return f"frame:{short_hash(file_path.lower(), function_name)}"
