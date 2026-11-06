"""Related Fix Discovery: read-only graph traversal for PRs that share evidence (exception types, changed
files, optionally a function name) with a selected primary fix PR.

These are structurally related *leads*, not verified fixes for the source issue. Nothing here writes to the
graph, promotes a REFERENCES/POSSIBLY_ADDRESSES edge to CLOSES, or attaches a related PR to the evidence
bundle of a different issue - see docs/NODES_2026_ENHANCED_DEMO.md and
Neo4j_NODES2026_Enhancement_Requirements_for_Claude.md section 7 (trust boundaries).

Verified against the live psf/requests snapshot before writing this (docs/ENHANCEMENT_VALIDATION.md):
``PatchHunk.section`` is mostly empty or a class-level header in this dataset, not a function name, so
shared-function evidence is parsed from the diff body instead of trusted from ``section``. Common files
(``tests/test_requests.py``: 42/368 PRs, ``requests/models.py``: 32/368 PRs) are down-weighted continuously
by degree rather than given full credit, the same decay shape ``retrieval/hybrid_search.breadth_weight``
already uses for sweeping PRs.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

from graph import queries
from graph.db import Neo4jStore
from retrieval.hybrid_search import SWEEPING_PR_FILES, breadth_weight

# Exception names too generic to be meaningful relatedness evidence (same convention as retrieval/rerank.py).
GENERIC_EXCEPTIONS = {"Exception", "Error", "Warning"}
# A file touched by at least this many PRs is labeled a "busy" file in evidence text (weight still decays
# continuously below this - see _file_weight - this constant only controls the human-readable label).
BUSY_FILE_DEGREE = 20
FUNC_RE = re.compile(r"\bdef (\w+)")
GENERIC_FUNCS = {"__init__", "main", "wrapper", "setUp", "tearDown", "run"}


@dataclass(frozen=True)
class RelatedFix:
    pr_id: str
    pr_number: int
    title: str
    url: str | None
    shared_exceptions_changed_lines: tuple[str, ...]  # exception raised/caught in CHANGED lines on both sides
    shared_exceptions_mentioned: tuple[str, ...]  # weaker: at least one side is a hunk-context mention only
    shared_files: tuple[str, ...]
    shared_files_degree: dict[str, int] = field(default_factory=dict)
    shared_functions: tuple[str, ...] = ()
    score: float = 0.0
    relative_score: int = 0  # 0-100 within this result set only; heuristic, not a probability of a fix
    reasons: tuple[str, ...] = ()
    evidence_paths: tuple[dict[str, Any], ...] = ()
    earliest_release: str | None = None


def _file_weight(degree: int) -> float:
    """1.0 for a file few PRs touch; decays for files so many PRs change that sharing one means little.
    Same decay shape as hybrid_search.breadth_weight, applied to file popularity instead of PR breadth."""
    return 1.0 if degree <= 5 else 5.0 / degree


def _aggregate_exceptions(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group RELATED_FIXES_BY_EXCEPTION rows per candidate PR; classify each exception as a changed-line
    match (both sides) or a weaker mention (either side only in hunk context, never assumed confirmed)."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = out.setdefault(
            r["pr_id"],
            {"number": r["number"], "title": r["title"], "url": r["url"], "changed_file_count": r.get("changed_file_count") or 1, "changed": set(), "mentioned": set()},
        )
        if r["source_changed_lines"] and r["candidate_changed_lines"]:
            d["changed"].add(r["exception"])
        else:
            d["mentioned"].add(r["exception"])
    for d in out.values():
        d["mentioned"] -= d["changed"]  # a changed-line match is strictly stronger; don't double-count it
    return out


def _aggregate_files(rows: list[dict[str, Any]], degree: dict[str, int]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = out.setdefault(
            r["pr_id"],
            {"number": r["number"], "title": r["title"], "url": r["url"], "changed_file_count": r.get("changed_file_count") or 1, "files": set()},
        )
        d["files"].update(r["shared_files"])
    for d in out.values():
        d["degree"] = {p: degree.get(p, 1) for p in d["files"]}
    return out


def _shared_functions(source_hunks: list[dict[str, Any]], cand_hunks: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Function names touched in a diff, intersected per (candidate PR, shared file path). ``section`` is
    unreliable in this snapshot (often empty or a class header), so functions are parsed from the diff body."""
    by_path: dict[str, set[str]] = {}
    for h in source_hunks:
        names = {n for n in FUNC_RE.findall(h["diff"] or "") if n not in GENERIC_FUNCS and len(n) > 2}
        if names:
            by_path.setdefault(h["path"], set()).update(names)
    out: dict[str, set[str]] = {}
    for h in cand_hunks:
        src_names = by_path.get(h["path"])
        if not src_names:
            continue
        names = {n for n in FUNC_RE.findall(h["diff"] or "") if n not in GENERIC_FUNCS and len(n) > 2}
        shared = names & src_names
        if shared:
            out.setdefault(h["pr_id"], set()).update(shared)
    return out


