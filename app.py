"""Streamlit app: From Stack Trace to Fix Path - GitHub troubleshooting graphs with Neo4j.

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from config import get_settings
from graph.db import Neo4jStore
from retrieval.pipeline import Investigator
from ui.evaluation_view import render_evaluation
from ui.investigation_view import render_investigation
from ui.repository_view import render_repository

st.set_page_config(page_title="Stack Trace → Fix Path", page_icon="🧭", layout="wide")


@st.cache_resource(show_spinner=False)
def get_store() -> Neo4jStore:
    return Neo4jStore(get_settings())


@st.cache_resource(show_spinner="Loading embedding model…")
def get_investigator(signature: str) -> Investigator:
    return Investigator(get_store())


def backend_signature(store: Neo4jStore) -> tuple[bool, str, str]:
    ok, detail = store.ping()
    if not ok:
        return False, "offline", detail
    rows = store.query("MATCH (m:SnapshotMeta) RETURN m.snapshot_id AS s, m.loaded_at AS t LIMIT 1")
    sig = f"{store.settings.neo4j_uri}|{rows[0]['s']}|{rows[0]['t']}" if rows else f"{store.settings.neo4j_uri}|empty"
    return True, sig, detail


def main() -> None:
    settings = get_settings()
    store = get_store()
    connected, signature, detail = backend_signature(store)

    with st.sidebar:
        st.markdown("### 🧭 Stack Trace → Fix Path")
        st.caption("GitHub troubleshooting graphs with Neo4j")
        if connected:
            st.success("Neo4j connected", icon="✅")
        else:
            st.error("Neo4j offline", icon="⚠️")
        st.caption(detail)
        view = st.radio("View", ["Troubleshoot", "Repository", "Evaluation"], key="view")
        use_llm = st.toggle(
            "LLM explanation (Claude)",
            value=settings.llm_provider == "anthropic",
            help="Off = deterministic explanation from Neo4j results only. On = Claude writes it from the same evidence; "
            "the output is validated and falls back to deterministic on any problem.",
        )

    investigator = get_investigator(signature) if connected and not signature.endswith("|empty") else None
    if view == "Repository":
        render_repository(store, connected)
    elif investigator is None:
        st.warning("Load a snapshot first (Repository view).")
    elif view == "Troubleshoot":
        render_investigation(investigator, signature, use_llm)
    else:
        render_evaluation(investigator, connected)


main()
