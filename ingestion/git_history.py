"""Collect evidence from a local git clone: PR merge/squash commits, patches, tags and release ancestry,
release notes, documentation and source code.

Everything here comes from git objects, so it is reproducible offline and needs no API token. It cannot
see issue text or GitHub's own link records; those come from :mod:`ingestion.github_client`.
"""

from __future__ import annotations

import ast
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .extract_patches import parse_unified_diff
from .references import pr_number_from_commit_subject

log = logging.getLogger(__name__)

FIELD = "\x1f"
RECORD = "\x1e"
LOG_FORMAT = FIELD.join(["%H", "%P", "%an", "%aI", "%cI", "%s", "%b"]) + RECORD
RELEASE_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")
CHANGELOG_CANDIDATES = ("HISTORY.md", "HISTORY.rst", "CHANGELOG.md", "CHANGELOG.rst", "CHANGES.md", "CHANGES.rst")
CHANGELOG_HEADER_RE = re.compile(r"^v?(?P<ver>\d+\.\d+(?:\.\d+)?)\s*\((?P<date>\d{4}-\d{2}-\d{2})\)\s*$")
MAX_FILE_PATCH_CHARS = 60_000
SKIP_PATCH_PATTERNS = (".lock", "package-lock.json", ".min.js", ".svg", ".png", ".jpg", ".gif", ".ico")


def version_key(tag: str) -> tuple[int, int, int]:
    m = RELEASE_TAG_RE.match(tag)
    if not m:
        return (-1, -1, -1)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