def _score(changed: set[str], mentioned: set[str], files: set[str], degree: dict[str, int], functions: set[str], candidate_file_count: int) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    reasons: list[str] = []
    n_changed = min(2, len(changed))
    if n_changed:
        score += 3 * n_changed
        reasons.append(f"changed-line match: {', '.join(sorted(changed))}")
    n_mentioned = min(2, len(mentioned))
    if n_mentioned:
        score += 1 * n_mentioned
        reasons.append(f"hunk mention only (not confirmed on changed lines): {', '.join(sorted(mentioned))}")
    n_func = min(2, len(functions))
    if n_func:
        score += 1 * n_func
        reasons.append(f"shared function: {', '.join(sorted(functions))}")
    if files:
        weight = min(2.0, sum(_file_weight(degree.get(p, 1)) for p in files))
        score += 0.25 * weight
        busy = sorted(p for p in files if degree.get(p, 1) >= BUSY_FILE_DEGREE)
        if busy:
            reasons.append(f"weak relationship: same changed file, but touched by many PRs ({', '.join(busy)})")
        quiet = sorted(p for p in files if degree.get(p, 1) < BUSY_FILE_DEGREE)
        if quiet:
            reasons.append(f"shared changed file: {', '.join(quiet)}")
    evidence_types = sum(bool(x) for x in ((changed or mentioned), files, functions))
    if evidence_types > 1:
        score += 1.0
        reasons.append("corroborated by more than one independent signal")
    if candidate_file_count > SWEEPING_PR_FILES:
        score *= breadth_weight(candidate_file_count)
        reasons.append(f"candidate PR touches {candidate_file_count} files (sweeping change: formatting/dependency/vendoring PRs share evidence with almost everything, so this is weaker than it looks)")
    return round(score, 3), tuple(reasons)


