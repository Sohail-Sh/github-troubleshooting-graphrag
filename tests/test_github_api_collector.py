"""The GitHub API collector and the normalizer's handling of API evidence, against a fake GitHub.

The fake repository is ``acme/widgets``: synthetic test data, not a real project.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ingestion.fetch_repository import collect_api
from ingestion.github_client import GitHubClient, GitHubError
from ingestion.ids import RepoRef
from ingestion.normalize import build_graph
from ingestion.snapshot import Snapshot
from ingestion.validate_links import validate_graph

REPO = RepoRef("acme", "widgets")
API = "https://api.github.com/repos/acme/widgets"

ISSUE_10 = {
    "number": 110, "title": "Crash: KeyError 'host' when proxy URL has no scheme", "state": "closed",
    "body": 'Traceback:\n  File "/x/site-packages/widgets/adapters.py", line 5, in get_connection\nKeyError: \'host\'',
    "labels": [{"name": "bug"}], "created_at": "2021-01-01T00:00:00Z", "closed_at": "2021-02-01T00:00:00Z",
    "user": {"login": "reporter"}, "comments": 1, "html_url": "https://github.com/acme/widgets/issues/110",
}
ISSUE_11 = {**ISSUE_10, "number": 111, "title": "Docs typo", "body": "typo", "comments": 0, "html_url": "https://github.com/acme/widgets/issues/111"}
PR_ITEM_IN_ISSUES = {"number": 120, "title": "PR shows up in /issues", "pull_request": {}, "created_at": "2021-01-05T00:00:00Z"}
PR_20 = {
    "number": 120, "title": "Validate proxy host", "body": "This fixes the crash.\n\nCloses #110", "merged_at": "2021-01-20T00:00:00Z",
    "merge_commit_sha": "a" * 40, "head": {"ref": "fix-110"}, "base": {"ref": "main"}, "user": {"login": "dev"},
    "html_url": "https://github.com/acme/widgets/pull/120", "created_at": "2021-01-05T00:00:00Z",
}
PR_21 = {**PR_20, "number": 121, "title": "Mention only", "body": "Related to #111", "merge_commit_sha": "b" * 40,
         "head": {"ref": "misc"}, "html_url": "https://github.com/acme/widgets/pull/121"}
FILES_20 = [{"filename": "widgets/adapters.py", "status": "modified", "additions": 2, "deletions": 1,
             "patch": "@@ -4,3 +4,4 @@ def get_connection(url):\n-    host = parts['host']\n+    if 'host' not in parts:\n+        raise InvalidProxyURL(url)\n     return host"}]


class FakeResponse:
    def __init__(self, data, status=200, links=None, headers=None):
        self._data, self.status_code, self.links, self.headers = data, status, links or {}, headers or {}
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def request(self, method, url, timeout=None, params=None, json=None):
        self.calls.append((method, url))
        if method == "POST":
            number = json["variables"]["number"]
            nodes = [{"number": 110, "repository": {"nameWithOwner": "acme/widgets"}}] if number == 120 else []
            return FakeResponse({"data": {"repository": {"pullRequest": {"closingIssuesReferences": {"nodes": nodes}}}}})
        for pattern, resp in self.routes.items():
            if re.fullmatch(pattern, url):
                return resp() if callable(resp) else resp
        return FakeResponse({"message": "Not Found"}, 404)


def routes():
    return {
        API: FakeResponse({"default_branch": "main", "private": False, "description": "test"}),
        f"{API}/issues": FakeResponse([ISSUE_11, PR_ITEM_IN_ISSUES, ISSUE_10]),
        f"{API}/pulls": FakeResponse([PR_21, PR_20]),
        f"{API}/releases": FakeResponse([{"tag_name": "v1.1.0", "published_at": "2021-03-01T00:00:00Z", "body": "Fixed proxy crash (#120)", "html_url": "https://github.com/acme/widgets/releases/tag/v1.1.0"}]),
        f"{API}/pulls/120/files": FakeResponse(FILES_20),
        f"{API}/pulls/121/files": FakeResponse([]),
        f"{API}/issues/110/comments": FakeResponse([{"id": 900, "body": "Same KeyError here", "user": {"login": "x"}, "created_at": "2021-01-02T00:00:00Z", "html_url": "https://github.com/acme/widgets/issues/110#issuecomment-900"}]),
        f"{API}/issues/111/timeline": FakeResponse([]),
        f"{API}/issues/110/timeline": FakeResponse([
            {"event": "cross-referenced", "source": {"issue": {"number": 120, "pull_request": {"merged_at": "2021-01-20T00:00:00Z"}, "repository": {"full_name": "acme/widgets"}}}},
            {"event": "closed", "commit_id": "a" * 40, "created_at": "2021-01-20T00:00:00Z"},
        ]),
    }


@pytest.fixture()
def snapshot(tmp_path: Path) -> Snapshot:
    snap = Snapshot.create(tmp_path, "acme-test", repository={"owner": "acme", "name": "widgets", "url": REPO.html_url, "id": REPO.id}, collected_at="2026-01-01T00:00:00Z")
    client = GitHubClient(token="test-token", session=FakeSession(routes()), sleep=lambda s: None)
    collect_api(snap, REPO, client, "2020-01-01", "2021-12-31", max_issues=100, max_prs=100)
    return snap


def test_collect_api_writes_raw_evidence(snapshot: Snapshot):
    issues = snapshot.read("api", "issues")
    assert sorted(i["number"] for i in issues) == [110, 111]  # the PR listed under /issues is skipped
    assert snapshot.read("api", "closing_references") == [{"pr_number": 120, "issue_number": 110, "basis": "github_closing_reference"}]
    assert {f["pr_number"] for f in snapshot.read("api", "pr_files")} == {120}
    assert snapshot.read("api", "comments")[0]["body"] == "Same KeyError here"


def test_normalize_api_evidence_levels(snapshot: Snapshot):
    g = build_graph(snapshot)
    rels = {(t, s.split(":")[-1], e.split(":")[-1]): r["props"] for (t, s, e), r in g.rels.items()}
    closes = rels[("CLOSES", "120", "110")]
    assert closes["verification_status"] == "verified"
    assert set(closes["bases"]) == {"github_closing_reference", "timeline_closed_by_commit"}
    # The "Related to #111" mention stays a REFERENCES link and is never promoted to CLOSES.
    assert rels[("REFERENCES", "121", "111")]["verification_status"] == "referenced"
    assert ("CLOSES", "121", "111") not in rels
    # Branch name "fix-110" is inferred evidence only.
    assert rels[("POSSIBLY_ADDRESSES", "120", "110")]["verification_status"] == "inferred"
    issue = g.nodes[f"{REPO.id}:issue:110"]["props"]
    assert issue["hydrated"] and issue["title"].startswith("Crash")
    assert any(t == "HAS_STACK_FRAME" for (t, _, _) in g.rels)
    hunks = [n for n in g.nodes.values() if "PatchHunk" in n["labels"]]
    assert len(hunks) == 1 and "InvalidProxyURL" in hunks[0]["props"]["diff"]
    report = validate_graph(list(g.nodes.values()), [r for r in g.rels.values()], REPO)
    assert report["ok"], report


def test_validator_rejects_promoted_and_foreign_links():
    nodes = [
        {"id": f"{REPO.id}:pr:1", "labels": ["PullRequest"], "props": {}},
        {"id": f"{REPO.id}:issue:2", "labels": ["Issue"], "props": {}},
    ]
    rels = [
        {"type": "CLOSES", "start": nodes[0]["id"], "end": nodes[1]["id"], "props": {"verification_status": "referenced"}},
        {"type": "REFERENCES", "start": nodes[0]["id"], "end": nodes[1]["id"], "props": {"verification_status": "referenced", "source_url": "https://github.com/other/repo/pull/1"}},
        {"type": "REFERENCES", "start": nodes[0]["id"], "end": "repo:acme/widgets:issue:404", "props": {"verification_status": "referenced"}},
    ]
    report = validate_graph(nodes, rels, REPO)
    assert report["problems"] == {"evidence_status_violation": 1, "foreign_source_url": 1, "missing_endpoint": 1}
    assert report["dropped_relationships"] == 3


def test_client_paginates_and_retries(tmp_path):
    pages = iter([
        FakeResponse({"message": "boom"}, 502),
        FakeResponse([{"n": 1}], links={"next": {"url": "https://api.github.com/page2"}}),
    ])
    session = FakeSession({f"{API}/things": lambda: next(pages), "https://api.github.com/page2": FakeResponse([{"n": 2}])})
    client = GitHubClient(session=session, cache_dir=tmp_path, sleep=lambda s: None)
    assert [x["n"] for x in client.paginate(f"{API}/things")] == [1, 2]
    calls = len(session.calls)
    assert [x["n"] for x in client.paginate(f"{API}/things")] == [1, 2]  # served from the cache
    assert len(session.calls) == calls


def test_client_raises_on_404():
    client = GitHubClient(session=FakeSession({}), sleep=lambda s: None)
    with pytest.raises(GitHubError):
        client.get(f"{API}/missing")
