"""View 3 - retrieval evaluation: keyword vs vector vs hybrid vs hybrid + graph."""

from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

from config import PROJECT_ROOT
from retrieval.hybrid_search import MODE_LABELS, MODES

RESULTS = PROJECT_ROOT / "evaluation" / "results" / "latest.json"
METRICS = {
    "hit_at_1": "Hit@1",
    "hit_at_5": "Hit@5",
    "mrr_at_5": "MRR@5",
    "answered_with_correct_fix": "Correct fix path",
    "no_match_rejection": "No-match rejection",
}
# Categorical slots 1-4 of the validated reference palette; color follows the mode, never its rank.
MODE_COLORS = {"keyword": "#2a78d6", "vector": "#eb6834", "hybrid": "#1baf7a", "graph": "#eda100"}


def render_evaluation(investigator, connected: bool) -> None:
    st.markdown("## Evaluation")
    st.caption("Same benchmark, four retrieval strategies. Ranking quality and fix-path correctness are measured separately.")
    if connected and st.button("Run benchmark now"):
        from evaluation.evaluate_retrieval import run

        with st.spinner("Running every case in every mode…"):
            result = run(investigator=investigator)
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(result, indent=2))
    if not RESULTS.exists():
        st.info("No results yet. Run `python -m evaluation.evaluate_retrieval` or press the button.")
        return
    result = json.loads(RESULTS.read_text())
    s = result["summary"]
    st.caption(f"snapshot `{result['snapshot']}` · embeddings `{result['embedding_model']}` · "
               f"{s[MODES[0]]['positives']} positive cases, {s[MODES[0]]['negatives']} negative controls")

    tiles = st.columns(4)
    g, h = s.get("graph", {}), s.get("hybrid", {})
    tiles[0].metric("Hit@1 (hybrid + graph)", f"{g.get('hit_at_1', 0):.2f}", f"{g.get('hit_at_1', 0) - h.get('hit_at_1', 0):+.2f} vs hybrid")
    tiles[1].metric("MRR@5 (hybrid + graph)", f"{g.get('mrr_at_5', 0):.2f}", f"{g.get('mrr_at_5', 0) - h.get('mrr_at_5', 0):+.2f} vs hybrid")
    tiles[2].metric("Correct fix path", f"{g.get('answered_with_correct_fix', 0):.2f}")
    tiles[3].metric("No-match rejection", f"{g.get('no_match_rejection', 0):.0%}")

    rows = [
        {"Mode": MODE_LABELS[m], "mode": m, "Metric": label, "Value": s[m][key]}
        for m in MODES if m in s for key, label in METRICS.items() if s[m].get(key) is not None
    ]
    df = pd.DataFrame(rows)
    domain = [MODE_LABELS[m] for m in MODES]
    base = alt.Chart(df).encode(
        x=alt.X("Mode:N", sort=domain, axis=None),
        y=alt.Y("Value:Q", scale=alt.Scale(domain=[0, 1.08]), axis=alt.Axis(format=".0%", grid=True, gridOpacity=0.3, title=None)),
    )
    bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, stroke="white", strokeWidth=2).encode(
        color=alt.Color("Mode:N", sort=domain, scale=alt.Scale(domain=domain, range=[MODE_COLORS[m] for m in MODES]), legend=alt.Legend(orient="top", title=None, labelLimit=320)),
        tooltip=["Mode", "Metric", alt.Tooltip("Value:Q", format=".3f")],
    )
    labels = base.mark_text(dy=-6, fontSize=10).encode(text=alt.Text("Value:Q", format=".2f"), color=alt.value("#52514e"))
    chart = (bars + labels).properties(width=150, height=220).facet(
        column=alt.Column("Metric:N", sort=list(METRICS.values()), header=alt.Header(title=None, labelOrient="bottom"))
    )
    st.altair_chart(chart, use_container_width=False)

    st.markdown("#### Table view")
    table = pd.DataFrame(
        {MODE_LABELS[m]: {**{METRICS[k]: s[m].get(k) for k in METRICS}, "False rejections": s[m]["false_rejections"], "Avg latency (ms)": s[m]["avg_latency_ms"]} for m in MODES if m in s}
    ).T
    st.dataframe(table, use_container_width=True)

    st.markdown("#### Per case")
    cases = pd.DataFrame(result["cases"])
    pivot = cases.pivot_table(index=["case", "kind"], columns="mode", values="rank", aggfunc="first").reindex(columns=list(MODES))
    st.dataframe(pivot.fillna("-"), use_container_width=True)
    st.caption("Values are the rank of the expected report (\"-\" = not in the top 10). Negative controls should be rejected, not ranked.")
