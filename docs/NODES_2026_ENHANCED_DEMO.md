# NODES 2026 enhanced demo (5 minutes)

Builds on `docs/DEMO_SCRIPT.md` (the full 30-minute, two-speaker talk). This is the tight version that adds
Related Fix Discovery, the evidence-edges inspector, and the version-applicability showcase described in
`Neo4j_NODES2026_Enhancement_Requirements_for_Claude.md`. See `docs/ENHANCEMENT_VALIDATION.md` for how every
claim below was checked against the live graph.

## Before the talk

```bash
docker compose up -d neo4j      # or: make neo4j
make data                       # normalizes + loads the committed snapshot with real BGE embeddings
python -m compileall -q app.py ui graph retrieval explanation tests
python -m pytest -q             # 64 passed, 0 skipped, as of this feature
make demo-check                 # 22 live integration/app tests + evaluation re-run
streamlit run app.py            # http://localhost:8501
```

Nothing in the five-minute sequence below calls GitHub, Claude, or any CDN - it is safe with Wi-Fi off, same
as the original demo. Keep **LLM explanation** toggled off in the sidebar unless you specifically want to show
the Claude-generated explanation path; the deterministic explanation is what every example below was written
against.

## Five-minute sequence

| Time | Action | What the audience should learn |
|---|---|---|
| 0:00-1:05 | Paste the **Full traceback** sample, set *Library version* `2.26.0`, mode **Hybrid + graph re-ranking**, click **Investigate** | The system finds a grounded historical fix: Issue #5367 → PR #5414 → `requests/models.py` → v2.27.0, with the **Version applicability** banner right under the match metrics already telling you "predates v2.27.0; upgrade" |
| 1:05-2:00 | Open **Fix Graph**, then expand **Evidence edges - why does each arrow exist?** below the chart | The graph isn't just connected dots: every edge's strength, basis and source link is shown, and `CHANGES` honestly says "Not available in this snapshot" for provenance instead of pretending it has verification data it doesn't |
| 2:00-3:15 | Open **Related Fixes** | Graph traversal surfaces **PR #3789 "Lazy load idna library"** as the top lead (100/100 relatedness) - a real PR that touches the *same two exception types* in the *same file*, clearly labeled "Related candidate - NOT a verified fix for this issue." This is the real ambiguity `docs/EVALUATION.md` already documents, not a staged example |
| 3:15-4:15 | Click **Show for installed 2.31.0** (next to the version-applicability banner) | Same query, same anchor PR, one click - the banner flips to "this historical fix is already present; investigate another cause or version/environment difference," reusing the exact same engine logic, not a hardcoded string |
| 4:15-5:00 | Click **Unrelated error** sample, **Investigate** | `No sufficiently supported historical fix found`, and the **Related Fixes** tab shows "Related exploration requires a supported anchor" rather than inventing a related PR for a report that was never matched |

## Expected output (what you should actually see - not aspirational)

**Related Fixes tab for PR #5414**, "All" filter, in order:

1. **PR #3789** "3780: Lazy load idna library" - relatedness 100/100, earliest release v2.13.0. Reasons:
   `hunk mention only (not confirmed on changed lines): InvalidURL, UnicodeError`, `weak relationship: same
   changed file, but touched by many PRs (requests/models.py)`, `corroborated by more than one independent
   signal`.
2. **PR #6017** "Fix erroneous use of 'schema' instead of 'scheme'" - relatedness 67/100.
3. **PR #4765** "wrap url parsing exceptions from urllib3's PoolManager" - relatedness 67/100.
4. **PR #6655** "Use TLS settings in selecting connection pool" - relatedness 67/100.
5. Four more, down to PR #6662 at 23/100 (discounted: it touches 30 files, flagged as a sweeping change).

If a `make collect` (API-enriched) snapshot is loaded instead of the committed git-only one, exact scores and
the candidate set may shift slightly (issue text becomes available, more PRs may link to real issues) - **that
is expected**; re-check this table against a live run before presenting, don't assume it's frozen.