def _build(candidate_ids: set[str], exc_by_pr: dict, file_by_pr: dict, func_by_pr: dict, earliest: dict[str, str | None], limit: int) -> list[RelatedFix]:
    built: list[RelatedFix] = []
    for pid in candidate_ids:
        e, f = exc_by_pr.get(pid, {}), file_by_pr.get(pid, {})
        meta = e or f
        changed, mentioned = e.get("changed", set()), e.get("mentioned", set())
        files, degree, functions = f.get("files", set()), f.get("degree", {}), func_by_pr.get(pid, set())
        file_count = e.get("changed_file_count") or f.get("changed_file_count") or 1
        score, reasons = _score(changed, mentioned, files, degree, functions, file_count)
        if score <= 0:
            continue
        paths: list[dict[str, Any]] = (
            [{"type": "exception", "name": n, "changed_lines": True} for n in sorted(changed)]
            + [{"type": "exception", "name": n, "changed_lines": False} for n in sorted(mentioned)]
            + [{"type": "file", "path": p, "degree": degree.get(p, 1)} for p in sorted(files)]
            + [{"type": "function", "name": n} for n in sorted(functions)]
        )
        built.append(
            RelatedFix(
                pr_id=pid,
                pr_number=meta["number"],
                title=meta.get("title") or "",
                url=meta.get("url"),
                shared_exceptions_changed_lines=tuple(sorted(changed)),
                shared_exceptions_mentioned=tuple(sorted(mentioned)),
                shared_files=tuple(sorted(files)),
                shared_files_degree=dict(degree),
                shared_functions=tuple(sorted(functions)),
                score=score,
                reasons=reasons,
                evidence_paths=tuple(paths),
                earliest_release=earliest.get(pid),
            )
        )
    built.sort(key=lambda r: (-r.score, r.pr_number))
    top = built[:limit]
    max_score = top[0].score if top else 0.0
    if max_score > 0:
        top = [replace(r, relative_score=round(100 * r.score / max_score)) for r in top]
    return top


def _run(store: Neo4jStore, trace: list[dict[str, Any]] | None, name: str, cypher: str, **params: Any) -> list[dict[str, Any]]:
    t0 = time.perf_counter()
    rows = store.query(cypher, **params)
    if trace is not None:
        trace.append({"name": name, "cypher": cypher.strip(), "params": params, "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return rows


def find_related_fixes(
    store: Neo4jStore, pr_id: str, *, limit: int = 8, hard_cap: int = 300, trace: list[dict[str, Any]] | None = None
) -> list[RelatedFix]:
    """Read-only. Raises whatever ``store.query`` raises on a Neo4j failure; the caller decides fallback UX.
    ``trace`` (optional), when given a list, is appended with one entry per executed query - same shape as
    HybridSearcher's trace - so the UI's Cypher tab can show related-fix queries beside the main investigation."""
    exc_rows = _run(store, trace, "related_by_exception", queries.RELATED_FIXES_BY_EXCEPTION, pr_id=pr_id, generic_exceptions=sorted(GENERIC_EXCEPTIONS), hard_cap=hard_cap)
    degree_rows = _run(store, trace, "related_source_file_degree", queries.RELATED_FIXES_SOURCE_FILE_DEGREE, pr_id=pr_id)
    degree = {r["path"]: r["degree"] for r in degree_rows}
    file_rows = _run(store, trace, "related_by_file", queries.RELATED_FIXES_BY_FILE, pr_id=pr_id, hard_cap=hard_cap)

    exc_by_pr = _aggregate_exceptions(exc_rows)
    file_by_pr = _aggregate_files(file_rows, degree)

    candidate_ids = set(exc_by_pr) | set(file_by_pr)
    if not candidate_ids:
        return []

    # Shared-function signal only for files already known to be shared by a candidate - bounded, no
    # whole-graph scan, and no N+1 (two batched queries regardless of candidate count).
    shared_paths = sorted({p for d in file_by_pr.values() for p in d["files"]})
    func_by_pr: dict[str, set[str]] = {}
    if shared_paths:
        src_hunks = _run(store, trace, "related_source_hunks", queries.RELATED_FIXES_HUNK_DIFFS, pr_ids=[pr_id], paths=shared_paths)
        cand_hunks = _run(store, trace, "related_candidate_hunks", queries.RELATED_FIXES_HUNK_DIFFS, pr_ids=sorted(candidate_ids), paths=shared_paths)
        func_by_pr = _shared_functions(src_hunks, cand_hunks)

    release_rows = _run(store, trace, "related_earliest_release", queries.RELATED_FIXES_EARLIEST_RELEASE, pr_ids=sorted(candidate_ids))
    earliest = {r["pr_id"]: r["earliest_release"] for r in release_rows}

    return _build(candidate_ids, exc_by_pr, file_by_pr, func_by_pr, earliest, limit)
