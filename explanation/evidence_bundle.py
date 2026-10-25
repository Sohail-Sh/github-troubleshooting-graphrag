"""The evidence bundle: the only information the explanation layer is allowed to use.

Every citable item gets an id (E1, E2, ...) so generated text can be checked claim by claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

STATUS_TEXT = {
    "verified": "verified by GitHub (closing reference / timeline)",
    "referenced": "referenced in PR or commit text, not confirmed by GitHub",
    "inferred": "inferred heuristically, not confirmed",
    "self": "merged pull request (no linked issue)",
}


@dataclass
class EvidenceItem:
    eid: str
    kind: str  # issue | comment | pull_request | link | commit | patch | release | release_note | doc | code
    label: str
    url: str | None
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FixEvidence:
    pr_number: int
    pr_title: str
    pr_url: str
    link_type: str
    verification_status: str
    bases: list[str]
    link_source_url: str | None
    merge_commit: dict[str, Any] | None
    files: list[dict[str, Any]]
    hunks: list[dict[str, Any]]
    hunk_total: int
    releases: list[dict[str, Any]]
    containing_release_count: int
    earliest_release: str | None
    release_notes: list[dict[str, Any]]
    evidence_ids: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    status: str  # fix_found | match_without_fix | no_reliable_match
    repository: str
    query: dict[str, Any]
    anchor_id: str | None = None
    anchor_label: str | None = None
    issue: dict[str, Any] | None = None
    match: dict[str, Any] | None = None
    fixes: list[FixEvidence] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    context: list[dict[str, Any]] = field(default_factory=list)
    items: dict[str, EvidenceItem] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    release_history_complete: bool = False

    @property
    def primary_fix(self) -> FixEvidence | None:
        return self.fixes[0] if self.fixes else None

    def add_item(self, kind: str, label: str, url: str | None, text: str = "", **meta: Any) -> str:
        eid = f"E{len(self.items) + 1}"
        self.items[eid] = EvidenceItem(eid, kind, label, url, text[:4000], meta)
        return eid

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["items"] = {k: asdict(v) for k, v in self.items.items()}
        return d


def build_bundle(
    status: str,
    repository: str,
    query: dict[str, Any],
    candidate: dict[str, Any] | None,
    paths: list[dict[str, Any]],
    context: list[dict[str, Any]],
    release_history_complete: bool,
) -> EvidenceBundle:
    b = EvidenceBundle(status=status, repository=repository, query=query, release_history_complete=release_history_complete)
    if candidate is None:
        b.missing.append("No candidate passed the match threshold (enough key terms and a consistent exception type).")
        return b
    b.anchor_id = candidate["anchor_id"]
    b.anchor_label = candidate["label"]
    b.match = {k: candidate[k] for k in ("rank", "retrieval_score", "final_score", "term_coverage", "match_support", "matched_terms", "missing_terms", "vector_similarity", "exception_match", "frame_match", "reasons", "evidence_status", "already_fixed_in_user_version")}

    issue = next((p["issue"] for p in paths if p.get("issue")), None)
    if issue:
        b.issue = issue
        if issue.get("hydrated"):
            b.issue["eid"] = b.add_item("issue", f"Issue #{issue['number']}: {issue.get('title')}", issue.get("url"), issue.get("body") or "")
        else:
            b.issue["eid"] = b.add_item("issue", f"Issue #{issue['number']} (text not ingested)", issue.get("url"))
            b.missing.append(
                f"Issue #{issue['number']} is known only from references; its title, body and comments were not "
                "collected (run the GitHub API collector to hydrate it)."
            )
            if not issue.get("type_confirmed"):
                b.missing.append(f"#{issue['number']} is not confirmed to be an issue (it could be an unmerged PR).")
        comments = next((p.get("comments") for p in paths if p.get("comments")), []) or []
        terms = [t.lower() for t in query.get("key_terms", [])]
        ranked = sorted(comments, key=lambda c: -sum(t in (c.get("body") or "").lower() for t in terms))[:3]
        for c in ranked:
            c["eid"] = b.add_item("comment", f"Comment by {c.get('author')} on #{issue['number']}", c.get("url"), c.get("body") or "")
            b.comments.append(c)

    for p in paths:
        pr = p.get("pr")
        if not pr:
            continue
        link = p["links"][0] if p["links"] else {}
        fx = FixEvidence(
            pr_number=pr["number"],
            pr_title=pr.get("title") or "",
            pr_url=pr.get("url"),
            link_type=link.get("type", "NONE"),
            verification_status=link.get("verification_status", "none"),
            bases=sorted({bb for l in p["links"] for bb in (l.get("bases") or [l.get("basis")]) if bb}),
            link_source_url=link.get("source_url"),
            merge_commit=p.get("merge_commit"),
            files=p.get("files", []),
            hunks=p.get("hunks", []),
            hunk_total=p.get("all_hunk_count", 0),
            releases=p.get("releases", []),
            containing_release_count=p.get("containing_release_count", 0),
            earliest_release=next((r["tag"] for r in p.get("releases", []) if r.get("earliest")), None),
            release_notes=p.get("release_notes", []),
        )
        ev = fx.evidence_ids
        ev["pr"] = [b.add_item("pull_request", f"PR #{fx.pr_number}: {fx.pr_title}", fx.pr_url, pr.get("body") or "")]
        if fx.link_type != "SELF":
            ev["link"] = [
                b.add_item(
                    "link",
                    f"{fx.link_type} PR #{fx.pr_number} -> issue #{issue['number'] if issue else '?'} ({fx.verification_status})",
                    fx.link_source_url,
                    "; ".join(f"{l.get('type')}:{l.get('verification_status')}:{l.get('basis')} {l.get('snippet') or ''}".strip() for l in p["links"]),
                    status=fx.verification_status,
                    bases=fx.bases,
                )
            ]
        if fx.merge_commit:
            ev["commit"] = [b.add_item("commit", f"Merge commit {fx.merge_commit['short_sha']}", fx.merge_commit.get("url"), fx.merge_commit.get("message") or "")]
        ev["patch"] = [b.add_item("patch", f"{h['path']} {h.get('section') or ''}".strip(), h.get("url"), h.get("diff") or "", path=h["path"]) for h in fx.hunks]
        ev["release"] = [
            b.add_item(
                "release",
                f"Release {r['tag']}" + (" (earliest containing release)" if r.get("earliest") else " (contains the fix)"),
                r.get("source_url") or r.get("url"),
                f"{r['tag']} published {r.get('published_at')}; basis: {r.get('basis')}",
                earliest=bool(r.get("earliest")),
            )
            for r in fx.releases[:2]
        ]
        ev["release_note"] = [b.add_item("release_note", f"Release notes {n['version']}", None, n["text"]) for n in fx.release_notes[:2]]
        b.fixes.append(fx)

    for c in context[:3]:
        c["eid"] = b.add_item("doc" if c["label"] == "DocChunk" else "code", c["title"], c.get("url"), c.get("text") or "")
        b.context.append(c)

    # ---- disclose what is missing
    fx = b.primary_fix
    if not b.fixes:
        b.missing.append("No pull request in the graph is linked to this report.")
    elif fx is not None:
        if fx.verification_status == "referenced":
            b.missing.append("The issue-PR link comes from text references; GitHub's closing reference was not confirmed.")
        if fx.verification_status == "inferred":
            b.missing.append("The only issue-PR link is heuristic (inferred); treat it as a lead, not a known fix.")
        if not fx.merge_commit:
            b.missing.append("No merge commit is recorded for the PR.")
        if not fx.earliest_release:
            if fx.containing_release_count:
                b.missing.append("Releases containing the fix are known, but the earliest one cannot be claimed (release history incomplete).")
            else:
                b.missing.append("No release in the snapshot contains the fix's merge commit.")
        if fx.hunk_total == 0:
            b.missing.append("No patch hunks were collected for the PR.")
    if not query.get("package_versions"):
        b.missing.append("Your installed library/Python versions were not provided, so applicability to your environment is unknown.")
    return b
