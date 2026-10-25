from ingestion.extract_errors import exception_types_in_code, extract_errors, normalize_message
from ingestion.extract_patches import parse_hunks, parse_unified_diff
from ingestion.git_history import chunk_python, parse_changelog
from ingestion.ids import parse_repo_url
from ingestion.references import find_references, pr_number_from_commit_subject, references_from_branch

TRACEBACK = """Traceback (most recent call last):
  File "app.py", line 3, in <module>
    requests.get("http://.example.com")
  File "/usr/lib/python3.9/site-packages/requests/models.py", line 401, in prepare_url
    host = self._get_idna_encoded_host(host)
UnicodeError: encoding with 'idna' codec failed (UnicodeError: label empty or too long)
requests==2.26.0"""


def test_parse_repo_url():
    r = parse_repo_url("https://github.com/psf/requests")
    assert (r.owner, r.name, r.id) == ("psf", "requests", "repo:psf/requests")
    assert parse_repo_url("psf/requests.git").name == "requests"
    assert parse_repo_url("github.com/psf/requests/").full_name == "psf/requests"


def test_extract_errors_from_traceback():
    p = extract_errors(TRACEBACK)
    assert p.exception_types == ["UnicodeError"]
    assert p.errors[0].message.startswith("encoding with 'idna' codec failed")
    assert [f.function_name for f in p.frames] == ["<module>", "prepare_url"]
    assert p.frames[1].normalized_path == "requests/models.py"
    assert p.package_versions["requests"] == "2.26.0"


def test_normalize_message_strips_volatile_parts():
    assert normalize_message("Max retries exceeded with url: http://x.io/a (port 8080)") == normalize_message(
        "Max retries exceeded with url: https://y.org/b (port 443)"
    )


def test_exception_types_in_code():
    diff = "-        raise ValueError('x')\n+        raise InvalidURL('URL has an invalid label.')\n             except UnicodeError:"
    assert exception_types_in_code(diff) == ["ValueError", "InvalidURL", "UnicodeError"]


def test_references_classification():
    text = "Fixes #5367. See also #4000 and psf/requests#12 and other/repo#99. Step #1."
    refs = {r.number: r for r in find_references(text, "psf", "requests", self_number=5414)}
    assert refs[5367].closing_keyword and refs[5367].level == "referenced"
    assert not refs[4000].closing_keyword
    assert 99 not in refs  # other repository
    assert 1 not in refs  # list numbering, not an issue
    assert find_references("closes #5414", "psf", "requests", self_number=5414) == []


def test_branch_references_are_inferred_only():
    refs = references_from_branch("mondeja/5367", self_number=5414)
    assert [(r.number, r.level) for r in refs] == [(5367, "inferred")]
    assert references_from_branch("feature/http2") == []


def test_pr_number_from_commit_subject():
    assert pr_number_from_commit_subject("Merge pull request #5414 from mondeja/5367") == (5414, "merge_commit", "mondeja/5367")
    assert pr_number_from_commit_subject("Fix urllib3 pin in setup.cfg (#6545)") == (6545, "squash_commit", "Fix urllib3 pin in setup.cfg")
    assert pr_number_from_commit_subject("Bump version") is None


def test_parse_unified_diff():
    diff = """diff --git a/requests/models.py b/requests/models.py
index e7d292d5..d8638818 100644
--- a/requests/models.py
+++ b/requests/models.py
@@ -403,7 +403,7 @@ class PreparedRequest(RequestEncodingMixin, RequestHooksMixin):
                 host = self._get_idna_encoded_host(host)
             except UnicodeError:
                 raise InvalidURL('URL has an invalid label.')
-        elif host.startswith(u'*'):
+        elif host.startswith((u'*', u'.')):
             raise InvalidURL('URL has an invalid label.')
diff --git a/docs/new.rst b/docs/new.rst
new file mode 100644
--- /dev/null
+++ b/docs/new.rst
@@ -0,0 +1,2 @@
+Title
+=====
"""
    files = parse_unified_diff(diff)
    assert [(f.path, f.status) for f in files] == [("requests/models.py", "modified"), ("docs/new.rst", "added")]
    h = files[0].hunks[0]
    assert (h.old_start, h.new_start, h.additions, h.deletions) == (403, 403, 1, 1)
    assert h.section.startswith("class PreparedRequest")
    assert "u'.'" in h.added_text


def test_parse_hunks_github_patch_field():
    hunks = parse_hunks("@@ -1,2 +1,3 @@\n a\n+b\n c\n@@ -10 +11 @@\n-x\n+y")
    assert len(hunks) == 2 and hunks[1].old_lines == 1


def test_parse_changelog():
    text = """Release History
===============

2.27.0 (2022-01-03)
-------------------

**Bugfixes**

- Fixed idna exception leak, wrapping `UnicodeError` with
  `requests.exceptions.InvalidURL` for URLs with a leading dot (.) in the
  domain. (#5414)

2.26.0 (2021-07-13)
-------------------

- Something else. (#5800)
"""
    rel = parse_changelog(text)
    assert [r["version"] for r in rel] == ["2.27.0", "2.26.0"]
    entry = rel[0]["entries"][0]
    assert entry["category"] == "Bugfixes" and entry["text"].endswith("(#5414)")


def test_chunk_python():
    src = "import x\n\ndef a():\n    return 1\n\nclass B:\n    def m(self):\n        pass\n"
    assert [c["symbol"] for c in chunk_python(src)] == ["a", "B.m"]