**Evidence edges for PR #5414** (Fix Graph tab): 9 rows - the `REFERENCES` link to #5367 (`referenced`,
*not* verified), `MERGED_AS` to commit `d0965999` (`verified`), five `CONTAINS_COMMIT` releases starting with
v2.27.0 (earliest, `verified`), and two `CHANGES` rows (`requests/models.py`, `tests/test_requests.py`) marked
`structural (git diff)` with provenance columns reading "Not available in this snapshot."

## Fallback screenshots to capture in advance

Take these with Neo4j freshly loaded and Wi-Fi off, in this exact order, so they can replace any live step:

1. Troubleshoot view immediately after Investigate (2.26.0) - banner + Version applicability callout visible.
2. Fix Graph tab with the Evidence edges expander open.
3. Related Fixes tab, "All" filter, PR #3789 card expanded (click "Expand evidence" first).
4. Troubleshoot view after clicking "Show for installed 2.31.0" - banner should read "already present."
5. Unrelated-error no-match state, Related Fixes tab showing the neutral anchor message.

## Preflight checklist

- [ ] `docker compose ps neo4j` shows `healthy`; Repository view shows the snapshot id, source (`git`), and
      `embeddings: BAAI/bge-small-en-v1.5` - **not** `hashing-ngrams-v1** (if it says hashing, re-run `make
      data` with internet access before going further offline).
- [ ] `python -m pytest -q` passes with 0 skipped (skips mean Neo4j wasn't reachable when you ran it).
- [ ] `make demo-check` passes.
- [ ] Confirm live (not from memory) that PR #5414's REFERENCES link to #5367 still shows `referenced`, not
      `verified` - if you later load an API-collected snapshot, re-check whether it's actually a verified
      `CLOSES` edge now and update the script wording if so (see accuracy notes below).
- [ ] Confirm live which real PR is the top Related Fixes result for #5414 - do not assume it's still #3789
      if the snapshot changed (different collection window, API data added, etc.).
- [ ] LLM toggle off; deterministic explanation is what the script above was written against.
- [ ] Browser zoom at 100%, window at a size you'll actually present at (1366x768 was the target in the
      original script) - re-check that "Evidence edges" table and Related Fixes cards don't require horizontal
      scrolling at that size.
- [ ] Rollback path: current branch/commit noted, known-good screenshots from the list above saved to the
      slide deck.

## Accuracy notes for slide authors (do not state these without checking the live graph first)

- The git-only snapshot's #5367→#5414 link is `REFERENCES` (text mention), not GitHub-verified `CLOSES`. Only
  an API-collected snapshot (`make collect` with `GITHUB_TOKEN`) can have a verified `CLOSES` edge here, and
  even then only if GitHub's own closing-reference record says so.
- "Related Fixes" discovery scores are **graph-traversal heuristics bounded 0-100 within one result set**, not
  probabilities and not evidence that any related PR fixes the user's bug. Never say "the graph found 3 other
  fixes for this bug" - say "3 other PRs that touched related code, as leads."
- PR #3789's relevance is *mention-level* evidence (`hunk mention only`), not a confirmed changed-line match -
  say "shares the same exception types in the same file," not "confirmed to touch the same code path."
- The evaluation numbers now on screen were re-run with the **real BAAI/bge-small-en-v1.5 embedder**, not the
  hashing fallback the original committed baseline used - see `docs/ENHANCEMENT_VALIDATION.md` for the exact
  before/after table, including the metrics that got slightly worse (don't cherry-pick only the improved
  ones).
- Keep the existing accuracy notes from `docs/DEMO_SCRIPT.md`: 17 positive / 6 negative cases, no
  cross-repository generalization claim, and no "LLM cannot hallucinate" / "Neo4j is the only database that
  can do this" framing.
- Suggested takeaway line (unchanged from the requirements doc): **"We don't just retrieve a fix. We can
  inspect its evidence, explore related history, test applicability, and refuse unsupported conclusions."**

## If something breaks on stage

Same fallbacks as `docs/DEMO_SCRIPT.md`: Neo4j down → `docker compose up -d neo4j` then switch to the
recording; LLM unavailable → deterministic explanation kicks in automatically; Related Fixes query failure →
the tab shows "Related Fix Discovery failed (...). The Fix Graph and Summary above are unaffected" and the
rest of the investigation stays fully usable - this was a deliberate design requirement (P0-D), not a gap.
