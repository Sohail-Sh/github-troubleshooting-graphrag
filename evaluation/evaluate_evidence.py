"""Check that a returned evidence bundle has the *right* fix path: PR, changed file, release, evidence level."""

from __future__ import annotations

from typing import Any

from explanation.evidence_bundle import EvidenceBundle

LEVEL = {"verified": 3, "self": 2, "referenced": 2, "inferred": 1, "none": 0}


def check_evidence(bundle: EvidenceBundle, case: dict[str, Any]) -> dict[str, Any]:
    fx = bundle.primary_fix
    if bundle.status == "no_reliable_match" or fx is None:
        return {"fix_pr_correct": False, "files_correct": False, "release_correct": False, "evidence_level_ok": False, "fix_path_correct": False}
    pr_ok = fx.pr_number == case.get("expected_pr")
    paths = {f["path"] for f in fx.files}
    files_ok = all(p in paths for p in case.get("expected_files", []))
    release_ok = fx.earliest_release == case.get("expected_release")
    level_ok = LEVEL.get(fx.verification_status, 0) >= LEVEL.get(case.get("min_evidence", "referenced"), 2)
    # A fix path is "correct" when it names the right PR, the right code and the right release, and does not
    # overstate the evidence (an inferred link must never be shown as verified).
    return {
        "fix_pr_correct": pr_ok,
        "files_correct": files_ok,
        "release_correct": release_ok,
        "evidence_level_ok": level_ok,
        "evidence_level": fx.verification_status,
        "fix_path_correct": pr_ok and files_ok and release_ok and level_ok,
    }
