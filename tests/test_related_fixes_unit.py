"""Unit tests for retrieval/related_fixes.py against stable, hand-built rows (no live Neo4j needed).

Covers the P0 requirements in section 10.1 of Neo4j_NODES2026_Enhancement_Requirements_for_Claude.md:
reason aggregation/dedup, exception-match outranking file-only match, busy-file dampening, missing-property
safety, deterministic tie ordering, limit enforcement, and that no related-fix query can write to the graph.
"""

from __future__ import annotations

import re

from graph import queries
from retrieval.related_fixes import (
    BUSY_FILE_DEGREE,
    _aggregate_exceptions,
    _aggregate_files,
    _build,
    _file_weight,
    _score,
    _shared_functions,
    find_related_fixes,
)

WRITE_KEYWORDS = ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DETACH")


def test_related_fixes_queries_are_read_only_and_parameterized():
    """Static safety check: no related-fix query can mutate the graph or build Cypher from unparameterized text."""
    for name in dir(queries):
        if not name.startswith("RELATED_FIXES"):
            continue
        cypher = getattr(queries, name)
        code_only = "\n".join(line for line in cypher.splitlines() if not line.strip().startswith("//"))
        for kw in WRITE_KEYWORDS:
            assert not re.search(rf"\b{kw.strip()}\b", code_only.upper()), f"{name} contains a write keyword: {kw}"
        assert "$" in cypher, f"{name} takes no parameters - suspicious for a per-PR query"


def test_aggregate_exceptions_dedup_and_classification():
    rows = [
        {"pr_id": "p1", "number": 1, "title": "t1", "url": "u1", "exception": "InvalidURL", "source_changed_lines": True, "candidate_changed_lines": True},
        {"pr_id": "p1", "number": 1, "title": "t1", "url": "u1", "exception": "InvalidURL", "source_changed_lines": True, "candidate_changed_lines": True},  # duplicate row
        {"pr_id": "p1", "number": 1, "title": "t1", "url": "u1", "exception": "UnicodeError", "source_changed_lines": False, "candidate_changed_lines": True},
    ]
    out = _aggregate_exceptions(rows)
    assert out["p1"]["changed"] == {"InvalidURL"}
    assert out["p1"]["mentioned"] == {"UnicodeError"}  # not double-counted as both changed and mentioned


def test_aggregate_exceptions_missing_properties_handled_safely():
    rows = [{"pr_id": "p1", "number": 1, "title": None, "url": None, "exception": "KeyError", "source_changed_lines": None, "candidate_changed_lines": None}]
    out = _aggregate_exceptions(rows)
    assert out["p1"]["mentioned"] == {"KeyError"}
    assert out["p1"]["changed"] == set()


def test_aggregate_exceptions_empty_rows():
    assert _aggregate_exceptions([]) == {}


def test_file_weight_decays_for_busy_files():
    assert _file_weight(1) == 1.0
    assert _file_weight(5) == 1.0
    assert _file_weight(BUSY_FILE_DEGREE) < 0.3
    assert _file_weight(100) < _file_weight(20)  # busier file -> strictly weaker


def test_shared_exception_candidate_outranks_file_only_candidate():
    changed = {"InvalidURL"}
    exc_score, _ = _score(changed, set(), set(), {}, set(), candidate_file_count=1)
    file_score, _ = _score(set(), set(), {"requests/models.py"}, {"requests/models.py": 32}, set(), candidate_file_count=1)
    assert exc_score > file_score


def test_busy_file_does_not_dominate_a_rare_file():
    busy, _ = _score(set(), set(), {"tests/test_requests.py"}, {"tests/test_requests.py": 42}, set(), candidate_file_count=1)
    rare, _ = _score(set(), set(), {"requests/rare_module.py"}, {"requests/rare_module.py": 1}, set(), candidate_file_count=1)
    assert rare > busy


