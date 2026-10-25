"""Expand a candidate into its fix path: issue -> PR -> commit -> files/hunks -> releases."""

from __future__ import annotations

import re
from typing import Any

from graph import queries

from .query_parser import ParsedQuery

STATUS_ORDER = {"verified": 0, "self": 1, "referenced": 2, "inferred": 3}
LINK_ORDER = {"CLOSES": 0, "SELF": 1, "REFERENCES": 2, "POSSIBLY_ADDRESSES": 3}


def is_test_path(path: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$", path))


def score_hunk(h: dict[str, Any], q: ParsedQuery) -> float:
    diff = h.get("diff") or ""
    changed = "\n".join(line for line in diff.splitlines() if line[:1] in "+-").lower()
    s = 0.0
    if not is_test_path(h.get("path", "")):
        s += 2.0
    for t in q.exception_types:
        if t.lower() in diff.lower():
            s += 1.5
        if t in (h.get("exceptions") or []):
            s += 0.5
    for term in q.key_terms:
        if term in changed:
            s += 0.5
    for fn in q.function_names:
        if fn in (h.get("section") or "") or f"def {fn}" in diff:
            s += 1.0
    return s - 0.0005 * len(diff)


def expand_fix_path(searcher, anchor_id: str, anchor_label: str, q: ParsedQuery, max_hunks: int = 4) -> list[dict[str, Any]]:
    """Return one record per (anchor, PR) pair, strongest evidence first."""
    cypher = queries.FIX_PATH_FOR_PR if anchor_label == "PullRequest" else queries.FIX_PATH_FOR_ISSUE
    rows = searcher._run("fix_path", cypher, anchor_id=anchor_id)
    out = []
    for r in rows:
        if r["pr"] is None and r["issue"] is None:
            continue
        links = sorted(r["links"], key=lambda l: (LINK_ORDER.get(l.get("type"), 9), STATUS_ORDER.get(l.get("verification_status"), 9)))
        hunks = sorted(r["hunks"], key=lambda h: -score_hunk(h, q))[:max_hunks] if r["pr"] else []
        out.append({**r, "links": links, "hunks": hunks, "all_hunk_count": len(r["hunks"])})
    out.sort(
        key=lambda r: (
            STATUS_ORDER.get(r["links"][0].get("verification_status"), 9) if r["links"] else 9,
            0 if any(x.get("earliest") for x in r["releases"]) else 1,
        )
    )
    return out
