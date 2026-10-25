"""Extract exception types, error messages and stack frames from free text (issues, tracebacks, queries)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Python-style exception class names, optionally dotted (requests.exceptions.InvalidURL).
EXCEPTION_RE = re.compile(
    r"\b((?:[a-z_][a-z0-9_]*\.)*([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning|Exit|Interrupt|Timeout)))\b"
)
# "SomeError: message" at the start of a line (the last line of a traceback) or inline.
EXCEPTION_LINE_RE = re.compile(
    r"(?:^|\s|\()((?:[a-z_][a-z0-9_]*\.)*[A-Z][A-Za-z0-9]*(?:Error|Exception|Warning|Timeout))(?::[ \t]*([^\n]*))?",
    re.MULTILINE,
)
FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<func>[\w<>.]+)')
VERSION_RE = re.compile(
    r"\b(?P<pkg>requests|urllib3|python|idna|chardet|charset[-_]normalizer|certifi)\s*(?:==|version|v)?\s*"
    r"(?P<ver>\d+\.\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Exception-ish words that are too generic to be useful identity signals on their own.
GENERIC_EXCEPTIONS = {"Exception", "Error", "Warning", "BaseException"}

_QUOTED = re.compile(r"(['\"`])(.*?)\1")
_URL = re.compile(r"https?://\S+")
_HEX = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_NUM = re.compile(r"\b\d+\b")
_WS = re.compile(r"\s+")


@dataclass
class StackFrame:
    file_path: str
    line_number: int
    function_name: str

    @property
    def normalized_path(self) -> str:
        """Path relative to the package: '/usr/lib/python3/site-packages/requests/models.py' -> 'requests/models.py'."""
        p = self.file_path.replace("\\", "/")
        for marker in ("site-packages/", "dist-packages/", "/src/", "/lib/python"):
            if marker in p:
                p = p.split(marker)[-1]
        if p.startswith("python") and "/" in p:  # lib/pythonX.Y/foo.py
            p = p.split("/", 1)[1]
        return p.lstrip("/")


@dataclass
class ErrorMention:
    exception_type: str  # short class name, e.g. "UnicodeError"
    qualified_name: str  # as written, e.g. "requests.exceptions.InvalidURL"
    message: str  # raw message after the colon (may be empty)
    message_pattern: str  # normalized message used for identity


@dataclass
class ParsedErrorText:
    raw: str
    errors: list[ErrorMention] = field(default_factory=list)
    frames: list[StackFrame] = field(default_factory=list)
    package_versions: dict[str, str] = field(default_factory=dict)

    @property
    def exception_types(self) -> list[str]:
        seen: list[str] = []
        for e in self.errors:
            if e.exception_type not in seen:
                seen.append(e.exception_type)
        return seen


def normalize_message(message: str) -> str:
    """Strip volatile details (URLs, numbers, addresses) so the same error groups together."""
    m = message.strip()
    m = _URL.sub("<url>", m)
    m = _HEX.sub("<hex>", m)
    m = _NUM.sub("<n>", m)
    m = m.replace("'", "").replace('"', "").replace("`", "")
    m = _WS.sub(" ", m).strip(" .")
    return m.lower()[:200]


def extract_errors(text: str) -> ParsedErrorText:
    text = text or ""
    result = ParsedErrorText(raw=text)
    seen: set[tuple[str, str]] = set()

    for m in EXCEPTION_LINE_RE.finditer(text):
        qualified = m.group(1)
        short = qualified.split(".")[-1]
        message = (m.group(2) or "").strip()
        # A message that is itself only code punctuation isn't useful.
        if len(message) > 300:
            message = message[:300]
        key = (short, normalize_message(message))
        if key in seen:
            continue
        seen.add(key)
        result.errors.append(ErrorMention(short, qualified, message, key[1]))

    # Bare mentions without a colon ("raises UnicodeError") are already captured above when preceded by
    # whitespace; capture any remaining dotted/inline forms.
    for m in EXCEPTION_RE.finditer(text):
        short = m.group(2)
        if not any(e.exception_type == short for e in result.errors):
            result.errors.append(ErrorMention(short, m.group(1), "", ""))

    for m in FRAME_RE.finditer(text):
        result.frames.append(StackFrame(m.group("path"), int(m.group("line")), m.group("func")))

    for m in VERSION_RE.finditer(text):
        pkg = m.group("pkg").lower().replace("_", "-")
        result.package_versions.setdefault(pkg, m.group("ver"))

    return result


def exception_types_in_code(diff_text: str) -> list[str]:
    """Exception class names that appear in `raise`/`except` statements of a patch."""
    found: list[str] = []
    for m in re.finditer(r"\b(?:raise|except)\s+\(?\s*((?:[\w.]+\s*,\s*)*[\w.]+)", diff_text):
        for dotted in m.group(1).split(","):
            name = dotted.strip().split(".")[-1]
            if re.fullmatch(r"[A-Z][A-Za-z0-9]+", name) and name not in found:
                found.append(name)
    return found
