"""Turn a pasted error / traceback / symptom description into a structured query."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ingestion.extract_errors import GENERIC_EXCEPTIONS, extract_errors

STOPWORDS = set(
    """a an and are as at be but by can could did do does doing for from get gets getting got had has have how i if in
    into is it its it's me my no not of on or our please raise raised raises raising so some that the their them then
    there these they this such to too try trying use used using was we were what when where which while who why will with would
    you your error errors exception issue problem bug fails failed failing failure traceback recent call last file line
    most module none true false self return def""".split()
)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*[A-Za-z0-9_]|[A-Za-z]{2,}")


@dataclass
class ParsedQuery:
    raw: str
    exception_types: list[str] = field(default_factory=list)
    qualified_exceptions: list[str] = field(default_factory=list)
    message: str = ""
    file_paths: list[str] = field(default_factory=list)
    function_names: list[str] = field(default_factory=list)
    package_versions: dict[str, str] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    is_traceback: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def key_terms(self) -> list[str]:
        """Terms a matching historical report should contain; used for coverage and the no-match gate."""
        terms: list[str] = []
        for t in self.exception_types:
            if t not in GENERIC_EXCEPTIONS:
                terms.append(t.lower())
        for k in self.keywords:
            if k not in terms:
                terms.append(k)
        return terms[:10]


def parse_query(text: str, library_version: str | None = None, python_version: str | None = None) -> ParsedQuery:
    parsed = extract_errors(text)
    q = ParsedQuery(raw=text)
    q.exception_types = [t for t in parsed.exception_types]
    q.qualified_exceptions = list(dict.fromkeys(e.qualified_name for e in parsed.errors))
    messages = [e.message for e in parsed.errors if e.message]
    q.message = messages[-1] if messages else ""
    q.is_traceback = bool(parsed.frames)
    # Only frames inside the library are useful for matching (skip the user's own code and stdlib).
    for f in parsed.frames:
        path = f.normalized_path
        if path not in q.file_paths:
            q.file_paths.append(path)
        if f.function_name not in q.function_names and f.function_name != "<module>":
            q.function_names.append(f.function_name)
    q.package_versions = dict(parsed.package_versions)
    if library_version:
        q.package_versions["library"] = library_version
    if python_version:
        q.package_versions["python"] = python_version

    # Keywords: distinctive words of the error message (or of the whole text for prose descriptions).
    source = " ".join(messages) if messages else text
    if q.is_traceback and not messages:
        source = text.splitlines()[-1] if text.strip() else ""
    exc_lower = {t.lower() for t in q.exception_types}
    words = []
    for w in _WORD.findall(source):
        lw = w.lower().strip(".")
        if len(lw) < 3 or lw in STOPWORDS or lw in exc_lower or lw.isdigit():
            continue
        if lw not in words:
            words.append(lw)
    q.keywords = words[:12]
    return q


LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def lucene_escape(term: str) -> str:
    return LUCENE_SPECIAL.sub(r"\\\1", term)


def fulltext_query(q: ParsedQuery) -> str:
    """Build an OR query: exception names boosted, then message keywords, identifiers and frames."""
    parts: list[str] = []
    for t in q.exception_types:
        parts.append(f"{lucene_escape(t)}^3")
    for k in q.keywords:
        for piece in re.split(r"[._]", k):
            if len(piece) >= 3 and piece not in STOPWORDS:
                parts.append(lucene_escape(piece))
    for fn in q.function_names[-5:]:
        parts.append(f"{lucene_escape(fn)}^1.5")
    for path in q.file_paths[-3:]:
        base = path.rsplit("/", 1)[-1].removesuffix(".py")
        if base:
            parts.append(lucene_escape(base))
    if q.message:
        phrase = re.sub(r"[^\w\s]", " ", q.message).strip()
        if 2 <= len(phrase.split()) <= 12:
            parts.append(f'"{lucene_escape(phrase)}"^2')
    if not parts:
        words = [w for w in _WORD.findall(q.raw) if w.lower() not in STOPWORDS][:20]
        parts = [lucene_escape(w) for w in words]
    return " OR ".join(dict.fromkeys(parts)) or "*"


def embedding_text(q: ParsedQuery) -> str:
    """Text for the query embedding: the error lines rather than the full (noisy) traceback."""
    if not q.is_traceback:
        return q.raw.strip()[:2000]
    lines = [ln for ln in q.raw.splitlines() if ln.strip() and not ln.strip().startswith("File ")]
    tail = "\n".join(lines[-6:])
    return f"{tail}\nFunctions: {' '.join(q.function_names[-6:])}"
