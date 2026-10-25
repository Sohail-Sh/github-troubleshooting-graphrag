"""Turn a raw snapshot (git + API evidence) into a normalized, provenance-aware property graph.

Output: ``graph/nodes.jsonl`` and ``graph/relationships.jsonl`` inside the snapshot. Each node is
``{"id", "labels", "props"}``; each relationship is ``{"type", "start", "end", "props"}``. Evidence
relationships carry ``basis``, ``verification_status``, ``source_url``, ``retrieved_at`` and
``extractor_version``.
"""

from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from config import EXTRACTOR_VERSION, get_settings

from . import ids
from .extract_errors import GENERIC_EXCEPTIONS, exception_types_in_code, extract_errors
from .extract_patches import parse_hunks
from .git_history import version_key
from .references import find_references, references_from_branch
from .snapshot import Snapshot, write_jsonl
from .validate_links import validate_graph

log = logging.getLogger(__name__)

STATUS_RANK = {"verified": 3, "referenced": 2, "inferred": 1}
MAX_BODY = 20_000
MAX_SEARCH_TEXT = 8_000


class GraphBuilder:
    def __init__(self, repo: ids.RepoRef, retrieved_at: str):
        self.repo = repo
        self.retrieved_at = retrieved_at
        self.nodes: dict[str, dict[str, Any]] = {}
        self.rels: dict[tuple[str, str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------ primitives
    def node(self, node_id: str, *labels: str, **props: Any) -> str:
        n = self.nodes.setdefault(node_id, {"id": node_id, "labels": [], "props": {}})
        for label in labels:
            if label not in n["labels"]:
                n["labels"].append(label)
        for k, v in props.items():
            if v is not None and (v != "" or k not in n["props"]):
                n["props"][k] = v
        return node_id

    def rel(self, rtype: str, start: str, end: str, **props: Any) -> None:
        key = (rtype, start, end)
        existing = self.rels.get(key)
        props = {k: v for k, v in props.items() if v is not None}
        if existing is None:
            if "basis" in props:
                props.setdefault("bases", [props["basis"]])
            self.rels[key] = {"type": rtype, "start": start, "end": end, "props": props}
            return
        ep = existing["props"]
        if "basis" in props:
            bases = ep.setdefault("bases", [ep.get("basis")])
            if props["basis"] not in bases:
                bases.append(props["basis"])
            # keep the strongest basis as the primary one
            if STATUS_RANK.get(props.get("verification_status", ""), 0) > STATUS_RANK.get(
                ep.get("verification_status", ""), 0
            ):
                ep.update({k: v for k, v in props.items() if k != "bases"})
        else:
            ep.update(props)

    def evidence(self, basis: str, status: str, source_url: str, **extra: Any) -> dict[str, Any]:
        return {
            "basis": basis,
            "verification_status": status,
            "source_url": source_url,
            "retrieved_at": self.retrieved_at,
            "extractor_version": EXTRACTOR_VERSION,
            **extra,
        }

    def searchable(self, node_id: str, title: str, text: str, kind: str) -> None:
        self.node(
            node_id,
            "Searchable",
            search_title=title[:500],
            search_text=(title + "\n" + text).strip()[:MAX_SEARCH_TEXT],
            search_kind=kind,
        )

    # ------------------------------------------------------------------ entities
    def issue_stub(self, number: int) -> str:
        """An issue we only know by number (from a reference). Not yet confirmed to be an issue."""
        iid = ids.issue_id(self.repo, number)
        if iid not in self.nodes:
            self.node(iid, "Issue", number=number, url=self.repo.issue_url(number), hydrated=False, type_confirmed=False)
            self.rel("HAS_ISSUE", self.repo.id, iid)
        return iid

    def file(self, path: str) -> str:
        fid = ids.file_id(self.repo, path)
        if fid not in self.nodes:
            self.node(fid, "File", path=path, repository_id=self.repo.id, name=path.rsplit("/", 1)[-1])
            self.rel("HAS_FILE", self.repo.id, fid)
        return fid

    def exception_type(self, name: str) -> str:
        return self.node(ids.exception_type_id(name), "ExceptionType", name=name)

    def link_errors(self, source_id: str, text: str, origin: str, url: str | None) -> None:
        parsed = extract_errors(text)
        for err in parsed.errors:
            if err.exception_type in GENERIC_EXCEPTIONS:
                continue
            et = self.exception_type(err.exception_type)
            if err.message_pattern:
                sid = ids.error_signature_id(err.exception_type, err.message_pattern)
                self.node(
                    sid,
                    "ErrorSignature",
                    exception_type=err.exception_type,
                    message_pattern=err.message_pattern,
                    example_message=err.message[:300],
                )
                self.rel("OF_TYPE", sid, et)
                self.rel("MENTIONS_ERROR", source_id, sid, origin=origin, source_url=url)
            else:
                self.rel("MENTIONS_EXCEPTION", source_id, et, origin=origin, source_url=url)
        for order, frame in enumerate(parsed.frames):
            path = frame.normalized_path
            fr = self.node(ids.stack_frame_id(path, frame.function_name), "StackFrame", file_path=path, function_name=frame.function_name)
            self.rel("HAS_STACK_FRAME", source_id, fr, line_number=frame.line_number, order=order, origin=origin)


# ---------------------------------------------------------------------- build
def build_graph(snap: Snapshot) -> GraphBuilder:
    m = snap.manifest
    repo = ids.RepoRef(m["repository"]["owner"], m["repository"]["name"])
    g = GraphBuilder(repo, m.get("collected_at", ""))
    g.node(
        repo.id,
        "Repository",
        name=repo.full_name,
        url=repo.html_url,
        default_branch=m["repository"].get("default_branch"),
        snapshot_id=snap.id,
        source_sha=(m.get("source_ref") or {}).get("sha"),
    )

    api_issues = {i["number"]: i for i in snap.read("api", "issues")}
    api_prs = {p["number"]: p for p in snap.read("api", "pull_requests")}
    git_prs = {p["number"]: p for p in snap.read("git", "pull_requests")}
    known_pr_numbers = set(api_prs) | set(git_prs)

    # ---- issues (API)
    comments_by_issue: dict[int, list[dict]] = defaultdict(list)
    for c in snap.read("api", "comments"):
        comments_by_issue[c["issue_number"]].append(c)
    for n, issue in api_issues.items():
        iid = ids.issue_id(repo, n)
        g.node(
            iid,
            "Issue",
            number=n,
            title=issue["title"],
            body=issue["body"][:MAX_BODY],
            state=issue.get("state"),
            state_reason=issue.get("state_reason"),
            labels=issue.get("labels", []),
            created_at=issue.get("created_at"),
            closed_at=issue.get("closed_at"),
            author=issue.get("author"),
            url=issue.get("url") or repo.issue_url(n),
            hydrated=True,
            type_confirmed=True,
        )
        g.rel("HAS_ISSUE", repo.id, iid)
        g.searchable(iid, f"Issue #{n}: {issue['title']}", issue["body"], "issue")
        g.link_errors(iid, f"{issue['title']}\n{issue['body']}", "issue_body", issue.get("url"))
        for c in comments_by_issue.get(n, []):
            cid = ids.comment_id(repo, c["id"])
            g.node(cid, "Comment", body=c["body"][:5000], author=c.get("author"), created_at=c.get("created_at"), url=c.get("url"))
            g.rel("HAS_COMMENT", iid, cid)
            g.searchable(cid, f"Comment on issue #{n}", c["body"], "comment")
            g.link_errors(iid, c["body"], "comment", c.get("url"))

    # ---- commits
    commits = {c["sha"]: c for c in snap.read("git", "commits")}
    for sha, c in commits.items():
        g.node(
            ids.commit_id(repo, sha),
            "Commit",
            sha=sha,
            short_sha=sha[:8],
            message=c["message"][:5000],
            committed_at=c.get("committed_at"),
            authored_at=c.get("authored_at"),
            author_name=c.get("author_name"),
            parents=c.get("parents", []),
            url=repo.commit_url(sha),
        )

    # ---- pull requests (merge git + API views by number)
    pr_files_api: dict[int, list[dict]] = defaultdict(list)
    for f in snap.read("api", "pr_files"):
        pr_files_api[f["pr_number"]].append(f)

    for n in sorted(known_pr_numbers):
        gp, ap = git_prs.get(n, {}), api_prs.get(n, {})
        pid = ids.pr_id(repo, n)
        title = ap.get("title") or gp.get("title") or ""
        body = ap.get("body") or gp.get("body") or ""
        merge_sha = ap.get("merge_commit_sha") or gp.get("merge_commit_sha")
        url = ap.get("url") or repo.pr_url(n)
        g.node(
            pid,
            "PullRequest",
            number=n,
            title=title,
            body=body[:MAX_BODY],
            merged_at=ap.get("merged_at") or gp.get("merged_at"),
            merge_commit_sha=merge_sha,
            merge_style=gp.get("merge_style"),
            head_ref=ap.get("head_ref") or gp.get("head_ref"),
            author=ap.get("author") or gp.get("author_name"),
            url=url,
            sources=sorted({s for s in (gp.get("source"), ap.get("source")) if s}),
        )
        g.rel("HAS_PR", repo.id, pid)
        commit_messages = [commits[s]["message"] for s in gp.get("commit_shas", []) if s in commits]
        g.searchable(pid, f"PR #{n}: {title}", body + "\n" + "\n".join(commit_messages), "pull_request")
        g.link_errors(pid, f"{title}\n{body}\n" + "\n".join(commit_messages), "pull_request", url)

        if merge_sha:
            cid = ids.commit_id(repo, merge_sha)
            if cid not in g.nodes:
                g.node(cid, "Commit", sha=merge_sha, short_sha=merge_sha[:8], url=repo.commit_url(merge_sha), message="")
            g.rel("MERGED_AS", pid, cid, **g.evidence(f"{gp.get('merge_style') or 'api'}_record", "verified", url))
        for s in gp.get("commit_shas", []):
            g.rel("HAS_COMMIT", pid, ids.commit_id(repo, s))

        # files + hunks: prefer git (full diff), fall back to API `patch`
        files = gp.get("files") or [
            {
                "path": f["path"],
                "old_path": f.get("old_path"),
                "status": f.get("status"),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "hunks": [
                    {
                        "header": h.header,
                        "section": h.section,
                        "old_start": h.old_start,
                        "old_lines": h.old_lines,
                        "new_start": h.new_start,
                        "new_lines": h.new_lines,
                        "additions": h.additions,
                        "deletions": h.deletions,
                        "diff": h.diff,
                    }
                    for h in parse_hunks(f.get("patch") or "")
                ],
            }
            for f in pr_files_api.get(n, [])
        ]
        g.node(pid, changed_file_count=len(files))
        for f in files:
            fid = g.file(f["path"])
            g.rel("CHANGES", pid, fid, status=f.get("status"), additions=f.get("additions"), deletions=f.get("deletions"), old_path=f.get("old_path"))
            if merge_sha and f.get("status") != "removed":
                rid = ids.file_revision_id(repo, f["path"], merge_sha)
                g.node(rid, "FileRevision", path=f["path"], commit_sha=merge_sha, url=repo.blob_url(merge_sha, f["path"]))
                g.rel("HAS_REVISION", fid, rid)
                g.rel("PRODUCED", ids.commit_id(repo, merge_sha), rid)
            for idx, h in enumerate(f.get("hunks", [])):
                hid = ids.hunk_id(repo, n, f["path"], idx)
                g.node(
                    hid,
                    "PatchHunk",
                    path=f["path"],
                    diff=h["diff"],
                    section=h.get("section", ""),
                    old_start=h["old_start"],
                    new_start=h["new_start"],
                    additions=h["additions"],
                    deletions=h["deletions"],
                    index=idx,
                    pr_changed_files=len(files),
                    url=f"{url}/files",
                )
                g.rel("HAS_PATCH", pid, hid)
                g.rel("IN_FILE", hid, fid)
                g.searchable(hid, f"{f['path']} {h.get('section', '')}".strip(), h["diff"], "patch")
                changed = "\n".join(line for line in h["diff"].splitlines() if line[:1] in "+-")
                for name in exception_types_in_code(h["diff"]):
                    g.rel("TOUCHES_EXCEPTION", hid, g.exception_type(name), in_changed_lines=name in changed)

        # ---- PR -> issue evidence
        link_pr_to_issues(g, n, pid, title, body, url, ap.get("head_ref") or gp.get("head_ref"), gp, commits, known_pr_numbers)

    # ---- API verified closing references and timeline evidence
    commit_to_pr: dict[str, int] = {}
    for n, gp in git_prs.items():
        for s in [gp.get("merge_commit_sha"), *gp.get("commit_shas", [])]:
            if s:
                commit_to_pr[s] = n
    for n, ap in api_prs.items():
        if ap.get("merge_commit_sha"):
            commit_to_pr[ap["merge_commit_sha"]] = n

    for row in snap.read("api", "closing_references"):
        if row["issue_number"] in known_pr_numbers:
            continue
        iid = ids.issue_id(repo, row["issue_number"])
        if iid not in g.nodes:
            g.issue_stub(row["issue_number"])
        g.rel("CLOSES", ids.pr_id(repo, row["pr_number"]), iid, **g.evidence(row["basis"], "verified", repo.pr_url(row["pr_number"])))

    for ev in snap.read("api", "timeline"):
        iid = ids.issue_id(repo, ev["issue_number"])
        if iid not in g.nodes:
            continue
        if ev["event"] == "closed" and ev.get("commit_id") in commit_to_pr:
            prn = commit_to_pr[ev["commit_id"]]
            g.rel(
                "CLOSES",
                ids.pr_id(repo, prn),
                iid,
                **g.evidence("timeline_closed_by_commit", "verified", repo.issue_url(ev["issue_number"]), commit_sha=ev["commit_id"]),
            )
        elif ev["event"] == "cross-referenced" and ev.get("pr_number") in known_pr_numbers:
            if (ev.get("pr_repo") or repo.full_name).lower() != repo.full_name.lower():
                continue
            g.rel(
                "REFERENCES",
                ids.pr_id(repo, ev["pr_number"]),
                iid,
                **g.evidence("timeline_cross_reference", "referenced", repo.issue_url(ev["issue_number"])),
            )

    add_inferred_frame_links(g, repo)
    add_releases(g, snap, repo, commit_to_pr, known_pr_numbers)
    add_docs_and_code(g, snap, repo)
    return g


def link_pr_to_issues(g, n, pid, title, body, url, head_ref, gp, commits, known_pr_numbers) -> None:
    repo = g.repo
    texts = [("pr_text", f"{title}\n{body}", url)]
    for s in gp.get("commit_shas", []):
        if s in commits:
            texts.append(("commit_message", commits[s]["message"], repo.commit_url(s)))
    for origin, text, src in texts:
        for ref in find_references(text, repo.owner, repo.name, n):
            if ref.number in known_pr_numbers:
                continue  # PR-to-PR mentions are not fix evidence for an issue
            iid = g.issue_stub(ref.number)
            basis = f"{origin}_{ref.basis}"
            g.rel(
                "REFERENCES",
                pid,
                iid,
                **g.evidence(basis, "referenced", src, closing_keyword=ref.closing_keyword, snippet=ref.snippet),
            )
    for ref in references_from_branch(head_ref or "", n):
        if ref.number in known_pr_numbers:
            continue
        iid = g.issue_stub(ref.number)
        g.rel("POSSIBLY_ADDRESSES", pid, iid, **g.evidence("branch_name_number", "inferred", url, snippet=ref.snippet))


def add_inferred_frame_links(g: GraphBuilder, repo: ids.RepoRef) -> None:
    """Issue stack frame function == function touched by a later PR, with no explicit link: *inferred* only."""
    frames_by_issue: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for (rtype, start, end), _ in g.rels.items():
        if rtype == "HAS_STACK_FRAME" and g.nodes[start]["labels"][0] == "Issue":
            fp = g.nodes[end]["props"]
            frames_by_issue[start].add((fp["file_path"], fp["function_name"]))
    if not frames_by_issue:
        return
    linked = {(s, e) for (t, s, e) in g.rels if t in ("CLOSES", "REFERENCES")}
    pr_funcs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for (rtype, start, end), _ in list(g.rels.items()):
        if rtype == "HAS_PATCH":
            h = g.nodes[end]["props"]
            func = re.findall(r"def (\w+)", h.get("section", "") + "\n" + h.get("diff", ""))
            for fn in func:
                pr_funcs[start].add((h["path"], fn))
    for iid, frames in frames_by_issue.items():
        created = g.nodes[iid]["props"].get("created_at") or ""
        candidates = []
        for pid, funcs in pr_funcs.items():
            if (pid, iid) in linked:
                continue
            merged = g.nodes[pid]["props"].get("merged_at") or ""
            if merged < created:
                continue
            hits = {fn for (path, fn) in funcs for (fpath, ffn) in frames if ffn == fn and (path.endswith(fpath) or fpath.endswith(path))}
            if hits:
                candidates.append((merged, pid, sorted(hits)))
        for merged, pid, hits in sorted(candidates)[:3]:
            g.rel(
                "POSSIBLY_ADDRESSES",
                pid,
                iid,
                **g.evidence("stack_frame_function_overlap", "inferred", g.nodes[pid]["props"]["url"], functions=hits),
            )


def add_releases(g: GraphBuilder, snap: Snapshot, repo: ids.RepoRef, commit_to_pr: dict[str, int], known_pr_numbers: set[int]) -> None:
    complete = bool(snap.manifest.get("release_history_complete"))
    tags = {t["tag"]: t for t in snap.read("git", "tags")}
    api_releases = {r["tag"]: r for r in snap.read("api", "releases")}
    for tag in sorted(set(tags) | set(api_releases), key=version_key):
        t, r = tags.get(tag, {}), api_releases.get(tag, {})
        rid = ids.release_id(repo, tag)
        g.node(
            rid,
            "Release",
            tag=tag,
            version=tag.lstrip("v"),
            published_at=r.get("published_at") or t.get("tagged_at"),
            commit_sha=t.get("commit_sha"),
            notes=(r.get("body") or "")[:MAX_BODY],
            url=r.get("url") or repo.release_url(tag),
        )
        g.rel("HAS_RELEASE", repo.id, rid)

    # ancestry: which releases contain each PR merge commit
    containing: dict[str, list[str]] = defaultdict(list)
    for row in snap.read("git", "release_contains"):
        containing[row["commit_sha"]].append(row["tag"])
    for sha, tag_list in containing.items():
        tag_list.sort(key=version_key)
        for i, tag in enumerate(tag_list):
            g.rel(
                "CONTAINS_COMMIT",
                ids.release_id(repo, tag),
                ids.commit_id(repo, sha),
                **g.evidence(
                    "git_tag_ancestry",
                    "verified",
                    repo.compare_url(tag, sha),
                    earliest_containing=(i == 0 and complete),
                ),
            )

    # release notes (changelog) -> entries -> PR/issue mentions
    by_version = {t.lstrip("v"): t for t in tags}
    for section in snap.read("git", "changelog"):
        tag = by_version.get(section["version"]) or by_version.get(section["version"] + ".0")
        for idx, entry in enumerate(section["entries"]):
            eid = ids.changelog_id(repo, tag or section["version"], idx)
            g.node(eid, "ChangelogEntry", text=entry["text"], category=entry["category"], version=section["version"], date=section["date"])
            g.searchable(eid, f"Release {section['version']} ({entry['category'] or 'notes'})", entry["text"], "changelog")
            g.link_errors(eid, entry["text"], "changelog", None)
            if tag:
                g.rel("HAS_NOTE", ids.release_id(repo, tag), eid)
            source = repo.blob_url((snap.manifest.get("source_ref") or {}).get("sha", "HEAD"), snap.manifest.get("changelog_path") or "HISTORY.md")
            for ref in find_references(entry["text"], repo.owner, repo.name):
                target = ids.pr_id(repo, ref.number) if ref.number in known_pr_numbers else g.issue_stub(ref.number)
                g.rel("MENTIONS", eid, target, **g.evidence("release_notes_mention", "referenced", source))


def add_docs_and_code(g: GraphBuilder, snap: Snapshot, repo: ids.RepoRef) -> None:
    for c in snap.read("git", "doc_chunks"):
        did = ids.doc_id(repo, c["path"])
        g.node(did, "Doc", path=c["path"], revision=c["revision"], url=repo.blob_url(c["revision"], c["path"]))
        g.rel("HAS_DOC", repo.id, did)
        cid = ids.doc_chunk_id(repo, c["path"], c["revision"], c["index"])
        g.node(cid, "DocChunk", text=c["text"], heading=c.get("heading", ""), url=repo.blob_url(c["revision"], c["path"]), path=c["path"])
        g.rel("HAS_CHUNK", did, cid)
        g.searchable(cid, f"{c['path']} - {c.get('heading', '')}", c["text"], "doc")
    for c in snap.read("git", "code_chunks"):
        fid = g.file(c["path"])
        rid = ids.file_revision_id(repo, c["path"], c["revision"])
        g.node(rid, "FileRevision", path=c["path"], commit_sha=c["revision"], url=repo.blob_url(c["revision"], c["path"]))
        g.rel("HAS_REVISION", fid, rid)
        cid = ids.code_chunk_id(repo, c["path"], c["revision"], c["symbol"])
        g.node(
            cid,
            "CodeChunk",
            text=c["text"],
            symbol=c["symbol"],
            revision=c["revision"],
            path=c["path"],
            start_line=c["start_line"],
            url=repo.blob_url(c["revision"], c["path"], c["start_line"]),
        )
        g.rel("HAS_CHUNK", rid, cid)
        g.searchable(cid, f"{c['path']}::{c['symbol']}", c["text"], "code")


def normalize(snapshot_path: Path) -> dict[str, Any]:
    snap = Snapshot.open(snapshot_path)
    g = build_graph(snap)
    nodes = list(g.nodes.values())
    rels = list(g.rels.values())
    report = validate_graph(nodes, rels, g.repo)
    bad = set(map(tuple, report.pop("dropped_keys")))
    rels = [r for r in rels if (r["type"], r["start"], r["end"]) not in bad]
    write_jsonl(snap.path("graph", "nodes.jsonl"), nodes)
    write_jsonl(snap.path("graph", "relationships.jsonl"), rels)
    label_counts: dict[str, int] = defaultdict(int)
    for n in nodes:
        label_counts[n["labels"][0]] += 1
    rel_counts: dict[str, int] = defaultdict(int)
    status_counts: dict[str, int] = defaultdict(int)
    for r in rels:
        rel_counts[r["type"]] += 1
        if r["type"] in ("CLOSES", "REFERENCES", "POSSIBLY_ADDRESSES"):
            status_counts[f"{r['type']}:{r['props'].get('verification_status')}"] += 1
    snap.manifest["graph"] = {
        "nodes": dict(sorted(label_counts.items())),
        "relationships": dict(sorted(rel_counts.items())),
        "issue_links": dict(sorted(status_counts.items())),
        "validation": report,
    }
    snap.manifest["status"] = "normalized"
    snap.finalize_checksums()
    return snap.manifest["graph"]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Normalize a raw snapshot into graph JSONL")
    p.add_argument("snapshot", help="snapshot id or path")
    a = p.parse_args(argv)
    path = Path(a.snapshot)
    if not path.exists():
        path = get_settings().snapshots_dir / a.snapshot
    logging.basicConfig(level=logging.INFO)
    summary = normalize(path)
    import json

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
