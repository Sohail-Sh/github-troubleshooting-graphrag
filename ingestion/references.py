"""Find issue/PR references in text, classified by how strong the evidence is.

Evidence levels (never silently promoted):
  * ``verified``   - GitHub itself records the link (closingIssuesReferences, timeline close/connect events)
  * ``referenced`` - the text of a PR/commit explicitly mentions the number (closing keywords included)
  * ``inferred``   - heuristic only (e.g. a number in the branch name)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CLOSING_KEYWORDS = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
MIN_BARE_MENTION = 20


@dataclass(frozen=True)
class Reference:
    number: int
    basis: str  # e.g. closing_keyword, text_mention, url_mention, branch_name
    level: str  # referenced | inferred
    closing_keyword: bool = False
    snippet: str = ""


def _snippet(text: str, start: int, end: int, width: int = 60) -> str:
    s = max(0, start - width)
    e = min(len(text), end + width)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def find_references(text: str, owner: str, repo: str, self_number: int | None = None) -> list[Reference]:
    """Return references to issues/PRs of ``owner/repo`` found in ``text`` (deduplicated, strongest first)."""
    if not text:
        return []
    full = re.escape(f"{owner}/{repo}")
    found: dict[int, Reference] = {}

    def add(ref: Reference) -> None:
        if self_number is not None and ref.number == self_number:
            return
        prev = found.get(ref.number)
        if prev is None or (ref.closing_keyword and not prev.closing_keyword):
            found[ref.number] = ref

    closing = re.compile(
        rf"\b{CLOSING_KEYWORDS}\s*:?\s+(?:(?P<repo>[\w.-]+/[\w.-]+)?#|https?://github\.com/{full}/(?:issues|pull)/)(?P<num>\d+)",
        re.IGNORECASE,
    )
    for m in closing.finditer(text):
        if m.group("repo") and m.group("repo").lower() != f"{owner}/{repo}".lower():
            continue  # cross-repository reference: not ours
        add(Reference(int(m.group("num")), "closing_keyword", "referenced", True, _snippet(text, m.start(), m.end())))

    url = re.compile(rf"https?://github\.com/{full}/(?:issues|pull)/(?P<num>\d+)", re.IGNORECASE)
    for m in url.finditer(text):
        add(Reference(int(m.group("num")), "url_mention", "referenced", False, _snippet(text, m.start(), m.end())))

    # "#1234" or "psf/requests#1234", but not "foo/bar#12", "&#123;" or "abc#12". Bare small numbers ("step #1")
    # are usually list numbering, so they need an explicit "issue"/"PR" prefix.
    mention = re.compile(r"(?<![\w&/#])(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<num>\d{1,6})\b")
    for m in mention.finditer(text):
        if m.group("repo") and m.group("repo").lower() != f"{owner}/{repo}".lower():
            continue
        num = int(m.group("num"))
        prefix = text[max(0, m.start() - 12) : m.start()].lower()
        if num < MIN_BARE_MENTION and not re.search(r"(issue|pr|pull request|bug)\s*\\?$", prefix.strip()):
            continue
        add(Reference(num, "text_mention", "referenced", False, _snippet(text, m.start(), m.end())))
    # "##6604" typos: allow a doubled hash
    for m in re.finditer(r"(?<![\w&/])##(?P<num>\d{1,6})\b", text):
        add(Reference(int(m.group("num")), "text_mention", "referenced", False, _snippet(text, m.start(), m.end())))

    return sorted(found.values(), key=lambda r: (not r.closing_keyword, r.number))


BRANCH_NUMBER_RE = re.compile(r"(?:^|[/_-])(?:issue|issues|gh|fix|bug|bugfix|hotfix)?[-_]?(\d{3,6})(?=$|[/_-])", re.I)


def references_from_branch(branch: str, self_number: int | None = None) -> list[Reference]:
    """A number in a branch name (``mondeja/5367``) suggests, but does not prove, an issue link."""
    refs = []
    for m in BRANCH_NUMBER_RE.finditer(branch or ""):
        n = int(m.group(1))
        if n != self_number:
            refs.append(Reference(n, "branch_name", "inferred", False, branch))
    return refs


MERGE_SUBJECT_RE = re.compile(r"^Merge pull request #(?P<num>\d+) from (?P<branch>\S+)")
SQUASH_SUBJECT_RE = re.compile(r"^(?P<title>.+?)\s*\(#(?P<num>\d+)\)\s*$")


def pr_number_from_commit_subject(subject: str) -> tuple[int, str, str] | None:
    """Return (pr_number, merge_style, branch_or_title) for GitHub merge or squash commit subjects."""
    m = MERGE_SUBJECT_RE.match(subject)
    if m:
        return int(m.group("num")), "merge_commit", m.group("branch")
    m = SQUASH_SUBJECT_RE.match(subject)
    if m:
        return int(m.group("num")), "squash_commit", m.group("title")
    return None
