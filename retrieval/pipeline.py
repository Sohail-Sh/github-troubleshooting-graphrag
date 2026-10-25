"""End-to-end investigation: parse -> retrieve -> re-rank -> gate -> expand fix path -> evidence bundle."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from config import Settings, get_settings
from explanation.evidence_bundle import EvidenceBundle, build_bundle
from graph import queries
from graph.db import Neo4jStore

from .embeddings import get_embedder
from .fix_path import expand_fix_path
from .hybrid_search import HybridSearcher
from .query_parser import ParsedQuery, parse_query
from .rerank import ScoredCandidate, score_candidates

FIX_STATUSES = {"verified", "referenced", "self"}


@dataclass
class InvestigationResult:
    query: ParsedQuery
    mode: str
    status: str
    candidates: list[ScoredCandidate]
    bundle: EvidenceBundle
    trace: list[dict[str, Any]]
    timings_ms: dict[str, float]
    embedding_model: str
    snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.to_dict(),
            "mode": self.mode,
            "status": self.status,
            "candidates": [c.to_dict() for c in self.candidates],
            "bundle": self.bundle.to_dict(),
            "trace": self.trace,
            "timings_ms": self.timings_ms,
            "embedding_model": self.embedding_model,
            "snapshot": self.snapshot,
        }


class Investigator:
    def __init__(self, store: Neo4jStore | None = None, settings: Settings | None = None, embedder_kind: str | None = None):
        self.settings = settings or get_settings()
        self.store = store or Neo4jStore(self.settings)
        self.meta = self._snapshot_meta()
        # Always embed queries with the model the graph was built with.
        kind = embedder_kind or ("hashing" if self.meta.get("embedding_model", "").startswith("hashing") else self.settings.embedder)
        self.embedder = get_embedder(kind, self.meta.get("embedding_model") or self.settings.embedding_model)

    def _snapshot_meta(self) -> dict[str, Any]:
        rows = self.store.query("MATCH (m:SnapshotMeta) RETURN m {.*} AS m LIMIT 1")
        return rows[0]["m"] if rows else {}

    def investigate(
        self,
        text: str,
        mode: str = "graph",
        library_version: str | None = None,
        python_version: str | None = None,
        top_k: int = 10,
        selected_anchor: str | None = None,
        hide_already_fixed: bool = False,
    ) -> InvestigationResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        q = parse_query(text, library_version, python_version)
        searcher = HybridSearcher(self.store, self.embedder, self.settings.rrf_k)

        anchors, hits, _ = searcher.search(q, mode)
        timings["retrieval"] = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        profiles: dict[str, dict[str, Any]] = {}
        if anchors:
            rows = searcher._run("candidate_profile", queries.CANDIDATE_PROFILE, anchor_ids=[a.anchor_id for a in anchors[:50]])
            profiles = {r["anchor_id"]: r for r in rows}
        candidates = score_candidates(
            q, anchors[:50], profiles, mode, self.settings.min_term_coverage, self.settings.min_vector_similarity
        )
        if hide_already_fixed:
            candidates = [c for c in candidates if not c.already_fixed_in_user_version]
            for i, c in enumerate(candidates):
                c.rank = i + 1
        candidates = candidates[:top_k]
        timings["rerank"] = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        chosen = next((c for c in candidates if c.anchor_id == selected_anchor), None) if selected_anchor else None
        if chosen is None:
            chosen = candidates[0] if candidates and candidates[0].strong_match else None
        paths = expand_fix_path(searcher, chosen.anchor_id, chosen.label, q) if chosen else []
        context_ids = [h.id for h in hits if h.kind in ("doc", "code")][:5]
        context = searcher._run("supporting_context", queries.SUPPORTING_CONTEXT, ids=context_ids) if chosen and context_ids else []
        timings["graph_expansion"] = (time.perf_counter() - t2) * 1000

        if chosen is None:
            status = "no_reliable_match"
        else:
            statuses = {l.get("verification_status") for p in paths for l in p["links"][:1]}
            status = "fix_found" if statuses & FIX_STATUSES else "match_without_fix"
            if not chosen.strong_match:
                status = "no_reliable_match" if status != "fix_found" else "weak_match_with_fix"

        query_dict = {**q.to_dict(), "key_terms": q.key_terms}
        bundle = build_bundle(
            status,
            self.meta.get("repository_url", ""),
            query_dict,
            chosen.to_dict() if chosen else None,
            paths,
            context,
            bool(self.meta.get("release_history_complete")),
        )
        timings["total"] = (time.perf_counter() - t0) * 1000
        return InvestigationResult(
            query=q,
            mode=mode,
            status=status,
            candidates=candidates,
            bundle=bundle,
            trace=searcher.trace,
            timings_ms={k: round(v, 1) for k, v in timings.items()},
            embedding_model=self.embedder.name,
            snapshot=self.meta,
        )
