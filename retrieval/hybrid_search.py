"""Keyword, vector and hybrid (Reciprocal Rank Fusion) retrieval over Neo4j indexes, resolved to anchors."""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from graph import queries
from graph.db import Neo4jStore

from .embeddings import Embedder
from .query_parser import ParsedQuery, embedding_text, fulltext_query

MODES = ("keyword", "vector", "hybrid", "graph")
MODE_LABELS = {
    "keyword": "Keyword (full-text)",
    "vector": "Vector (embeddings)",
    "hybrid": "Hybrid (RRF of keyword + vector)",
    "graph": "Hybrid + graph re-ranking",
}


@dataclass
class Hit:
    id: str
    kind: str
    title: str
    score: float
    rank: int
    source: str  # keyword | vector


@dataclass
class AnchorCandidate:
    anchor_id: str
    label: str
    text_score: float = 0.0  # fused retrieval score (RRF), comparable within one query
    best_rank: int = 10_000
    vector_similarity: float = 0.0  # best cosine (Neo4j-normalized to [0, 1]) of the anchor's hits
    hits: list[dict[str, Any]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)


SWEEPING_PR_FILES = 15


def breadth_weight(changed_files: int) -> float:
    """1.0 for focused PRs, decaying for PRs that touch many files."""
    if changed_files <= 8:
        return 1.0
    return 1.0 / (1.0 + math.log2(changed_files / 8))


def rrf(rankings: list[list[Hit]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for h in ranking:
            scores[h.id] += 1.0 / (k + h.rank)
    return dict(scores)


class HybridSearcher:
    def __init__(self, store: Neo4jStore, embedder: Embedder, rrf_k: int = 60, k: int = 40):
        self.store = store
        self.embedder = embedder
        self.rrf_k = rrf_k
        self.k = k
        self.trace: list[dict[str, Any]] = []  # executed Cypher + params, shown in the UI

    def _run(self, name: str, cypher: str, **params: Any) -> list[dict[str, Any]]:
        t0 = time.perf_counter()
        rows = self.store.query(cypher, **params)
        shown = {k: (f"<{len(v)}-dim vector>" if k == "embedding" else v) for k, v in params.items()}
        self.trace.append({"name": name, "cypher": cypher.strip(), "params": shown, "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return rows

    def keyword(self, q: ParsedQuery) -> list[Hit]:
        rows = self._run("keyword_search", queries.KEYWORD_SEARCH, query=fulltext_query(q), k=self.k)
        return [Hit(r["id"], r["kind"], r["title"], r["score"], i + 1, "keyword") for i, r in enumerate(rows)]

    def vector(self, embedding: list[float]) -> list[Hit]:
        rows = self._run("vector_search", queries.VECTOR_SEARCH, embedding=embedding, k=self.k)
        return [Hit(r["id"], r["kind"], r["title"], r["score"], i + 1, "vector") for i, r in enumerate(rows)]

    def search(self, q: ParsedQuery, mode: str) -> tuple[list[AnchorCandidate], list[Hit], list[float]]:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        embedding = self.embedder.embed_query(embedding_text(q))
        rankings: list[list[Hit]] = []
        if mode in ("keyword", "hybrid", "graph"):
            rankings.append(self.keyword(q))
        if mode in ("vector", "hybrid", "graph"):
            rankings.append(self.vector(embedding))
        fused = rrf(rankings, self.rrf_k)
        all_hits = {h.id: h for ranking in rankings for h in ranking}
        hits = sorted(all_hits.values(), key=lambda h: -fused[h.id])
        for i, h in enumerate(hits):
            h.rank = i + 1
        return self.resolve(hits, fused, embedding, breadth_penalty=(mode == "graph")), hits, embedding

    def resolve(
        self, hits: list[Hit], fused: dict[str, float], embedding: list[float], breadth_penalty: bool = False
    ) -> list[AnchorCandidate]:
        """Map hits to anchors. With ``breadth_penalty`` (graph mode), hits inside sweeping PRs - release prep,
        vendoring, reformatting; known from the PR's CHANGES edges - count for less."""
        if not hits:
            return []
        rows = self._run("resolve_anchors", queries.RESOLVE_ANCHORS, ids=[h.id for h in hits], embedding=embedding)
        by_hit: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            if r["anchor_id"]:
                by_hit[r["hit_id"]].append(r)
        anchors: dict[str, AnchorCandidate] = {}
        per_anchor_scores: dict[str, list[float]] = defaultdict(list)
        for h in hits:
            for r in by_hit.get(h.id, []):
                a = anchors.setdefault(r["anchor_id"], AnchorCandidate(r["anchor_id"], r["anchor_label"]))
                if any(x["id"] == h.id for x in a.hits):
                    continue
                a.best_rank = min(a.best_rank, h.rank)
                a.vector_similarity = max(a.vector_similarity, r["vector_similarity"] or 0.0)
                a.hits.append({"id": h.id, "kind": h.kind, "title": h.title, "rank": h.rank, "via": r["via"], "score": round(fused[h.id], 5)})
                a.texts.append(r["text"] or "")
                weight = breadth_weight(r.get("breadth") or 1) if breadth_penalty else 1.0
                per_anchor_scores[a.anchor_id].append(fused[h.id] * weight)
        for aid, scores in per_anchor_scores.items():
            top = sorted(scores, reverse=True)[:3]
            # best hit counts fully; corroborating hits add diminishing support
            anchors[aid].text_score = top[0] + 0.5 * sum(top[1:])
        return sorted(anchors.values(), key=lambda a: (-a.text_score, a.best_rank))
