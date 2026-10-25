"""Load a normalized snapshot into Neo4j (idempotent MERGE) and attach embeddings.

    python -m graph.ingest_neo4j psf-requests-v2.32.3 [--reset] [--embedder hashing|fastembed]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from config import get_settings
from ingestion.snapshot import Snapshot, read_jsonl, utc_now, write_jsonl
from ingestion.validate_links import SCHEMA
from retrieval.embeddings import Embedder, get_embedder

from .db import Neo4jStore

log = logging.getLogger(__name__)
BATCH = 1000
ALLOWED_LABELS = {lbl for start, end in SCHEMA.values() for lbl in start | end} | {"Searchable"}


def _batches(rows: list[Any], size: int = BATCH) -> Iterable[list[Any]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _check_label(label: str) -> str:
    if label not in ALLOWED_LABELS or not label.isidentifier():
        raise ValueError(f"unexpected label {label!r}")
    return label


def reset_database(store: Neo4jStore) -> None:
    """Delete all data and the derived search indexes (their fields/dimensions may have changed)."""
    while True:
        res = store.query("MATCH (n) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c")
        if not res or res[0]["c"] == 0:
            break
    for name in ("searchable_text", "searchable_embedding"):
        store.write(f"DROP INDEX {name} IF EXISTS")


def load_nodes(store: Neo4jStore, nodes: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        groups[tuple(n["labels"])].append({"id": n["id"], "props": n["props"]})
    for labels, rows in groups.items():
        primary, *extra = (_check_label(lbl) for lbl in labels)
        set_extra = "".join(f" SET n:{lbl}" for lbl in extra)
        cypher = f"UNWIND $rows AS row MERGE (n:{primary} {{id: row.id}}) SET n += row.props{set_extra}"
        for batch in _batches(rows):
            store.write(cypher, rows=batch)
        log.info("nodes %s: %d", ":".join(labels), len(rows))


def load_relationships(store: Neo4jStore, rels: list[dict[str, Any]], primary_label: dict[str, str]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rels:
        groups[(r["type"], primary_label[r["start"]], primary_label[r["end"]])].append(
            {"start": r["start"], "end": r["end"], "props": r["props"]}
        )
    for (rtype, sl, el), rows in groups.items():
        if rtype not in SCHEMA:
            raise ValueError(f"unexpected relationship type {rtype!r}")
        cypher = (
            f"UNWIND $rows AS row MATCH (a:{_check_label(sl)} {{id: row.start}}) MATCH (b:{_check_label(el)} {{id: row.end}}) "
            f"MERGE (a)-[r:{rtype}]->(b) SET r += row.props"
        )
        for batch in _batches(rows):
            store.write(cypher, rows=batch)
        log.info("rels %s (%s->%s): %d", rtype, sl, el, len(rows))


def compute_embeddings(snap: Snapshot, nodes: list[dict[str, Any]], embedder: Embedder) -> dict[str, list[float]]:
    """Embed every Searchable node, reusing the snapshot's embedding cache when the text is unchanged."""
    slug = embedder.name.replace("/", "__")
    cache_path = snap.path("embeddings", f"{slug}.jsonl")
    cache = {row["id"]: row for row in read_jsonl(cache_path)}
    out: dict[str, list[float]] = {}
    todo: list[tuple[str, str, str]] = []
    for n in nodes:
        if "Searchable" not in n["labels"]:
            continue
        text = n["props"].get("search_text", "")
        digest = hashlib.sha1(text.encode()).hexdigest()
        cached = cache.get(n["id"])
        if cached and cached["text_sha1"] == digest:
            out[n["id"]] = cached["vector"]
        else:
            todo.append((n["id"], text, digest))
    log.info("embeddings: %d cached, %d to compute with %s", len(out), len(todo), embedder.name)
    for batch in _batches(todo, 256):
        vectors = embedder.embed_documents([t for _, t, _ in batch])
        for (nid, _, digest), vec in zip(batch, vectors):
            out[nid] = vec
            cache[nid] = {"id": nid, "text_sha1": digest, "vector": [round(x, 6) for x in vec]}
    if todo:
        write_jsonl(cache_path, cache.values())
    return out


def load_embeddings(store: Neo4jStore, vectors: dict[str, list[float]]) -> None:
    rows = [{"id": k, "v": v} for k, v in vectors.items()]
    for batch in _batches(rows, 500):
        store.write(
            "UNWIND $rows AS row MATCH (n:Searchable {id: row.id}) "
            "CALL db.create.setNodeVectorProperty(n, 'embedding', row.v)",
            rows=batch,
        )


def ingest(snapshot_path: Path, reset: bool = False, embedder_kind: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    snap = Snapshot.open(snapshot_path)
    bad = snap.verify_checksums()
    if bad:
        raise RuntimeError(f"snapshot checksum mismatch: {bad[:5]}")
    nodes = list(read_jsonl(snap.path("graph", "nodes.jsonl")))
    rels = list(read_jsonl(snap.path("graph", "relationships.jsonl")))
    if not nodes:
        raise RuntimeError("snapshot has no graph; run `python -m ingestion.normalize` first")
    embedder = get_embedder(embedder_kind or settings.embedder, settings.embedding_model)

    store = Neo4jStore(settings)
    t0 = time.time()
    if reset:
        reset_database(store)
    store.apply_schema()
    load_nodes(store, nodes)
    load_relationships(store, rels, {n["id"]: n["labels"][0] for n in nodes})
    vectors = compute_embeddings(snap, nodes, embedder)
    load_embeddings(store, vectors)
    meta = {
        "snapshot_id": snap.id,
        "repository": snap.manifest["repository"]["id"],
        "repository_url": snap.manifest["repository"]["url"],
        "collected_at": snap.manifest.get("collected_at"),
        "source_ref": json.dumps(snap.manifest.get("source_ref")),
        "sources": snap.manifest.get("sources", []),
        "api_status": snap.manifest.get("api_status", "not collected"),
        "release_history_complete": bool(snap.manifest.get("release_history_complete")),
        "embedding_model": embedder.name,
        "embedding_dimensions": embedder.dimensions,
        "loaded_at": utc_now(),
        "warnings": [f"[{e['stage']}] {e['message']}" for e in snap.manifest.get("errors", [])],
    }
    store.write("MERGE (m:SnapshotMeta {id: $id}) SET m = $meta, m.id = $id", id=snap.manifest["repository"]["id"], meta=meta)
    store.query("CALL db.awaitIndexes(300)")
    log.info("loaded %d nodes / %d relationships in %.1fs", len(nodes), len(rels), time.time() - t0)
    return meta


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("snapshot", help="snapshot id or path")
    p.add_argument("--reset", action="store_true", help="delete everything in the database first")
    p.add_argument("--embedder", choices=["fastembed", "hashing"])
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    path = Path(a.snapshot)
    if not path.exists():
        path = get_settings().snapshots_dir / a.snapshot
    meta = ingest(path, a.reset, a.embedder)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
