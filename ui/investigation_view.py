"""View 2 - the troubleshooting workbench."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from explanation.answer_generator import generate_answer
from explanation.evidence_bundle import STATUS_TEXT
from retrieval.hybrid_search import MODE_LABELS, MODES
from retrieval.rerank import EVIDENCE_LABEL

from .diff_view import render_diffs
from .graph_view import fix_path_dot

SAMPLES = {
    "UnicodeError (idna)": "UnicodeError: encoding with 'idna' codec failed (UnicodeError: label empty or too long)",
    "Full traceback": (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 3, in <module>\n'
        '    requests.get("http://.example.com")\n'
        '  File "/usr/lib/python3.9/site-packages/requests/models.py", line 401, in prepare_url\n'
        "    host = self._get_idna_encoded_host(host)\n"
        '  File "/usr/lib/python3.9/encodings/idna.py", line 165, in encode\n'
        '    raise UnicodeError("label empty or too long")\n'
        "UnicodeError: encoding with 'idna' codec failed (UnicodeError: label empty or too long)"
    ),
    "Plain description": "requests.get('http://.example.com') crashes with a UnicodeError from the idna codec instead of raising InvalidURL",
    "Unrelated error": "django.db.utils.OperationalError: no such table: auth_user",
}
STATUS_BANNER = {
    "fix_found": ("success", "Historical fix found"),
    "weak_match_with_fix": ("warning", "Weak match - the linked fix is only a lead"),
    "match_without_fix": ("warning", "Matching report found, but no supported fix"),
    "no_reliable_match": ("error", "No sufficiently supported historical fix found"),
}
BADGE = {"verified": ":green-badge[Verified link]", "referenced": ":orange-badge[Referenced]", "inferred": ":gray-badge[Inferred only]", "self": ":blue-badge[Merged PR]", "none": ":gray-badge[No fix link]"}


def _candidate_table(result) -> pd.DataFrame:
    rows = []
    for c in result.candidates:
        kind = "Issue" if c.label == "Issue" else "PR"
        title = c.title or ("(issue text not ingested)" if c.label == "Issue" else "")
        rows.append(
            {
                "Rank": c.rank,
                "Report": f"{kind} #{c.number}",
                "Title": title,
                "Retrieval score": c.final_score,
                "Match support": c.match_support,
                "Passes gate": "yes" if c.strong_match else "no",
                "Fix evidence": EVIDENCE_LABEL.get(c.evidence_status, "None"),
                "Earliest fix release": c.earliest_fix_release or "",
            }
        )
    return pd.DataFrame(rows)


def render_investigation(investigator, backend_signature: str, use_llm: bool) -> None:
    st.markdown("## From Stack Trace to Fix Path")
    st.caption("Search the symptom. Follow the evidence.")

    cols = st.columns(len(SAMPLES))
    for col, (name, text) in zip(cols, SAMPLES.items()):
        if col.button(name, use_container_width=True, key=f"sample_{name}"):
            st.session_state["query_text"] = text
    with st.form("investigate"):
        text = st.text_area("Error message, stack trace or symptom", key="query_text", height=160)
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        mode = c1.selectbox("Retrieval", MODES, index=MODES.index("graph"), format_func=MODE_LABELS.get, key="mode")
        lib_version = c2.text_input("Library version", key="lib_version", placeholder="e.g. 2.26.0")
        py_version = c3.text_input("Python version", key="py_version", placeholder="e.g. 3.9")
        hide_fixed = c4.checkbox("Hide fixes already in my version", key="hide_fixed")
        submitted = st.form_submit_button("Investigate", type="primary")

    if submitted:
        st.session_state.pop("selected_anchor", None)
        _run(investigator, backend_signature, text, mode, lib_version, py_version, hide_fixed, None)

    state = st.session_state.get("investigation")
    if not state:
        return
    if state["backend"] != backend_signature:
        st.warning("These results came from a different database or snapshot and are hidden. Run the search again.")
        return
    if state.get("error"):
        st.error(f"Search failed: {state['error']}")
        return
    _render_result(investigator, backend_signature, state, use_llm)


def _run(investigator, backend_signature, text, mode, lib_version, py_version, hide_fixed, selected) -> None:
    params = {"text": text, "mode": mode, "lib_version": lib_version, "py_version": py_version, "hide_fixed": hide_fixed}
    if not text.strip():
        st.session_state["investigation"] = {"backend": backend_signature, "error": "enter an error message first", "params": params}
        return
    try:
        result = investigator.investigate(
            text, mode, lib_version or None, py_version or None, selected_anchor=selected, hide_already_fixed=hide_fixed
        )
    except Exception as exc:  # noqa: BLE001 - never leave stale results on screen after a failure
        st.session_state["investigation"] = {"backend": backend_signature, "error": f"{type(exc).__name__}: {exc}", "params": params}
        return
    st.session_state["investigation"] = {"backend": backend_signature, "result": result, "params": params, "answer": None}


def _render_result(investigator, backend_signature, state: dict[str, Any], use_llm: bool) -> None:
    result = state["result"]
    bundle = result.bundle
    if state.get("answer") is None or state.get("answer_llm") != use_llm:
        with st.spinner("Writing the explanation from the evidence…"):
            state["answer"] = generate_answer(bundle, use_llm=use_llm)
        state["answer_llm"] = use_llm
    answer = state["answer"]

    kind, banner = STATUS_BANNER[result.status]
    getattr(st, kind)(f"**{banner}** · {MODE_LABELS[result.mode]} · {result.timings_ms['total']:.0f} ms · embeddings: `{result.embedding_model}`")

    left, right = st.columns([3, 2])
    with left:
        fx = bundle.primary_fix
        if bundle.issue or fx:
            issue = bundle.issue
            if issue:
                st.markdown(f"### [Issue #{issue['number']}]({issue['url']}): {issue.get('title') or '_issue text not ingested_'}")
            elif fx:
                st.markdown(f"### [PR #{fx.pr_number}]({fx.pr_url}): {fx.pr_title}")
            if fx:
                files = ", ".join(f"`{f['path']}`" for f in fx.files[:3])
                rel = f" · {fx.earliest_release} (earliest containing)" if fx.earliest_release else ""
                st.markdown(f"{BADGE.get(fx.verification_status, '')} [PR #{fx.pr_number}]({fx.pr_url}) · {files}{rel}")
        if result.status == "no_reliable_match":
            st.markdown("The retrieved issues do not provide enough evidence to identify an applicable fix for this error.  \n"
                        "**Suggested next step:** provide the complete traceback and installed dependency versions.")
    with right:
        m = bundle.match or {}
        if m:
            a, b, c = st.columns(3)
            a.metric("Retrieval score", f"{m['final_score']:.2f}", help="How well the report matches your query (relative within this search). Not a probability.")
            b.metric("Match support", f"{m['match_support']:.2f}", help="Share of key terms found + exception/frame agreement. The gate needs >= the configured threshold.")
            c.markdown("Evidence  \n" + BADGE.get(bundle.primary_fix.verification_status if bundle.primary_fix else "none", ""), help="Strength of the issue->fix link in the graph.")

    st.markdown("#### Ranked candidates")
    df = _candidate_table(result)
    if df.empty:
        st.caption("No candidates retrieved.")
    else:
        st.dataframe(df, hide_index=True, use_container_width=True)
        options = {f"{r['Report']} - {r['Title'][:60]}": c.anchor_id for (_, r), c in zip(df.iterrows(), result.candidates)}
        current = next((k for k, v in options.items() if v == bundle.anchor_id), None)
        pick = st.selectbox("Inspect candidate", list(options), index=list(options).index(current) if current else 0, key="pick")
        if options[pick] != bundle.anchor_id and st.button("Inspect this candidate"):
            p = state["params"]
            _run(investigator, backend_signature, p["text"], p["mode"], p["lib_version"], p["py_version"], p["hide_fixed"], options[pick])
            st.rerun()

    tabs = st.tabs(["Summary", "Fix Graph", "Code Diff", "Evidence", "Cypher"])
    with tabs[0]:
        st.caption(f"Explanation mode: **{answer.mode}**")
        for w in answer.warnings:
            st.warning(w)
        st.markdown(answer.to_markdown().split("\n", 1)[1])
        dl1, dl2 = st.columns(2)
        dl1.download_button("Download investigation (JSON)", json.dumps({**result.to_dict(), "answer": answer.to_dict()}, indent=2, default=str), "investigation.json", "application/json")
        dl2.download_button("Download explanation (Markdown)", answer.to_markdown(), "explanation.md", "text/markdown")
    with tabs[1]:
        if bundle.fixes or bundle.issue:
            st.graphviz_chart(fix_path_dot(bundle), use_container_width=True)
            st.caption("Solid edge = verified · dashed = referenced in text · dotted = inferred. Click a node to open it on GitHub.")
        else:
            st.info("Nothing to draw: no candidate passed the evidence gate.")
    with tabs[2]:
        render_diffs(bundle)
    with tabs[3]:
        _render_evidence(bundle)
    with tabs[4]:
        for t in result.trace:
            st.markdown(f"**{t['name']}** · {t['rows']} rows · {t['ms']} ms")
            st.code(t["cypher"], language="cypher")
            st.json(t["params"], expanded=False)
        st.markdown("**Parsed query**")
        st.json(result.query.to_dict(), expanded=False)


def _render_evidence(bundle) -> None:
    for fx in bundle.fixes:
        st.markdown(f"**PR #{fx.pr_number} → {'issue #' + str(bundle.issue['number']) if bundle.issue else 'no issue'}**: "
                    f"`{fx.link_type}` · {BADGE.get(fx.verification_status, '')} — {STATUS_TEXT.get(fx.verification_status, '')}")
        st.caption("Bases: " + ", ".join(fx.bases or ["-"]) + (f" · [source]({fx.link_source_url})" if fx.link_source_url else ""))
    rows = [{"Id": i.eid, "Kind": i.kind, "Evidence": i.label, "Source": i.url or "snapshot data"} for i in bundle.items.values()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, column_config={"Source": st.column_config.LinkColumn()})
    if bundle.missing:
        st.markdown("**Missing evidence / uncertainty**")
        for m in bundle.missing:
            st.markdown(f"- {m}")
    for c in bundle.context:
        with st.expander(f"Context [{c['eid']}]: {c['title']}"):
            st.code(c["text"][:2000], language="python" if c["label"] == "CodeChunk" else None)

