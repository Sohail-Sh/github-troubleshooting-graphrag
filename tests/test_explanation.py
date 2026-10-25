from explanation.answer_generator import Answer, Section, deterministic_answer, generate_answer
from explanation.answer_validator import validate_answer
from explanation.evidence_bundle import build_bundle

QUERY = {"raw": "UnicodeError: label empty", "exception_types": ["UnicodeError"], "key_terms": ["unicodeerror"], "package_versions": {"library": "2.26.0"}}
CANDIDATE = {
    "anchor_id": "repo:psf/requests:issue:5367", "label": "Issue", "rank": 1, "retrieval_score": 0.04, "final_score": 0.8,
    "term_coverage": 0.6, "match_support": 0.75, "matched_terms": ["unicodeerror"], "missing_terms": [], "vector_similarity": 0.7,
    "exception_match": True, "frame_match": 0.0, "reasons": ["matches 1/1 key terms"], "evidence_status": "referenced",
    "already_fixed_in_user_version": False,
}
PATH = {
    "issue": {"id": "repo:psf/requests:issue:5367", "number": 5367, "title": None, "url": "https://github.com/psf/requests/issues/5367", "hydrated": False, "type_confirmed": False},
    "comments": [],
    "pr": {"number": 5414, "title": "Raise InvalidUrl if host starts with '.' character.", "url": "https://github.com/psf/requests/pull/5414", "body": ""},
    "links": [{"type": "REFERENCES", "verification_status": "referenced", "basis": "commit_message_text_mention", "bases": ["commit_message_text_mention"], "source_url": "https://github.com/psf/requests/commit/658c10e8"}],
    "merge_commit": {"sha": "d09659997cd1e3eca49a07c59ece5557071c0ab9", "short_sha": "d0965999", "url": "https://github.com/psf/requests/commit/d0965999", "message": "Merge pull request #5414"},
    "releases": [{"tag": "v2.27.0", "url": "u", "published_at": "2022-01-03", "earliest": True, "basis": "git_tag_ancestry", "source_url": "s"}],
    "containing_release_count": 9,
    "files": [{"path": "requests/models.py", "additions": 1, "deletions": 1}],
    "hunks": [{"path": "requests/models.py", "diff": "@@ -1 +1 @@\n-elif host.startswith(u'*'):\n+elif host.startswith((u'*', u'.')):", "section": "", "url": "u"}],
    "all_hunk_count": 2,
    "release_notes": [{"version": "2.27.0", "text": "Fixed idna exception leak, wrapping UnicodeError with InvalidURL. (#5414)", "category": "Bugfixes"}],
}


def bundle(status="fix_found"):
    return build_bundle(status, "https://github.com/psf/requests", dict(QUERY), dict(CANDIDATE), [dict(PATH)], [], True)


def test_bundle_discloses_missing_evidence():
    b = bundle()
    assert b.primary_fix.earliest_release == "v2.27.0"
    text = " ".join(b.missing)
    assert "known only from references" in text and "not confirmed" in text
    assert all(i.eid.startswith("E") for i in b.items.values())


def test_deterministic_answer_is_grounded():
    b = bundle()
    a = deterministic_answer(b)
    assert a.headline == "Historical fix found: PR #5414"
    assert validate_answer(a, b) == []
    app = next(s for s in a.sections if s.heading == "Applicability")
    assert "2.26.0 predates v2.27.0" in app.text


def test_no_match_answer():
    b = build_bundle("no_reliable_match", "r", dict(QUERY), None, [], [], True)
    a = deterministic_answer(b)
    assert a.headline == "No sufficiently supported historical fix found"
    assert "complete traceback" in " ".join(s.text for s in a.sections)


def test_validator_catches_invented_facts():
    b = bundle()
    bad = Answer("fix_found", "x", [
        Section("Historical fix", "PR #9999 fixed it in v2.99.0 and we tested the patch.", ["E2"]),
        Section("Root cause", "Unknown.", ["E77"]),
        Section("Changed code", "It changed models.py.", []),
        Section("Likely historical issue", "This is a verified link to the issue.", ["E1"]),
    ], "llm")
    problems = " | ".join(validate_answer(bad, b))
    for expected in ("#9999", "2.99.0", "overclaims", "unknown evidence E77", "Changed code: no citation", "as verified"):
        assert expected in problems


def test_llm_failure_falls_back(monkeypatch):
    import explanation.answer_generator as ag

    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(ag, "llm_answer", boom)
    a = generate_answer(bundle(), use_llm=True)
    assert a.mode == "deterministic" and "unavailable" in a.warnings[0]


def test_llm_answer_with_hallucination_is_rejected(monkeypatch):
    import explanation.answer_generator as ag

    monkeypatch.setattr(ag, "llm_answer", lambda b, s: Answer("fix_found", "h", [Section("Historical fix", "Fixed by PR #1234.", ["E2"])], "llm"))
    a = generate_answer(bundle(), use_llm=True)
    assert a.mode == "deterministic" and "rejected by the validator" in a.warnings[0]
