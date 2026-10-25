"""End-to-end tests against Neo4j with the frozen psf/requests v2.32.3 snapshot loaded.

    python -m graph.ingest_neo4j psf-requests-v2.32.3 --reset --embedder hashing
"""

import pytest

from conftest import requires_neo4j

pytestmark = requires_neo4j


@pytest.fixture(scope="module")
def inv():
    from retrieval.pipeline import Investigator

    return Investigator()


@pytest.fixture(scope="module")
def store():
    from graph.db import Neo4jStore

    return Neo4jStore()


def test_milestone_fix_path_5367(store):
    """Issue #5367 -> PR #5414 -> requests/models.py -> merge commit -> earliest release v2.27.0, from git evidence alone."""
    rows = store.query(
        """
        MATCH (pr:PullRequest {number: 5414})-[link:REFERENCES]->(i:Issue {number: 5367})
        MATCH (pr)-[:MERGED_AS]->(c:Commit)<-[rc:CONTAINS_COMMIT {earliest_containing: true}]-(r:Release)
        MATCH (pr)-[:HAS_PATCH]->(h:PatchHunk)-[:IN_FILE]->(f:File {path: 'requests/models.py'})
        RETURN link.verification_status AS status, c.sha AS sha, r.tag AS tag, h.diff AS diff
        """
    )
    assert rows, "fix path not found"
    r = rows[0]
    assert r["status"] == "referenced"  # git text mention: never shown as verified
    assert r["sha"].startswith("d0965999")
    assert r["tag"] == "v2.27.0"
    assert "+        elif host.startswith((u'*', u'.')):" in r["diff"]


def test_no_unverified_closes(store):
    bad = store.query("MATCH ()-[r:CLOSES]->() WHERE r.verification_status <> 'verified' RETURN count(r) AS n")[0]["n"]
    assert bad == 0


def test_ingestion_is_idempotent(store):
    dupes = store.query("MATCH (p:PullRequest) WITH p.number AS n, count(*) AS c WHERE c > 1 RETURN count(*) AS d")[0]["d"]
    assert dupes == 0


def test_every_release_link_has_ancestry_basis(store):
    n = store.query("MATCH ()-[r:CONTAINS_COMMIT]->() WHERE r.basis <> 'git_tag_ancestry' RETURN count(r) AS n")[0]["n"]
    assert n == 0


def test_traceback_finds_fix(inv):
    tb = (
        'Traceback (most recent call last):\n  File "/usr/lib/python3.9/site-packages/requests/models.py", line 401, in prepare_url\n'
        "    host = self._get_idna_encoded_host(host)\nUnicodeError: encoding with 'idna' codec failed (UnicodeError: label empty or too long)"
    )
    res = inv.investigate(tb, "graph", library_version="2.26.0")
    assert res.status == "fix_found"
    fx = res.bundle.primary_fix
    assert fx.pr_number == 5414 and fx.earliest_release == "v2.27.0"
    assert any(f["path"] == "requests/models.py" for f in fx.files)
    assert res.bundle.match["already_fixed_in_user_version"] is False


def test_reworded_description_finds_fix(inv):
    res = inv.investigate("requests.get('http://.example.com') crashes with a UnicodeError from idna instead of InvalidURL", "graph")
    assert res.candidates[0].number == 5367


@pytest.mark.parametrize("text", ["ValueError: operands could not be broadcast together with shapes (3,) (4,)", "django.db.utils.OperationalError: no such table: auth_user"])
def test_unrelated_error_is_rejected(inv, text):
    for mode in ("keyword", "vector", "hybrid", "graph"):
        assert inv.investigate(text, mode).status == "no_reliable_match"


def test_rankings_are_reproducible(inv):
    q = "Two Host headers are sent for chunked requests"
    a = [c.anchor_id for c in inv.investigate(q, "graph").candidates]
    b = [c.anchor_id for c in inv.investigate(q, "graph").candidates]
    assert a == b


def test_version_already_fixed_is_flagged(inv):
    res = inv.investigate("requests.get('http://.example.com') crashes with a UnicodeError from idna instead of InvalidURL", "graph", library_version="2.31.0")
    c = next(c for c in res.candidates if c.number == 5367)
    assert c.already_fixed_in_user_version is True


def test_traversal_does_not_jump_between_issues_via_shared_files(inv):
    res = inv.investigate("requests.get('http://.example.com') crashes with a UnicodeError from idna instead of InvalidURL", "graph")
    b = res.bundle
    assert b.issue["number"] == 5367
    assert {fx.pr_number for fx in b.fixes} == {5414}  # PRs that merely touch models.py are not pulled in
