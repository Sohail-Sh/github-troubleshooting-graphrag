"""View 2 - the troubleshooting workbench."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from explanation.answer_generator import generate_answer
from explanation.evidence_bundle import STATUS_TEXT
from retrieval.hybrid_search import MODE_LABELS, MODES
from retrieval.related_fixes import find_related_fixes
from retrieval.rerank import EVIDENCE_LABEL

from .diff_view import render_diffs
from .graph_view import evidence_edge_rows, fix_path_dot

RELATED_FIXES_LIMIT = 8

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


def _render_version_applicability(investigator, backend_signature, state: dict[str, Any], answer, bundle) -> None:
    """Surface the engine's own Applicability text prominently, plus one-click re-runs of the SAME query
    against the two headline versions. Reuses Investigator.investigate's existing version logic
    (already_fixed_in_user_version / earliest_release) rather than a separate comparison algorithm, per
    Neo4j_NODES2026_Enhancement_Requirements_for_Claude.md section 6.2."""
    fx = bundle.primary_fix
    if not fx:
        return
    app_section = next((s for s in answer.sections if s.heading == "Applicability"), None)
    if app_section:
        m = bundle.match or {}
        already_fixed = m.get("already_fixed_in_user_version")
        kind = "success" if already_fixed else "warning" if already_fixed is False else "info"
        getattr(st, kind)(f"**Version applicability.** {app_section.text}")
    if state["params"].get("hide_fixed"):
        st.caption("\"Hide fixes already in my version\" is on - candidates already fixed in your installed version are excluded from the ranking above.")
    v1, v2, _ = st.columns([1, 1, 3])
    p = state["params"]
    if v1.button("Show for installed 2.26.0", help="Re-runs this same query with Library version = 2.26.0.", key="show_226"):
        _run(investigator, backend_signature, p["text"], p["mode"], "2.26.0", p["py_version"], p["hide_fixed"], bundle.anchor_id)
        st.rerun()
    if v2.button("Show for installed 2.31.0", help="Re-runs this same query with Library version = 2.31.0 (already contains this fix).", key="show_231"):
        _run(investigator, backend_signature, p["text"], p["mode"], "2.31.0", p["py_version"], p["hide_fixed"], bundle.anchor_id)
        st.rerun()


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
    if state["params"]["text"].strip() != st.session_state.get("query_text", "").strip():
        # "Investigate separately" (Related Fixes) and other non-form re-runs can't update the textarea above
        # (Streamlit forbids writing a widget's session-state key after that widget is drawn in the same run)
        # so make unmistakably clear what was actually searched for.
        st.caption(f"Now showing results for: “{state['params']['text'][:200]}”")

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

    _render_version_applicability(investigator, backend_signature, state, answer, bundle)

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

    related = _get_related_fixes(investigator, backend_signature, state, bundle)

    tabs = st.tabs(["Summary", "Fix Graph", "Related Fixes", "Code Diff", "Evidence", "Cypher"])
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
            _render_evidence_edges(investigator, bundle)
        else:
            st.info("Nothing to draw: no candidate passed the evidence gate.")
    with tabs[2]:
        _render_related_fixes(investigator, backend_signature, state, bundle, related)
    with tabs[3]:
        render_diffs(bundle)
    with tabs[4]:
        _render_evidence(bundle)
    with tabs[5]:
        all_trace = result.trace + (related["trace"] if related else [])
        for t in all_trace:
            st.markdown(f"**{t['name']}** · {t['rows']} rows · {t['ms']} ms")
            st.code(t["cypher"], language="cypher")
            st.json(t["params"], expanded=False)
        st.markdown("**Parsed query**")
        st.json(result.query.to_dict(), expanded=False)


def _get_related_fixes(investigator, backend_signature, state: dict[str, Any], bundle) -> dict[str, Any] | None:
    """Lazily compute Related Fix Discovery for the selected primary fix PR, cached on the investigation's
    own state dict. Caching only needs to be keyed by the anchor PR id *within* this state object: a new
    investigation, a new selected candidate, or a different snapshot already produces a brand-new
    ``st.session_state["investigation"]`` dict (see _run / backend_signature check), so stale related results
    can never leak across those boundaries - there is nothing from a previous dict left to read."""
    fx = bundle.primary_fix
    if not fx:
        state.pop("related", None)
        return None
    cached = state.get("related")
    if cached and cached.get("anchor_pr_id") == fx.pr_id:
        return cached
    trace: list[dict[str, Any]] = []
    try:
        results = find_related_fixes(investigator.store, fx.pr_id, limit=RELATED_FIXES_LIMIT, trace=trace)
        entry = {"anchor_pr_id": fx.pr_id, "results": results, "error": None, "trace": trace}
    except Exception as exc:  # noqa: BLE001 - a related-fix failure must never break the primary result
        entry = {"anchor_pr_id": fx.pr_id, "results": [], "error": f"{type(exc).__name__}: {exc}", "trace": trace}
    state["related"] = entry
    return entry


def _render_evidence_edges(investigator, bundle) -> None:
    with st.expander("Evidence edges - why does each arrow exist?"):
        try:
            rows = evidence_edge_rows(investigator.store, bundle)
        except Exception as exc:  # noqa: BLE001 - the graph chart above must stay usable even if this fails
            st.warning(f"Could not load edge provenance: {type(exc).__name__}: {exc}")
            return
        if not rows:
            st.caption("No fix path to inspect.")
            return
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, column_config={"Source": st.column_config.LinkColumn()})
        st.caption(
            "'Strength' for CLOSES/REFERENCES/POSSIBLY_ADDRESSES is GitHub-verification strength. 'structural' means "
            "the edge is a recorded fact (a diff, a merge) with no independent verification property in this schema - "
            "not a claim that it is weaker evidence, just a different kind of fact."
        )


RELATED_FILTERS = {
    "Shared exception": lambda r: bool(r.shared_exceptions_changed_lines or r.shared_exceptions_mentioned),
    "Shared file": lambda r: bool(r.shared_files),
    "Shared function": lambda r: bool(r.shared_functions),
}


def _render_related_fixes(investigator, backend_signature, state: dict[str, Any], bundle, related: dict[str, Any] | None) -> None:
    st.markdown("#### Explore PRs with shared graph evidence")
    fx = bundle.primary_fix
    if not fx:
        st.info("Related exploration requires a supported anchor (a selected fix PR). This investigation does not have one.")
        return
    st.caption(f"Exploring [PR #{fx.pr_number}]({fx.pr_url}) · {bundle.repository} · graph traversal, not the embedding model")
    st.markdown("These are structurally related **leads**, not verified resolutions of your current bug.")
    if related is None:
        return
    if related["error"]:
        st.warning(f"Related Fix Discovery failed ({related['error']}). The Fix Graph and Summary above are unaffected.")
        return
    results = related["results"]
    if not results:
        st.info("No related PRs found under current evidence rules. No other PR in this snapshot shares a non-generic "
                "exception type, a non-busy changed file, or a function name with this fix's own patches.")
        return

    available = [label for label, pred in RELATED_FILTERS.items() if any(pred(r) for r in results)]
    choice = st.radio("Filter", ["All"] + available, horizontal=True, key="related_filter") if available else "All"
    shown = results if choice == "All" else [r for r in results if RELATED_FILTERS[choice](r)]
    st.caption(f"{len(shown)} of {len(results)} candidates shown (capped at {RELATED_FIXES_LIMIT} by the traversal itself).")

    for r in shown:
        with st.container(border=True):
            top = st.columns([4, 1, 1])
            top[0].markdown(f"**[PR #{r.pr_number}]({r.url or '#'}): {r.title}**")
            top[1].metric("Relatedness /100", r.relative_score, help="Heuristic discovery score within this result set only. Not a probability, not proof of a fix.")
            top[2].markdown(f"Earliest release&nbsp;&nbsp;\n**{r.earliest_release or 'Unknown'}**", help="Only shown when this candidate's own merge commit has a known earliest-containing release.")
            st.markdown(":orange-badge[Related candidate — NOT a verified fix for this issue]")
            for reason in r.reasons:
                st.markdown(f"- {reason}")
            with st.expander("Expand evidence"):
                st.json([dict(p) for p in r.evidence_paths], expanded=False)
            b1, b2 = st.columns([1, 3])
            if r.url:
                b1.link_button("View on GitHub", r.url)
            if b2.button(
                f"Investigate PR #{r.pr_number} separately →",
                key=f"related_investigate_{r.pr_id}",
                help="Runs a NEW, separate investigation using this PR's own title as the symptom text. "
                "This replaces the result above with its own provenance - re-run your original query to go back.",
            ):
                _run(investigator, backend_signature, r.title, "graph", None, None, False, None)
                st.rerun()


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

