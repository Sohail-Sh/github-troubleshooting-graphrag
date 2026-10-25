"""Graph validation gates run before anything is written to Neo4j.

Rules:
  * every relationship has both endpoints
  * every entity id belongs to the ingested repository (no cross-repo leakage)
  * CLOSES must be ``verified``; REFERENCES must not claim ``verified``; POSSIBLY_ADDRESSES is ``inferred``
  * evidence source URLs point at the ingested repository
  * relationship endpoint labels match the schema
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .ids import RepoRef

# relationship type -> (allowed start labels, allowed end labels)
SCHEMA: dict[str, tuple[set[str], set[str]]] = {
    "HAS_ISSUE": ({"Repository"}, {"Issue"}),
    "HAS_PR": ({"Repository"}, {"PullRequest"}),
    "HAS_FILE": ({"Repository"}, {"File"}),
    "HAS_DOC": ({"Repository"}, {"Doc"}),
    "HAS_RELEASE": ({"Repository"}, {"Release"}),
    "CLOSES": ({"PullRequest"}, {"Issue"}),
    "REFERENCES": ({"PullRequest"}, {"Issue"}),
    "POSSIBLY_ADDRESSES": ({"PullRequest"}, {"Issue"}),
    "HAS_COMMENT": ({"Issue"}, {"Comment"}),
    "MENTIONS_ERROR": ({"Issue", "PullRequest", "ChangelogEntry"}, {"ErrorSignature"}),
    "MENTIONS_EXCEPTION": ({"Issue", "PullRequest", "ChangelogEntry"}, {"ExceptionType"}),
    "OF_TYPE": ({"ErrorSignature"}, {"ExceptionType"}),
    "HAS_STACK_FRAME": ({"Issue", "PullRequest", "ChangelogEntry"}, {"StackFrame"}),
    "CHANGES": ({"PullRequest"}, {"File"}),
    "HAS_PATCH": ({"PullRequest"}, {"PatchHunk"}),
    "IN_FILE": ({"PatchHunk", "StackFrame"}, {"File"}),
    "TOUCHES_EXCEPTION": ({"PatchHunk"}, {"ExceptionType"}),
    "HAS_REVISION": ({"File"}, {"FileRevision"}),
    "PRODUCED": ({"Commit"}, {"FileRevision"}),
    "MERGED_AS": ({"PullRequest"}, {"Commit"}),
    "HAS_COMMIT": ({"PullRequest"}, {"Commit"}),
    "CONTAINS_COMMIT": ({"Release"}, {"Commit"}),
    "HAS_NOTE": ({"Release"}, {"ChangelogEntry"}),
    "MENTIONS": ({"ChangelogEntry"}, {"PullRequest", "Issue"}),
    "HAS_CHUNK": ({"Doc", "FileRevision"}, {"DocChunk", "CodeChunk"}),
}
GLOBAL_ID_PREFIXES = ("error:", "exception:", "frame:")
REQUIRED_STATUS = {"CLOSES": {"verified"}, "REFERENCES": {"referenced"}, "POSSIBLY_ADDRESSES": {"inferred"}}


def validate_graph(nodes: list[dict[str, Any]], rels: list[dict[str, Any]], repo: RepoRef) -> dict[str, Any]:
    by_id = {n["id"]: n for n in nodes}
    problems: Counter[str] = Counter()
    examples: dict[str, str] = {}
    dropped: list[tuple[str, str, str]] = []

    def flag(kind: str, detail: str, key: tuple[str, str, str] | None = None) -> None:
        problems[kind] += 1
        examples.setdefault(kind, detail)
        if key is not None:
            dropped.append(key)

    for n in nodes:
        if not (n["id"].startswith(repo.id) or n["id"].startswith(GLOBAL_ID_PREFIXES)):
            flag("foreign_node_id", n["id"])

    for r in rels:
        key = (r["type"], r["start"], r["end"])
        s, e = by_id.get(r["start"]), by_id.get(r["end"])
        if s is None or e is None:
            flag("missing_endpoint", str(key), key)
            continue
        allowed = SCHEMA.get(r["type"])
        if allowed is None:
            flag("unknown_relationship_type", r["type"], key)
            continue
        if not (set(s["labels"]) & allowed[0]) or not (set(e["labels"]) & allowed[1]):
            flag("schema_violation", f"{r['type']}: {s['labels']} -> {e['labels']}", key)
            continue
        status = r["props"].get("verification_status")
        if r["type"] in REQUIRED_STATUS and status not in REQUIRED_STATUS[r["type"]]:
            flag("evidence_status_violation", f"{r['type']} with status {status}", key)
            continue
        url = r["props"].get("source_url")
        if url and not url.lower().startswith(repo.html_url.lower()):
            flag("foreign_source_url", url, key)

    return {
        "ok": not problems,
        "problems": dict(problems),
        "examples": examples,
        "dropped_relationships": len(dropped),
        "dropped_keys": dropped,
    }
