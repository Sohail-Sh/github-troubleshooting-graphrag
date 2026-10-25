"""Transparent candidate scoring.

The retrieval score says how well a historical report *matches the query*. Evidence strength says how well
the graph *supports a fix* for it. They are reported separately and neither is a probability that the fix
will work.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ingestion.git_history import version_key

from .hybrid_search import SWEEPING_PR_FILES, AnchorCandidate
from .query_parser import ParsedQuery

EVIDENCE_STRENGTH = {"verified": 1.0, "self": 0.8, "referenced": 0.7, "inferred": 0.35}
EVIDENCE_LABEL = {"verified": "Verified link", "self": "Merged PR", "referenced": "Referenced", "inferred": "Inferred only"}
WEIGHTS = {"text": 0.40, "coverage": 0.20, "exception": 0.15, "frames": 0.10, "evidence": 0.15}


@dataclass
class ScoredCandidate:
    anchor_id: str
    label: str
    number: int | None
    title: str | None
    url: str | None
    hydrated: bool
    rank: int = 0
    retrieval_score: float = 0.0
    final_score: float = 0.0
    term_coverage: float = 0.0
    match_support: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    missing_terms: list[str] = field(default_factory=list)
    vector_similarity: float = 0.0
    exception_match: bool | None = None
    frame_match: float = 0.0
    evidence_status: str = "none"
    evidence_strength: float = 0.0
    earliest_fix_release: str | None = None
    in_release_notes: bool = False
    already_fixed_in_user_version: bool | None = None
    strong_match: bool = False
    reasons: list[str] = field(default_factory=list)
    hits: list[dict[str, Any]] = field(default_factory=list)
    fixes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _term_coverage(terms: list[str], texts: list[str]) -> tuple[float, list[str], list[str]]:
    if not terms:
        return 0.0, [], []
    blob = " ".join(texts).lower()
    matched = [t for t in terms if re.search(rf"(?<![a-z0-9]){re.escape(t)}", blob)]
    return len(matched) / len(terms), matched, [t for t in terms if t not in matched]


def _user_version(q: ParsedQuery) -> str | None:
    return q.package_versions.get("library") or q.package_versions.get("requests")


def score_candidates(
    q: ParsedQuery,
    anchors: list[AnchorCandidate],
    profiles: dict[str, dict[str, Any]],
    mode: str,
    min_term_coverage: float = 0.5,
    min_vector_similarity: float = 0.80,
) -> list[ScoredCandidate]:
    terms = q.key_terms
    max_text = max((a.text_score for a in anchors), default=1.0) or 1.0
    q_exc = {t for t in q.exception_types if t not in {"Exception", "Error", "Warning"}}
    q_funcs = set(q.function_names)
    q_files = {p for p in q.file_paths}
    user_ver = _user_version(q)
    out: list[ScoredCandidate] = []

    for a in anchors:
        p = profiles.get(a.anchor_id, {})
        c = ScoredCandidate(
            anchor_id=a.anchor_id,
            label=a.label,
            number=p.get("number"),
            title=p.get("title"),
            url=p.get("url"),
            hydrated=bool(p.get("hydrated", True)),
            hits=a.hits[:8],
            fixes=p.get("fixes", []),
            vector_similarity=round(a.vector_similarity, 4),
        )
        c.retrieval_score = round(a.text_score, 5)
        c.term_coverage, c.matched_terms, c.missing_terms = _term_coverage(terms, a.texts + [p.get("title") or ""])

        # ---- graph features
        fixes = p.get("fixes", [])
        graph_exc = set(p.get("report_exceptions", []))
        for fx in fixes:
            graph_exc |= set(fx.get("fix_exceptions", []))
        text_exc = {t for t in q_exc if any(t.lower() in tx.lower() for tx in a.texts)}
        if q_exc:
            c.exception_match = bool(q_exc & graph_exc) if mode == "graph" else bool(text_exc)
            if mode == "graph" and not c.exception_match and text_exc:
                c.exception_match = True
        fix_files = {f for fx in fixes for f in fx.get("changed_files", [])} | set(p.get("report_files", []))
        fix_funcs = {re.sub(r".*def (\w+).*", r"\1", s) for fx in fixes for s in fx.get("hunk_sections", []) if s}
        fix_funcs |= set(p.get("report_functions", []))
        if q_funcs or q_files:
            file_hit = any(f.endswith(qf) or qf.endswith(f) for f in fix_files for qf in q_files)
            func_hits = q_funcs & {fn.split(".")[-1] for fn in fix_funcs}
            c.frame_match = round(min(1.0, 0.5 * file_hit + 0.5 * min(1.0, len(func_hits) / 2)), 3)

        best = max(fixes, key=lambda fx: EVIDENCE_STRENGTH.get(fx.get("status"), 0), default=None)
        sweeping = False
        if best:
            c.evidence_status = best.get("status") or "none"
            c.evidence_strength = EVIDENCE_STRENGTH.get(c.evidence_status, 0.0)
            sweeping = (best.get("file_count") or 0) > SWEEPING_PR_FILES
            if sweeping and mode == "graph":
                c.evidence_strength *= 0.4
            c.in_release_notes = any(fx.get("in_release_notes") for fx in fixes)
            if c.in_release_notes:
                c.evidence_strength = min(1.0, c.evidence_strength + 0.1)
            releases = sorted({r for fx in fixes for r in fx.get("earliest_release", [])}, key=version_key)
            c.earliest_fix_release = releases[0] if releases else None
        if user_ver and c.earliest_fix_release:
            c.already_fixed_in_user_version = version_key(user_ver.lstrip("v")) >= version_key(c.earliest_fix_release)

        # ---- scores
        text_norm = a.text_score / max_text
        if mode == "graph":
            score = (
                WEIGHTS["text"] * text_norm
                + WEIGHTS["coverage"] * c.term_coverage
                + WEIGHTS["exception"] * (1.0 if c.exception_match else 0.0 if q_exc else 0.5)
                + WEIGHTS["frames"] * c.frame_match
                + WEIGHTS["evidence"] * c.evidence_strength
            )
            if c.already_fixed_in_user_version:
                score -= 0.15
            if q_exc and c.exception_match is False:
                score -= 0.10
        else:
            score = text_norm
        c.final_score = round(score, 4)

        # ---- match gate (same rule in every mode so rejection rates are comparable)
        # support = share of key query terms found in the candidate's evidence, plus credit for an exception-type
        # match and stack-frame overlap. A contradicted exception type always fails the gate.
        c.match_support = round(min(1.0, c.term_coverage + 0.15 * bool(c.exception_match) + 0.10 * c.frame_match), 3)
        exception_ok = c.exception_match is not False
        lexical_ok = c.match_support >= min_term_coverage
        semantic_ok = c.vector_similarity >= min_vector_similarity and c.term_coverage >= min_term_coverage * 0.6
        c.strong_match = exception_ok and (lexical_ok or semantic_ok)

        # ---- human-readable reasons
        if c.matched_terms:
            c.reasons.append(f"matches {len(c.matched_terms)}/{len(terms)} key terms: {', '.join(c.matched_terms[:6])}")
        if c.exception_match:
            c.reasons.append("exception type appears in the report or in the fixing patch")
        elif c.exception_match is False:
            c.reasons.append(f"exception type {', '.join(sorted(q_exc))} not found in this candidate's evidence")
        if c.frame_match:
            c.reasons.append("stack-frame file/function overlaps the changed code")
        if c.evidence_status != "none":
            c.reasons.append(f"fix evidence: {EVIDENCE_LABEL.get(c.evidence_status, c.evidence_status)}")
        if c.in_release_notes:
            c.reasons.append("maintainers documented the fix in release notes")
        if sweeping:
            c.reasons.append(f"fixing PR touches {best.get('file_count')} files (sweeping change, weak fix evidence)")
        if c.already_fixed_in_user_version:
            c.reasons.append(f"your version {user_ver} already contains {c.earliest_fix_release}")
        out.append(c)

    out.sort(key=lambda c: (-c.final_score, -c.retrieval_score))
    for i, c in enumerate(out):
        c.rank = i + 1
    return out
