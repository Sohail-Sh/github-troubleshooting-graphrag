from retrieval.embeddings import HashingEmbedder, cosine
from retrieval.hybrid_search import AnchorCandidate, Hit, breadth_weight, rrf
from retrieval.query_parser import fulltext_query, lucene_escape, parse_query
from retrieval.rerank import score_candidates


def test_parse_query_message_and_keywords():
    q = parse_query("UnicodeError: encoding with 'idna' codec failed (UnicodeError: label empty or too long)", "2.26.0")
    assert q.exception_types == ["UnicodeError"]
    assert "idna" in q.keywords and "too" not in q.keywords
    assert q.key_terms[0] == "unicodeerror"
    assert q.package_versions["library"] == "2.26.0"
    ft = fulltext_query(q)
    assert "UnicodeError^3" in ft and "idna" in ft


def test_lucene_escape():
    assert lucene_escape("a:b(c)") == "a\\:b\\(c\\)"


def test_rrf():
    a = [Hit("x", "k", "", 1, 1, "keyword"), Hit("y", "k", "", 1, 2, "keyword")]
    b = [Hit("y", "k", "", 1, 1, "vector")]
    s = rrf([a, b], k=60)
    assert s["y"] > s["x"]


def test_breadth_weight():
    assert breadth_weight(2) == 1.0 and breadth_weight(64) < 0.3


def test_hashing_embedder_is_deterministic_and_lexical():
    e = HashingEmbedder()
    a, b, c = e.embed_documents(["UnicodeError idna label", "idna UnicodeError label", "pandas dataframe merge"])
    assert len(a) == 384 and e.embed_query("UnicodeError idna label") == a
    assert cosine(a, b) > cosine(a, c)


def _anchor(aid, label, score, texts):
    return AnchorCandidate(aid, label, text_score=score, best_rank=1, vector_similarity=0.7, texts=texts)


def test_score_candidates_gate_and_version():
    q = parse_query("UnicodeError: label empty or too long idna", "2.28.0")
    anchors = [_anchor("i1", "Issue", 0.05, ["UnicodeError label empty too long idna"]), _anchor("i2", "Issue", 0.04, ["ValueError other text"])]
    profiles = {
        "i1": {"number": 1, "fixes": [{"status": "verified", "earliest_release": ["v2.27.0"], "changed_files": ["a.py"], "hunk_sections": [], "fix_exceptions": ["UnicodeError"], "file_count": 1}]},
        "i2": {"number": 2, "fixes": []},
    }
    out = score_candidates(q, anchors, profiles, "graph")
    top = out[0]
    assert top.anchor_id == "i1" and top.strong_match and top.evidence_status == "verified"
    assert top.already_fixed_in_user_version is True  # 2.28.0 >= v2.27.0
    assert any("already contains" in r for r in top.reasons)
    other = next(c for c in out if c.anchor_id == "i2")
    assert other.exception_match is False and not other.strong_match


def test_sweeping_pr_is_weak_evidence():
    q = parse_query("KeyError: host proxy")
    anchors = [_anchor("p", "PullRequest", 0.05, ["KeyError host proxy"])]
    profiles = {"p": {"number": 9, "fixes": [{"status": "self", "earliest_release": [], "changed_files": [], "hunk_sections": [], "fix_exceptions": [], "file_count": 60}]}}
    c = score_candidates(q, anchors, profiles, "graph")[0]
    assert c.evidence_strength < 0.5 and any("sweeping" in r for r in c.reasons)