@dataclass
class GitRepo:
    path: Path

    def run(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def resolve(self, ref: str) -> str:
        return self.run("rev-parse", f"{ref}^{{commit}}").strip()

    def is_shallow(self) -> bool:
        return self.run("rev-parse", "--is-shallow-repository").strip() == "true"

    def show_file(self, ref: str, path: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(self.path), "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout if proc.returncode == 0 else None

    def ls_tree(self, ref: str) -> list[str]:
        return [p for p in self.run("ls-tree", "-r", "--name-only", ref).splitlines() if p]

    def log(self, *args: str) -> Iterator[dict[str, Any]]:
        out = self.run("log", f"--format={LOG_FORMAT}", *args)
        for rec in out.split(RECORD):
            rec = rec.strip("\n")
            if not rec:
                continue
            parts = rec.split(FIELD)
            if len(parts) < 7:
                continue
            sha, parents, author, authored, committed, subject, body = parts[:7]
            yield {
                "sha": sha,
                "parents": parents.split() if parents else [],
                "author_name": author,
                "authored_at": authored,
                "committed_at": committed,
                "subject": subject,
                "message": (subject + ("\n\n" + body.strip() if body.strip() else "")).strip(),
            }


def _filter_patch(files: list[Any]) -> list[dict[str, Any]]:
    out = []
    for fp in files:
        truncated = False
        hunks = []
        total = 0
        for h in fp.hunks:
            total += len(h.diff)
            if total > MAX_FILE_PATCH_CHARS:
                truncated = True
                break
            hunks.append(
                {
                    "header": h.header,
                    "section": h.section,
                    "old_start": h.old_start,
                    "old_lines": h.old_lines,
                    "new_start": h.new_start,
                    "new_lines": h.new_lines,
                    "additions": h.additions,
                    "deletions": h.deletions,
                    "diff": h.diff,
                }
            )
        skip = fp.binary or fp.path.endswith(SKIP_PATCH_PATTERNS)
        out.append(
            {
                "path": fp.path,
                "old_path": fp.old_path,
                "status": fp.status,
                "additions": fp.additions,
                "deletions": fp.deletions,
                "binary": fp.binary,
                "hunks": [] if skip else hunks,
                "patch_truncated": truncated,
            }
        )
    return out


class GitHistoryCollector:
    def __init__(self, repo_path: Path):
        self.git = GitRepo(Path(repo_path))

    # ------------------------------------------------------------------ pull requests
    def collect_pull_requests(self, ref: str, since: str | None, until: str | None) -> tuple[list[dict], list[dict]]:
        """Find PRs merged into ``ref`` via GitHub merge commits or squash commits.

        Returns (pull_requests, commits). Rebase-merged PRs carry no PR number in git and are not detected.
        """
        args = ["--first-parent", ref]
        if since:
            args.append(f"--since={since}")
        if until:
            args.append(f"--until={until}")
        prs: list[dict] = []
        commits: dict[str, dict] = {}
        for c in self.git.log(*args):
            parsed = pr_number_from_commit_subject(c["subject"])
            if not parsed:
                continue
            number, style, extra = parsed
            if style == "merge_commit" and len(c["parents"]) < 2:
                continue
            if style == "squash_commit" and len(c["parents"]) != 1:
                continue
            body_lines = c["message"].split("\n\n", 1)
            if style == "merge_commit":
                rest = body_lines[1] if len(body_lines) > 1 else ""
                title_line, _, body = rest.partition("\n")
                title, branch = title_line.strip(), extra
                pr_commits = list(self.git.log(f"{c['parents'][0]}..{c['parents'][1]}"))
                if not title and pr_commits:  # merge commit without the PR title: use the tip commit's subject
                    title = pr_commits[0]["subject"]
                diff = self.git.run("diff", "-M", "--unified=3", c["parents"][0], c["sha"])
                author = pr_commits[0]["author_name"] if pr_commits else c["author_name"]
            else:
                title, branch = extra, None
                body = body_lines[1] if len(body_lines) > 1 else ""
                pr_commits = []
                diff = self.git.run("show", "-M", "--unified=3", "--format=", c["sha"])
                author = c["author_name"]

            commits[c["sha"]] = {**c, "pr_number": number, "role": style}
            for pc in pr_commits:
                commits.setdefault(pc["sha"], {**pc, "pr_number": number, "role": "pr_commit"})

            prs.append(
                {
                    "number": number,
                    "title": title or c["subject"],
                    "body": body.strip(),
                    "merge_style": style,
                    "merge_commit_sha": c["sha"],
                    "merged_at": c["committed_at"],
                    "head_ref": branch,
                    "author_name": author,
                    "commit_shas": [pc["sha"] for pc in pr_commits],
                    "files": _filter_patch(parse_unified_diff(diff)),
                    "source": "git",
                }
            )
        log.info("git: %d PRs, %d commits", len(prs), len(commits))
        return prs, list(commits.values())

    # ------------------------------------------------------------------ releases
    def collect_tags(self) -> list[dict]:
        out = self.git.run(
            "for-each-ref",
            "refs/tags",
            f"--format=%(refname:short){FIELD}%(objectname){FIELD}%(*objectname){FIELD}%(creatordate:iso-strict)",
        )
        tags = []
        for line in out.splitlines():
            name, obj, peeled, date = (line.split(FIELD) + ["", "", "", ""])[:4]
            if not RELEASE_TAG_RE.match(name):
                continue
            tags.append({"tag": name, "commit_sha": peeled or obj, "tagged_at": date})
        tags.sort(key=lambda t: (version_key(t["tag"]), t["tag"]))
        return tags

    def release_containment(self, tags: list[dict], shas: set[str]) -> list[dict]:
        """For each tag, which of ``shas`` are ancestors of (contained in) the tagged commit."""
        rows = []
        for t in tags:
            reachable = set(self.git.run("rev-list", t["commit_sha"]).split())
            for sha in sorted(shas & reachable):
                rows.append({"tag": t["tag"], "commit_sha": sha, "basis": "git_ancestry"})
        return rows

    def collect_changelog(self, ref: str) -> tuple[str | None, list[dict]]:
        for name in CHANGELOG_CANDIDATES:
            text = self.git.show_file(ref, name)
            if text:
                return name, parse_changelog(text)
        return None, []

    # ------------------------------------------------------------------ docs + code
    def collect_docs(self, ref: str) -> list[dict]:
        docs = []
        for path in self.git.ls_tree(ref):
            if not (path.startswith("docs/") or path in ("README.md", "README.rst")):
                continue
            if not path.endswith((".rst", ".md")):
                continue
            text = self.git.show_file(ref, path)
            if text:
                docs.append({"path": path, "revision": ref, "text": text})
        return docs

    def collect_code(self, ref: str, package_dirs: tuple[str, ...] | None = None) -> list[dict]:
        paths = self.git.ls_tree(ref)
        if package_dirs is None:
            package_dirs = tuple(
                d for d in {p.rsplit("/", 1)[0] for p in paths if p.endswith("/__init__.py")}
                if not d.startswith(("tests", "docs", "test")) and "/tests" not in d
            )
        out = []
        for path in paths:
            if path.endswith(".py") and any(path.startswith(d + "/") for d in package_dirs) and path.count("/") <= 3:
                text = self.git.show_file(ref, path)
                if text:
                    out.append({"path": path, "revision": ref, "text": text})
        return out


# ---------------------------------------------------------------------- parsers
def parse_changelog(text: str) -> list[dict]:
    """Parse ``2.27.0 (2022-01-03)`` style sections into release-note entries."""
    lines = text.splitlines()
    releases: list[dict] = []
    current: dict | None = None
    category = ""
    entry: list[str] = []

    def flush_entry() -> None:
        nonlocal entry
        if current is not None and entry:
            current["entries"].append({"category": category, "text": re.sub(r"\s+", " ", " ".join(entry)).strip()})
        entry = []

    for i, line in enumerate(lines):
        m = CHANGELOG_HEADER_RE.match(line.strip())
        if m and i + 1 < len(lines) and set(lines[i + 1].strip()) <= {"-", "="} and lines[i + 1].strip():
            flush_entry()
            current = {"version": m.group("ver"), "date": m.group("date"), "entries": []}
            releases.append(current)
            category = ""
            continue
        if current is None:
            continue
        stripped = line.strip()
        if re.fullmatch(r"\*\*(.+?)\*\*:?", stripped):
            flush_entry()
            category = stripped.strip("*: ")
        elif re.match(r"^[-*]\s+", stripped) and not set(stripped) <= {"-", "="}:
            flush_entry()
            entry = [re.sub(r"^[-*]\s+", "", stripped)]
        elif stripped and entry:
            entry.append(stripped)
        elif not stripped:
            flush_entry()
    flush_entry()
    return releases


def chunk_document(text: str, max_chars: int = 1500) -> list[dict]:
    """Split RST/Markdown by headings, then by size."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = [("", [])]
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_rst_heading = line.strip() and nxt.strip() and set(nxt.strip()) <= set("=-~^*#") and len(nxt.strip()) >= 3
        if is_rst_heading or line.startswith("#"):
            sections.append((line.strip("# ").strip(), []))
        if not (set(line.strip()) <= set("=-~^*#") and line.strip()):
            sections[-1][1].append(line)
    chunks = []
    for heading, body in sections:
        body_text = "\n".join(body).strip()
        if not body_text:
            continue
        for start in range(0, len(body_text), max_chars):
            chunks.append({"heading": heading, "text": body_text[start : start + max_chars]})
    return chunks


def chunk_python(text: str, max_chars: int = 4000) -> list[dict]:
    """One chunk per top-level function, and per method of each top-level class."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    chunks = []

    def add(symbol: str, node: ast.AST) -> None:
        start = node.lineno - 1
        if getattr(node, "decorator_list", None):
            start = min(d.lineno for d in node.decorator_list) - 1
        end = node.end_lineno or node.lineno
        chunks.append(
            {"symbol": symbol, "start_line": start + 1, "end_line": end, "text": "\n".join(lines[start:end])[:max_chars]}
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node)
        elif isinstance(node, ast.ClassDef):
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if not methods:
                add(node.name, node)
            for m in methods:
                add(f"{node.name}.{m.name}", m)
    return chunks
