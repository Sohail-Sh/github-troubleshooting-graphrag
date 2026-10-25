"""Streamlit AppTest: UI state transitions against the live Neo4j snapshot."""

from streamlit.testing.v1 import AppTest

from conftest import ROOT, requires_neo4j

pytestmark = requires_neo4j
APP = str(ROOT / "app.py")


def run_query(at: AppTest, text: str) -> AppTest:
    at.text_area(key="query_text").set_value(text)
    at.button[[b.label for b in at.button].index("Investigate")].click()
    return at.run(timeout=60)


def test_fix_found_flow():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = run_query(at, "requests.get('http://.example.com') crashes with a UnicodeError from idna instead of InvalidURL")
    assert not at.exception
    assert any("Historical fix found" in s.value for s in at.success)
    assert any("Issue #5367" in m.value for m in at.markdown)
    assert [t.label for t in at.tabs] == ["Summary", "Fix Graph", "Code Diff", "Evidence", "Cypher"]


def test_no_match_flow_replaces_previous_result():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = run_query(at, "requests.get('http://.example.com') crashes with a UnicodeError from idna instead of InvalidURL")
    at = run_query(at, "ModuleNotFoundError: No module named 'pandas'")
    assert any("No sufficiently supported historical fix found" in e.value for e in at.error)
    assert not any("Historical fix found" in s.value for s in at.success)


def test_stale_results_are_hidden_after_backend_change():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = run_query(at, "Two Host headers are sent for chunked requests")
    at.session_state["investigation"]["backend"] = "bolt://elsewhere|other-snapshot|t0"
    at = at.run()
    assert any("different database or snapshot" in w.value for w in at.warning)


def test_empty_query_shows_error():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = run_query(at, "   ")
    assert any("enter an error message" in e.value for e in at.error)
