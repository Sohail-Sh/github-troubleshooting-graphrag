"""Fix-path graph as Graphviz DOT (rendered client-side by st.graphviz_chart; no system binary needed)."""

from __future__ import annotations

from explanation.evidence_bundle import EvidenceBundle

NODE_STYLE = {
    "Issue": ("#fde7d9", "#c2410c"),
    "PullRequest": ("#dbeafe", "#1d4ed8"),
    "Commit": ("#ede9fe", "#6d28d9"),
    "File": ("#dcfce7", "#15803d"),
    "PatchHunk": ("#f0fdf4", "#15803d"),
    "Release": ("#fef9c3", "#a16207"),
    "ChangelogEntry": ("#fefce8", "#a16207"),
}
EDGE_STYLE = {"verified": "solid", "referenced": "dashed", "inferred": "dotted", "self": "solid"}


def _q(s: str) -> str:
    """Quote a DOT string; real newlines become centered line breaks."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _wrap(text: str, width: int = 28) -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return "\n".join(lines[:3]) + ("…" if len(lines) > 3 else "")


def fix_path_dot(bundle: EvidenceBundle, max_files: int = 4) -> str:
    out = [
        "digraph fixpath {",
        "rankdir=LR; bgcolor=transparent; nodesep=0.35; ranksep=0.6;",
        'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, penwidth=1.4];',
        'edge [fontname="Helvetica", fontsize=9, color="#64748b", fontcolor="#334155"];',
    ]

    def node(nid: str, label: str, kind: str, url: str | None = None) -> None:
        fill, line = NODE_STYLE.get(kind, ("#f1f5f9", "#475569"))
        attrs = f"label={_q(label)}, fillcolor={_q(fill)}, color={_q(line)}"
        if url:
            attrs += f", URL={_q(url)}, target=_blank, tooltip={_q(url)}"
        out.append(f"{_q(nid)} [{attrs}];")

    def edge(a: str, b: str, label: str, style: str = "solid") -> None:
        out.append(f"{_q(a)} -> {_q(b)} [label={_q(label)}, style={style}];")

    issue = bundle.issue
    if issue:
        title = issue.get("title") or "(text not ingested)"
        node("issue", f"Issue #{issue['number']}\n{_wrap(title)}", "Issue", issue.get("url"))
    for k, fx in enumerate(bundle.fixes[:3]):
        pr = f"pr{k}"
        node(pr, f"PR #{fx.pr_number}\n{_wrap(fx.pr_title)}", "PullRequest", fx.pr_url)
        if issue and fx.link_type != "SELF":
            edge(pr, "issue", f"{fx.link_type}\n({fx.verification_status})", EDGE_STYLE.get(fx.verification_status, "solid"))
        if fx.merge_commit:
            c = f"c{k}"
            node(c, f"Commit {fx.merge_commit['short_sha']}", "Commit", fx.merge_commit.get("url"))
            edge(pr, c, "MERGED_AS")
            rels = [r for r in fx.releases if r.get("earliest")] or fx.releases[:1]
            for r in rels:
                rid = f"r{k}_{r['tag']}"
                node(rid, f"Release {r['tag']}" + ("\n(earliest)" if r.get("earliest") else ""), "Release", r.get("url"))
                edge(rid, c, "CONTAINS_COMMIT")
        hunk_files = {h["path"] for h in fx.hunks}
        for j, f in enumerate(fx.files[:max_files]):
            fid = f"f{k}_{j}"
            node(fid, f"{f['path']}\n+{f.get('additions') or 0} / -{f.get('deletions') or 0}", "File")
            edge(pr, fid, "CHANGES" + (" + HAS_PATCH" if f["path"] in hunk_files else ""))
        if len(fx.files) > max_files:
            node(f"more{k}", f"+{len(fx.files) - max_files} more files", "File")
            edge(pr, f"more{k}", "CHANGES")
        for j, n in enumerate(fx.release_notes[:1]):
            nid = f"n{k}_{j}"
            node(nid, f"Release notes {n['version']}\n{_wrap(n['text'], 32)}", "ChangelogEntry")
            edge(nid, pr, "MENTIONS", "dashed")
    out.append("}")
    return "\n".join(out)