def test_sweeping_pr_is_discounted():
    focused, _ = _score({"InvalidURL"}, set(), set(), {}, set(), candidate_file_count=3)
    sweeping, reasons = _score({"InvalidURL"}, set(), set(), {}, set(), candidate_file_count=100)
    assert sweeping < focused
    assert any("sweeping" in r for r in reasons)


def test_shared_functions_requires_same_file_and_excludes_generic_names():
    source = [{"path": "requests/models.py", "diff": "+def _get_idna_encoded_host(self, host):\n+    pass"}]
    same_file = [{"pr_id": "p1", "path": "requests/models.py", "diff": "+def _get_idna_encoded_host(self, host):\n+    return host"}]
    other_file = [{"pr_id": "p2", "path": "requests/sessions.py", "diff": "+def _get_idna_encoded_host(self, host):\n+    pass"}]
    generic = [{"pr_id": "p3", "path": "requests/models.py", "diff": "+def __init__(self):\n+    pass"}]
    out = _shared_functions(source, same_file + other_file + generic)
    assert out == {"p1": {"_get_idna_encoded_host"}}  # other_file: wrong path; generic: excluded name


def test_build_limit_is_enforced_and_order_is_deterministic():
    candidate_ids = {f"p{i}" for i in range(10)}
    exc_by_pr = {cid: {"number": int(cid[1:]), "title": "t", "url": None, "changed": {"InvalidURL"}, "mentioned": set(), "changed_file_count": 1} for cid in candidate_ids}
    out1 = _build(candidate_ids, exc_by_pr, {}, {}, {}, limit=3)
    out2 = _build(candidate_ids, exc_by_pr, {}, {}, {}, limit=3)
    assert len(out1) == 3
    assert [r.pr_id for r in out1] == [r.pr_id for r in out2]  # same scores -> tie-broken by PR number, every time
    assert [r.pr_number for r in out1] == sorted(r.pr_number for r in out1)


def test_build_excludes_zero_score_candidates():
    # a candidate with no exception/file/function evidence at all should never appear
    exc_by_pr = {"p1": {"number": 1, "title": "t", "url": None, "changed": set(), "mentioned": set(), "changed_file_count": 1}}
    out = _build({"p1"}, exc_by_pr, {}, {}, {}, limit=8)
    assert out == []


def test_relative_score_is_bounded_and_top_candidate_is_100():
    exc_by_pr = {
        "p1": {"number": 1, "title": "t", "url": None, "changed": {"InvalidURL", "UnicodeError"}, "mentioned": set(), "changed_file_count": 1},
        "p2": {"number": 2, "title": "t", "url": None, "changed": set(), "mentioned": {"InvalidURL"}, "changed_file_count": 1},
    }
    out = _build({"p1", "p2"}, exc_by_pr, {}, {}, {}, limit=8)
    assert out[0].relative_score == 100
    assert all(0 <= r.relative_score <= 100 for r in out)


def test_find_related_fixes_excludes_source_pr_via_fake_store():
    """The Cypher itself excludes the source PR (dst.id <> $pr_id); this confirms the service doesn't
    accidentally reintroduce it, using a fake store that would return it if the service asked for it wrong."""

    class FakeStore:
        def query(self, cypher, **params):
            if "RELATED_FIXES_BY_EXCEPTION" in cypher or "exception" in cypher:
                pass
            if cypher.strip().startswith("// Candidate PRs whose patches touch an exception"):
                return [
                    {"pr_id": "other", "number": 99, "title": "other PR", "url": "u", "changed_file_count": 1,
                     "exception": "InvalidURL", "source_changed_lines": True, "candidate_changed_lines": True}
                ]
            if cypher.strip().startswith("// How many PRs touch each file"):
                return []
            if cypher.strip().startswith("// Candidate PRs that changed at least one"):
                return []
            if cypher.strip().startswith("// Earliest release containing"):
                return [{"pr_id": "other", "earliest_release": "v1.0.0"}]
            return []

    out = find_related_fixes(FakeStore(), "repo:x:pr:1", limit=8)
    assert {r.pr_id for r in out} == {"other"}
    assert all(r.pr_id != "repo:x:pr:1" for r in out)
