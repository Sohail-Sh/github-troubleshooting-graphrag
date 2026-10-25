"""Patch hunks with before/after highlighting."""

from __future__ import annotations

import streamlit as st

from explanation.evidence_bundle import EvidenceBundle
from retrieval.fix_path import is_test_path


def render_diffs(bundle: EvidenceBundle) -> None:
    if not bundle.fixes:
        st.info("No code change is linked to this candidate.")
        return
    for fx in bundle.fixes[:3]:
        st.markdown(f"#### PR #{fx.pr_number}: {fx.pr_title}  \n[View on GitHub]({fx.pr_url}/files) · {fx.hunk_total} hunks in total")
        if not fx.hunks:
            st.caption("No patch hunks were collected for this PR.")
        for h, eid in zip(fx.hunks, fx.evidence_ids.get("patch", [])):
            tag = "test" if is_test_path(h["path"]) else "source"
            exc = f" · touches {', '.join(h['exceptions'])}" if h.get("exceptions") else ""
            st.markdown(f"**`{h['path']}`** — {h.get('section') or 'module level'} · `{tag}` · +{h.get('additions')}/-{h.get('deletions')}{exc} · [{eid}]")
            st.code(h["diff"], language="diff")
