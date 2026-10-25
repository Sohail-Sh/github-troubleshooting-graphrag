"""Compare keyword / vector / hybrid / hybrid+graph retrieval on the benchmark.

    python -m evaluation.evaluate_retrieval [--cases evaluation/benchmark_cases.json] [--out evaluation/results]

Metrics (positives): Hit@1, Hit@5, MRR@5 over anchors. A hit is the expected issue, or the expected PR when
the system anchors on the PR. Negatives: rejection rate (no strong match). Fix-path evidence correctness is
measured separately in :mod:`evaluation.evaluate_evidence` because graph expansion can make the evidence
complete without changing which report ranks first.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT
from retrieval.hybrid_search import MODES
from retrieval.pipeline import Investigator

from .evaluate_evidence import check_evidence

DEFAULT_CASES = PROJECT_ROOT / "evaluation" / "benchmark_cases.json"
DEFAULT_OUT = PROJECT_ROOT / "evaluation" / "results"


def is_hit(candidate: dict[str, Any], case: dict[str, Any]) -> bool:
    if candidate["label"] == "Issue":
        return candidate["number"] == case.get("expected_issue")
    if candidate["label"] == "PullRequest":
        return candidate["number"] == case.get("expected_pr")
    return False


def run(cases_path: Path = DEFAULT_CASES, modes: tuple[str, ...] = MODES, investigator: Investigator | None = None) -> dict[str, Any]:
    spec = json.loads(Path(cases_path).read_text())
    inv = investigator or Investigator()
    per_case: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for mode in modes:
        rows = []
        for case in spec["cases"]:
            res = inv.investigate(case["query"], mode=mode, library_version=case.get("library_version"))
            cands = [c.to_dict() for c in res.candidates]
            rank = next((c["rank"] for c in cands if is_hit(c, case)), None)
            row = {
                "mode": mode,
                "case": case["id"],
                "kind": case["kind"],
                "rank": rank,
                "status": res.status,
                "top": [f"{c['label']}#{c['number']}" for c in cands[:5]],
                "latency_ms": res.timings_ms["total"],
                "rejected": res.status == "no_reliable_match",
            }
            if case["kind"] == "positive":
                row.update(check_evidence(res.bundle, case))
            rows.append(row)
        per_case.extend(rows)
        pos = [r for r in rows if r["kind"] == "positive"]
        neg = [r for r in rows if r["kind"] == "negative"]
        summary[mode] = {
            "hit_at_1": round(sum(1 for r in pos if r["rank"] == 1) / len(pos), 3) if pos else None,
            "hit_at_5": round(sum(1 for r in pos if r["rank"] and r["rank"] <= 5) / len(pos), 3) if pos else None,
            "mrr_at_5": round(sum(1 / r["rank"] for r in pos if r["rank"] and r["rank"] <= 5) / len(pos), 3) if pos else None,
            "answered_with_correct_fix": round(sum(1 for r in pos if r.get("fix_path_correct")) / len(pos), 3) if pos else None,
            "false_rejections": sum(1 for r in pos if r["rejected"]),
            "no_match_rejection": round(sum(1 for r in neg if r["rejected"]) / len(neg), 3) if neg else None,
            "avg_latency_ms": round(statistics.mean(r["latency_ms"] for r in rows), 1),
            "positives": len(pos),
            "negatives": len(neg),
        }
    return {
        "snapshot": inv.meta.get("snapshot_id"),
        "embedding_model": inv.embedder.name,
        "cases_file": str(cases_path),
        "summary": summary,
        "cases": per_case,
    }


def print_table(result: dict[str, Any]) -> None:
    cols = ["hit_at_1", "hit_at_5", "mrr_at_5", "answered_with_correct_fix", "no_match_rejection", "false_rejections", "avg_latency_ms"]
    print(f"snapshot={result['snapshot']} embedding={result['embedding_model']}")
    print(f"{'mode':10}" + "".join(f"{c:>27}" for c in cols))
    for mode, s in result["summary"].items():
        print(f"{mode:10}" + "".join(f"{str(s[c]):>27}" for c in cols))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--modes", default=",".join(MODES))
    a = p.parse_args(argv)
    result = run(a.cases, tuple(a.modes.split(",")))
    a.out.mkdir(parents=True, exist_ok=True)
    out_file = a.out / f"retrieval_{result['snapshot']}_{result['embedding_model'].replace('/', '_')}.json"
    out_file.write_text(json.dumps(result, indent=2))
    (a.out / "latest.json").write_text(json.dumps(result, indent=2))
    print_table(result)
    misses = [c for c in result["cases"] if c["kind"] == "positive" and c["mode"] == "graph" and c["rank"] != 1]
    for m in misses:
        print(f"  graph miss: {m['case']} rank={m['rank']} status={m['status']} top={m['top'][:3]}")
    print(f"written {out_file}")


if __name__ == "__main__":
    main()
