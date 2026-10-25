"""View 1 - repository setup and graph health."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import get_settings
from graph import queries
from ingestion.snapshot import Snapshot


def list_snapshots() -> list[Path]:
    root = get_settings().snapshots_dir
    return sorted(p for p in root.glob("*") if (p / "manifest.json").exists()) if root.exists() else []


def render_repository(store, connected: bool) -> None:
    st.markdown("## Repository Intelligence")
    st.caption("GitHub → Neo4j ingestion")

    if connected:
        health = store.query(queries.GRAPH_HEALTH)
        if health:
            h = health[0]
            meta = h["meta"]
            st.success(f"Indexed **{meta.get('repository_url')}** · snapshot `{meta.get('snapshot_id')}` · collected {meta.get('collected_at')} · loaded {meta.get('loaded_at')}")
            cols = st.columns(6)
            cols[0].metric("Issues", h["issues"], help=f"{h['hydrated_issues']} with full text (from the GitHub API); the rest are known only from references")
            cols[1].metric("Merged PRs", h["merged_prs"])
            cols[2].metric("Verified links", h["verified_links"])
            cols[3].metric("Referenced links", h["referenced_links"])
            cols[4].metric("Inferred links", h["inferred_links"])
            cols[5].metric("Releases", h["releases"])
            st.caption(
                f"Sources: {', '.join(meta.get('sources') or [])} · API status: {meta.get('api_status')} · "
                f"release history complete: {meta.get('release_history_complete')} · embeddings: `{meta.get('embedding_model')}` "
                f"({h['embedded']}/{h['searchable']} searchable nodes embedded) · {h['patch_hunks']} patch hunks"
            )
            if str(meta.get("embedding_model", "")).startswith("hashing"):
                st.warning("Embeddings come from the offline hashing fallback (lexical, not semantic). Load with `--embedder fastembed` for BAAI/bge-small-en-v1.5.")
            for w in meta.get("warnings") or []:
                st.warning(f"Collection warning: {w}")
            idx = store.query(queries.INDEX_STATUS)
            st.dataframe(idx, hide_index=True)
        else:
            st.info("Neo4j is connected but empty. Load a snapshot below.")
    else:
        st.error("Neo4j is not reachable. Start it with `docker compose up -d neo4j`.")

    st.divider()
    st.markdown("### Build a snapshot")
    st.caption("For the presentation, ingest beforehand and load a frozen snapshot; nothing here needs conference Wi-Fi.")
    with st.form("ingest"):
        url = st.text_input("Public GitHub repository", "https://github.com/psf/requests")
        c1, c2, c3, c4 = st.columns(4)
        since = c1.text_input("Since", "2017-01-01")
        until = c2.text_input("Until", "2024-06-30")
        ref = c3.text_input("Freeze at git ref", "v2.32.3")
        sources = c4.multiselect("Sources", ["git", "api"], ["git", "api"])
        snapshot_id = st.text_input("Snapshot id", "psf-requests-v2.32.3")
        embedder = st.radio("Embeddings", ["fastembed", "hashing"], horizontal=True)
        go = st.form_submit_button("Collect → normalize → load", type="primary", disabled=not connected)
    if go:
        from graph.ingest_neo4j import ingest
        from ingestion.fetch_repository import fetch
        from ingestion.normalize import normalize

        with st.status("Ingesting…", expanded=True) as status:
            try:
                st.write("Collecting evidence (git clone / GitHub API)…")
                snap = fetch(url, sources, snapshot_id, None, ref, since or None, until or None)
                st.write({k: v for k, v in snap.manifest["collections"].items()})
                st.write("Normalizing and validating relationships…")
                summary = normalize(snap.root)
                st.write(summary["issue_links"], summary["validation"]["problems"] or "validation passed")
                st.write("Loading Neo4j and computing embeddings…")
                ingest(snap.root, reset=True, embedder_kind=embedder)
                status.update(label="Snapshot loaded", state="complete")
                st.cache_resource.clear()
            except Exception as exc:  # noqa: BLE001
                status.update(label=f"Ingestion failed: {exc}", state="error")

    snaps = list_snapshots()
    if snaps:
        st.markdown("### Snapshots on disk")
        for p in snaps:
            snap = Snapshot.open(p)
            with st.expander(f"{snap.id} · {snap.manifest.get('status')} · {snap.manifest.get('collected_at')}"):
                st.json({k: v for k, v in snap.manifest.items() if k != "checksums"}, expanded=False)
                bad = snap.verify_checksums()
                st.caption("Checksums OK" if not bad else f"Checksum mismatch: {bad[:3]}")
                if connected and st.button("Load this snapshot", key=f"load_{snap.id}"):
                    from graph.ingest_neo4j import ingest

                    with st.spinner("Loading…"):
                        ingest(p, reset=True)
                    st.cache_resource.clear()
                    st.rerun()
