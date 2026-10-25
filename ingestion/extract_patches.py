"""Split unified diffs into per-file patch hunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


@dataclass
class Hunk:
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    section: str  # text after the @@ ... @@ (often the enclosing function / class)
    lines: list[str] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(1 for line in self.lines if line.startswith("+"))

    @property
    def deletions(self) -> int:
        return sum(1 for line in self.lines if line.startswith("-"))

    @property
    def diff(self) -> str:
        return "\n".join([self.header, *self.lines])

    @property
    def added_text(self) -> str:
        return "\n".join(line[1:] for line in self.lines if line.startswith("+"))

    @property
    def removed_text(self) -> str:
        return "\n".join(line[1:] for line in self.lines if line.startswith("-"))


@dataclass
class FilePatch:
    path: str
    old_path: str | None
    status: str  # added | removed | modified | renamed
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False

    @property
    def additions(self) -> int:
        return sum(h.additions for h in self.hunks)

    @property
    def deletions(self) -> int:
        return sum(h.deletions for h in self.hunks)


def parse_hunks(patch: str) -> list[Hunk]:
    """Parse the hunks of a single-file patch (the format GitHub's `files[].patch` field uses)."""
    hunks: list[Hunk] = []
    current: Hunk | None = None
    for line in (patch or "").splitlines():
        m = HUNK_HEADER_RE.match(line)
        if m:
            current = Hunk(
                header=line,
                old_start=int(m.group(1)),
                old_lines=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_lines=int(m.group(4) or 1),
                section=m.group(5).strip(),
            )
            hunks.append(current)
        elif current is not None and (line[:1] in {" ", "+", "-", "\\"} or line == ""):
            current.lines.append(line)
    return hunks


def parse_unified_diff(diff: str) -> list[FilePatch]:
    """Parse a multi-file `git diff` / `git show` output."""
    files: list[FilePatch] = []
    current: FilePatch | None = None
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            current.hunks = parse_hunks("\n".join(body))
            files.append(current)

    for line in (diff or "").splitlines():
        m = DIFF_GIT_RE.match(line)
        if m:
            flush()
            body = []
            current = FilePatch(path=m.group(2), old_path=None, status="modified")
            if m.group(1) != m.group(2):
                current.old_path = m.group(1)
                current.status = "renamed"
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "removed"
        elif line.startswith("rename from "):
            current.old_path = line[len("rename from ") :]
            current.status = "renamed"
        elif line.startswith("Binary files"):
            current.binary = True
        elif line.startswith(("--- ", "+++ ", "index ", "similarity index", "rename to", "old mode", "new mode")):
            continue
        else:
            body.append(line)
    flush()
    return files
