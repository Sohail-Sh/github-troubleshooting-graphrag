"""Stage A + B collection for one public GitHub repository into a versioned raw snapshot.

Sources:
  * ``git`` - a local clone (made automatically): merge/squash commits, patches, tags, release ancestry,
    release notes, docs and code. Works offline and needs no token.
  * ``api`` - GitHub REST/GraphQL: issue + PR text, comments, timeline events, linked closing issues,
    PR files and releases. Needs network access; a GITHUB_TOKEN is strongly recommended (and required
    for GraphQL closing references).

Nothing here uses SWE-bench or any pre-computed issue->fix pairing: every link is derived from GitHub data.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from config import EXTRACTOR_VERSION, PROJECT_ROOT, get_settings

from .git_history import GitHistoryCollector, chunk_document, chunk_python
from .github_client import CLOSING_REFS_QUERY, GitHubClient, GitHubError
from .ids import RepoRef, parse_repo_url
from .references import find_references
from .snapshot import Snapshot, utc_now

log = logging.getLogger(__name__)


def _in_window(ts: str | None, since: str | None, until: str | None) -> bool:
    if not ts:
        return False
    day = ts[:10]
    return (not since or day >= since) and (not until or day <= until)


def ensure_clone(repo: RepoRef, path: Path) -> Path:
    if (path / ".git").exists():
        subprocess.run(["git", "-C", str(path), "fetch", "--tags", "--quiet", "origin"], check=False)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("cloning %s into %s", repo.html_url, path)
    subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", repo.html_url, str(path)], check=True)
    return path


# ---------------------------------------------------------------------------- git
def collect_git(snap: Snapshot, repo: RepoRef, git_path: Path, ref: str, since: str | None, until: str | None) -> None:
    collector = GitHistoryCollector(git_path)
    if collector.git.is_shallow():
        snap.record_error("git", "clone is shallow: release ancestry may be incomplete")
    sha = collector.git.resolve(ref)
    snap.manifest["source_ref"] = {"ref": ref, "sha": sha}

    prs, commits = collector.collect_pull_requests(sha, since, until)
    snap.write("git", "pull_requests", prs)
    snap.write("git", "commits", commits)

    tags = collector.collect_tags()
    snap.write("git", "tags", tags)
    merge_shas = {p["merge_commit_sha"] for p in prs}
    snap.write("git", "release_contains", collector.release_containment(tags, merge_shas))

    changelog_path, releases = collector.collect_changelog(sha)
    snap.manifest["changelog_path"] = changelog_path
    snap.write("git", "changelog", releases)

    docs = collector.collect_docs(sha)
    snap.write(
        "git",
        "doc_chunks",
        [
            {"path": d["path"], "revision": sha, "index": i, **chunk}
            for d in docs
            for i, chunk in enumerate(chunk_document(d["text"]))
        ],
    )
    code = collector.collect_code(sha)
    snap.write(
        "git",
        "code_chunks",
        [{"path": c["path"], "revision": sha, **chunk} for c in code for chunk in chunk_python(c["text"])],
    )
    # Release history is "complete" for earliest-release claims when tags exist and the clone is not shallow.
    snap.manifest["release_history_complete"] = bool(tags) and not collector.git.is_shallow()


# ---------------------------------------------------------------------------- api
def collect_api(
    snap: Snapshot,
    repo: RepoRef,
    client: GitHubClient,
    since: str | None,
    until: str | None,
    max_issues: int,
    max_prs: int,
    extra_issue_numbers: set[int] | None = None,
) -> None:
    base = f"/repos/{repo.full_name}"
    meta = client.get(base)
    if meta.get("private"):
        raise GitHubError(403, base, "only public repositories are supported")
    snap.manifest["repository"].update(
        {"default_branch": meta.get("default_branch"), "visibility": "public", "description": meta.get("description")}
    )

    # ---- Stage A: index issues, PRs, releases
    issues: list[dict[str, Any]] = []
    for item in client.paginate(f"{base}/issues", {"state": "all", "sort": "created", "direction": "desc"}):
        created = item.get("created_at", "")
        if until and created[:10] > until:
            continue
        if since and created[:10] < since:
            break  # sorted by created desc
        if "pull_request" in item:
            continue
        issues.append(_slim_issue(item))
        if len(issues) >= max_issues:
            snap.record_error("api", f"issue limit {max_issues} reached; older issues not collected")
            break

    pulls: list[dict[str, Any]] = []
    for item in client.paginate(f"{base}/pulls", {"state": "closed", "sort": "created", "direction": "desc"}):
        created = item.get("created_at", "")
        if since and created[:10] < since:
            break
        if not item.get("merged_at") or not _in_window(item["merged_at"], since, until):
            continue
        pulls.append(_slim_pull(item))
        if len(pulls) >= max_prs:
            snap.record_error("api", f"PR limit {max_prs} reached; older PRs not collected")
            break
    releases = [
        {
            "tag": r["tag_name"],
            "name": r.get("name"),
            "published_at": r.get("published_at"),
            "body": r.get("body") or "",
            "url": r.get("html_url"),
            "prerelease": r.get("prerelease", False),
        }
        for r in client.paginate(f"{base}/releases")
    ]

    # ---- Stage B: enrich PRs with files and verified closing references
    pr_files, closing = [], []
    linked_issue_numbers: set[int] = set(extra_issue_numbers or ())
    for pr in pulls:
        n = pr["number"]
        try:
            for f in client.paginate(f"{base}/pulls/{n}/files", max_items=300):
                pr_files.append(
                    {
                        "pr_number": n,
                        "path": f["filename"],
                        "old_path": f.get("previous_filename"),
                        "status": f.get("status"),
                        "additions": f.get("additions", 0),
                        "deletions": f.get("deletions", 0),
                        "patch": f.get("patch"),
                    }
                )
        except GitHubError as exc:
            snap.record_error("api.pr_files", str(exc))
        if client.token:
            try:
                data = client.graphql(CLOSING_REFS_QUERY, {"owner": repo.owner, "name": repo.name, "number": n})
                nodes = (((data.get("repository") or {}).get("pullRequest") or {}).get("closingIssuesReferences") or {}).get(
                    "nodes", []
                )
                for node in nodes:
                    same_repo = node["repository"]["nameWithOwner"].lower() == repo.full_name.lower()
                    if same_repo:
                        closing.append({"pr_number": n, "issue_number": node["number"], "basis": "github_closing_reference"})
                        linked_issue_numbers.add(node["number"])
            except GitHubError as exc:
                snap.record_error("api.graphql", str(exc))
        for ref in find_references(f"{pr['title']}\n{pr['body']}", repo.owner, repo.name, n):
            linked_issue_numbers.add(ref.number)

    # Hydrate linked issues that fall outside the issue window (e.g. reported years before the fix).
    have = {i["number"] for i in issues}
    pr_numbers = {p["number"] for p in pulls}
    for number in sorted(linked_issue_numbers - have - pr_numbers):
        try:
            item = client.get(f"{base}/issues/{number}")
        except GitHubError as exc:
            snap.record_error("api.issue", str(exc))
            continue
        if "pull_request" not in item:
            issues.append(_slim_issue(item))

    # Enrich issues with comments and timeline events (close/cross-reference evidence).
    comments, timeline = [], []
    for issue in issues:
        n = issue["number"]
        if issue.get("comments", 0):
            try:
                for c in client.paginate(f"{base}/issues/{n}/comments", max_items=100):
                    comments.append(
                        {
                            "issue_number": n,
                            "id": c["id"],
                            "body": c.get("body") or "",
                            "author": (c.get("user") or {}).get("login"),
                            "created_at": c.get("created_at"),
                            "url": c.get("html_url"),
                        }
                    )
            except GitHubError as exc:
                snap.record_error("api.comments", str(exc))
        try:
            for ev in client.paginate(f"{base}/issues/{n}/timeline", max_items=200):
                slim = _slim_timeline_event(n, ev)
                if slim:
                    timeline.append(slim)
        except GitHubError as exc:
            snap.record_error("api.timeline", str(exc))

    snap.write("api", "issues", issues)
    snap.write("api", "pull_requests", pulls)
    snap.write("api", "pr_files", pr_files)
    snap.write("api", "closing_references", closing)
    snap.write("api", "comments", comments)
    snap.write("api", "timeline", timeline)
    snap.write("api", "releases", releases)


def _slim_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item["number"],
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "state": item.get("state"),
        "state_reason": item.get("state_reason"),
        "labels": [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in item.get("labels", [])],
        "created_at": item.get("created_at"),
        "closed_at": item.get("closed_at"),
        "author": (item.get("user") or {}).get("login"),
        "comments": item.get("comments", 0),
        "url": item.get("html_url"),
    }


def _slim_pull(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item["number"],
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "merged_at": item.get("merged_at"),
        "merge_commit_sha": item.get("merge_commit_sha"),
        "head_ref": (item.get("head") or {}).get("ref"),
        "base_ref": (item.get("base") or {}).get("ref"),
        "author": (item.get("user") or {}).get("login"),
        "url": item.get("html_url"),
        "source": "api",
    }


def _slim_timeline_event(issue_number: int, ev: dict[str, Any]) -> dict[str, Any] | None:
    kind = ev.get("event")
    if kind == "cross-referenced":
        src = (ev.get("source") or {}).get("issue") or {}
        if "pull_request" not in src:
            return None
        return {
            "issue_number": issue_number,
            "event": kind,
            "pr_number": src.get("number"),
            "pr_repo": ((src.get("repository") or {}).get("full_name") or ""),
            "pr_merged_at": (src.get("pull_request") or {}).get("merged_at"),
            "created_at": ev.get("created_at"),
        }
    if kind == "closed":
        return {
            "issue_number": issue_number,
            "event": kind,
            "commit_id": ev.get("commit_id"),
            "created_at": ev.get("created_at"),
        }
    if kind in ("connected", "disconnected"):
        return {"issue_number": issue_number, "event": kind, "created_at": ev.get("created_at")}
    return None


# ---------------------------------------------------------------------------- CLI
def default_snapshot_id(repo: RepoRef) -> str:
    return f"{repo.owner}-{repo.name}-{datetime.utcnow():%Y%m%d}".lower()


def fetch(
    url: str,
    sources: list[str],
    snapshot_id: str | None = None,
    git_path: Path | None = None,
    ref: str = "HEAD",
    since: str | None = None,
    until: str | None = None,
    max_issues: int = 2000,
    max_prs: int = 1500,
) -> Snapshot:
    settings = get_settings()
    repo = parse_repo_url(url)
    snap = Snapshot.create(
        settings.snapshots_dir,
        snapshot_id or default_snapshot_id(repo),
        repository={"owner": repo.owner, "name": repo.name, "url": repo.html_url, "id": repo.id},
        collected_at=utc_now(),
        window={"since": since, "until": until},
        sources=sources,
        extractor_version=EXTRACTOR_VERSION,
    )
    snap.manifest["status"] = "collecting"
    snap.save_manifest()
    linked_from_git: set[int] = set()

    if "git" in sources:
        path = git_path or PROJECT_ROOT / "data" / "repos" / repo.owner / repo.name
        ensure_clone(repo, path)
        collect_git(snap, repo, path, ref, since, until)
        for pr in snap.read("git", "pull_requests"):
            for r in find_references(f"{pr['title']}\n{pr['body']}", repo.owner, repo.name, pr["number"]):
                linked_from_git.add(r.number)
        snap.save_manifest()

    if "api" in sources:
        client = GitHubClient(settings.github_token or None, settings.http_cache_dir)
        if not settings.github_token:
            snap.record_error("api", "GITHUB_TOKEN not set: 60 requests/hour and no GraphQL closing references")
        try:
            collect_api(snap, repo, client, since, until, max_issues, max_prs, linked_from_git)
        except GitHubError as exc:
            snap.record_error("api", str(exc))
            snap.manifest["api_status"] = "failed"
        else:
            snap.manifest["api_status"] = "complete"

    snap.manifest["status"] = "collected"
    snap.finalize_checksums()
    return snap


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="https://github.com/<owner>/<repo>")
    p.add_argument("--sources", default="git,api")
    p.add_argument("--snapshot-id")
    p.add_argument("--git-path", type=Path)
    p.add_argument("--ref", default="HEAD", help="git ref the snapshot is frozen at")
    p.add_argument("--since", help="YYYY-MM-DD (PR merge / issue creation window start)")
    p.add_argument("--until", help="YYYY-MM-DD")
    p.add_argument("--max-issues", type=int, default=2000)
    p.add_argument("--max-prs", type=int, default=1500)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    snap = fetch(a.url, a.sources.split(","), a.snapshot_id, a.git_path, a.ref, a.since, a.until, a.max_issues, a.max_prs)
    print(f"snapshot written to {snap.root}")
    for source, counts in snap.manifest["collections"].items():
        print(f"  {source}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for err in snap.manifest["errors"]:
        print(f"  WARNING [{err['stage']}] {err['message']}")


if __name__ == "__main__":
    main()
