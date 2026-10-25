"""Reject generated explanations that cite or name anything the evidence bundle does not contain."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .evidence_bundle import EvidenceBundle

if TYPE_CHECKING:  # pragma: no cover
    from .answer_generator import Answer

NUMBER_REF = re.compile(r"(?:#|\b(?:issue|pr|pull request)\s+#?)(\d{2,6})\b", re.IGNORECASE)
VERSION = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
OVERCLAIMS = [
    r"\b(i|we) (have )?(tested|ran|executed|verified that the patch)\b",
    r"\bguarantee[sd]?\b",
    r"\bwill (definitely|certainly) fix\b",
    r"\b\d{1,3}\s?% (chance|probability|likely)\b",
]
CLAIM_SECTIONS_REQUIRING_CITATIONS = {"Likely historical issue", "Root cause", "Historical fix", "Changed code", "Release information"}


def _allowed(bundle: EvidenceBundle) -> tuple[set[int], set[str], set[str]]:
    numbers: set[int] = set()
    versions: set[str] = set()
    shas: set[str] = set()
    if bundle.issue:
        numbers.add(int(bundle.issue["number"]))
    for fx in bundle.fixes:
        numbers.add(fx.pr_number)
        for r in fx.releases:
            versions.add(r["tag"].lstrip("v"))
        for n in fx.release_notes:
            versions.add(n["version"])
        if fx.merge_commit:
            shas.add(fx.merge_commit["sha"])
    for item in bundle.items.values():
        for m in NUMBER_REF.finditer(item.text + " " + item.label):
            numbers.add(int(m.group(1)))
        for m in VERSION.finditer(item.text + " " + item.label):
            versions.add(m.group(1))
        for m in SHA.finditer(item.text):
            shas.add(m.group(0))
    for v in (bundle.query.get("package_versions") or {}).values():
        versions.add(str(v).lstrip("v"))
    for m in VERSION.finditer(bundle.query.get("raw", "")):
        versions.add(m.group(1))
    return numbers, versions, shas


def validate_answer(answer: "Answer", bundle: EvidenceBundle) -> list[str]:
    problems: list[str] = []
    numbers, versions, shas = _allowed(bundle)
    for s in answer.sections:
        for c in s.citations:
            if c not in bundle.items:
                problems.append(f"{s.heading}: cites unknown evidence {c}")
        if s.heading in CLAIM_SECTIONS_REQUIRING_CITATIONS and s.text.strip() and not s.citations:
            problems.append(f"{s.heading}: no citation")
        text = s.text
        for m in NUMBER_REF.finditer(text):
            if int(m.group(1)) not in numbers:
                problems.append(f"{s.heading}: mentions #{m.group(1)} which is not in the evidence")
        for m in VERSION.finditer(text):
            if m.group(1) not in versions:
                problems.append(f"{s.heading}: mentions version {m.group(1)} which is not in the evidence")
        for m in SHA.finditer(text.lower()):
            if not any(full.startswith(m.group(0)) for full in shas):
                problems.append(f"{s.heading}: mentions commit {m.group(0)} which is not in the evidence")
        for pat in OVERCLAIMS:
            if re.search(pat, text, re.IGNORECASE):
                problems.append(f"{s.heading}: overclaims ({pat})")
        fx = bundle.primary_fix
        if fx and fx.verification_status != "verified" and re.search(r"\b(verified|confirmed) (link|fix|closing)\b", text, re.IGNORECASE):
            problems.append(f"{s.heading}: describes a {fx.verification_status} link as verified")
    return problems
