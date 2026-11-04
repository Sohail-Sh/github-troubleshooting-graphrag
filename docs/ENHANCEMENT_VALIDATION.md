# Enhancement validation report

Implements `Neo4j_NODES2026_Enhancement_Requirements_for_Claude.md`. Run on 2026-11-04 against a real,
locally-running Neo4j 5.26.32 Community instance (`docker compose up -d neo4j`) loaded with the committed
`psf-requests-v2.32.3` **git-only** snapshot and the real `BAAI/bge-small-en-v1.5` embedder (not the hashing
fallback). Python 3.14.0 (project documents 3.12/3.13 as tested; no compatibility issue found).

## Commands run and results

```
python -m compileall -q app.py ui graph retrieval explanation tests   → PASS (no output = success)
python -m pytest -q                                                   → PASS: 64 passed (44 pre-existing + 20 new)
make demo-check                                                       → PASS: 22/22 integration+app tests, evaluation re-run (see below)
```

No test was skipped. `tests/conftest.py`'s `requires_neo4j` marker gates integration/UI tests on a live,
snapshot-loaded Neo4j; since Neo4j was up throughout, every one of those 22 tests executed for real against
the database, not a mock.

### New tests added (20)

- `tests/test_related_fixes_unit.py` (13 tests, no Neo4j needed): read-only/parameterized-query static check,
  exception-reason dedup/classification, missing-property safety, busy-file decay, sweeping-PR discount,
  shared-function same-file+generic-name filtering, limit enforcement, deterministic tie order, zero-score
  exclusion, relative-score bounds, and a fake-store test confirming the source PR can never appear in its
  own related-fix results.
- `tests/test_integration_neo4j.py` (+7 tests, live Neo4j): related PR ids are real `PullRequest` nodes in the
  loaded graph; the top result for PR #5414 is PR #3789 (see below); every claimed exception-sharing reason is
  re-derived with an **independent** query (`RELATED_FIXES_VERIFY_PATH`, not the service's own traversal
  logic); results are deterministic and deduplicated across repeated calls; the feature writes zero new
  `CLOSES`/`REFERENCES`/`POSSIBLY_ADDRESSES` edges; the source PR is excluded; the original #5367 → #5414 →
  v2.27.0 milestone path is byte-for-byte unchanged by adding the feature.
- `tests/test_app.py`: updated the tab-label assertion to include `"Related Fixes"` (was already asserting
  the full tab list, so this was a required, not optional, change).
- `tests/test_explanation.py`: the `PR` test fixture was missing the `id` field that the real Cypher always
  returns; added it after `FixEvidence.pr_id` became a required field (see "Files modified").

## Answers to section 14 (resolved from source, not assumed)

1. **Does `TOUCHES_EXCEPTION.in_changed_lines` exist and is it populated?** Yes - set in
   `ingestion/normalize.py:300` as `name in changed` where `changed` is the joined +/- diff lines. Live count:
   340/488 edges (70%) are `true`. **Important finding**: PR #5414's own two `TOUCHES_EXCEPTION` edges
   (`InvalidURL`, `UnicodeError`) are both `false` - the exception names appear in the hunk but not inside a
   `+`/`-` line specifically. A strict "changed lines on both sides" query (the one sketched in the
   requirements doc, section 4.3) returns **zero** rows for the headline demo PR. `retrieval/related_fixes.py`
   therefore always runs the lenient query and classifies each match as `changed-line match` or the weaker
   `hunk mention only`, rather than assuming the strict form has coverage.
2. **Does `PatchHunk.section` contain meaningful function-level data?** No, not reliably. Live sample of the
   15 most common values: 335 hunks have `section == ""`; the rest are mostly class-level headers (e.g.
   `class PreparedRequest(RequestEncodingMixin, RequestHooksMixin):`, `class TestRequests:`), not function
   names. Shared-function evidence is therefore parsed from the **diff body** (`def (\w+)` regex, same pattern
   already used in `ingestion/normalize.py:390` for inferred-frame linking) rather than trusted from `section`.
3. **Does the snapshot contain real same-exception related PRs for #5414?** Yes. Using the lenient
   (hunk-mention) exception signal, 10 other PRs share `InvalidURL` and/or `UnicodeError`:
   #3789, #4004, #4067, #4765, #6017, #6095, #6655, #6662, #6710, #6716. The top-ranked one after all scoring
   (including sweeping-PR and busy-file dampening) is **PR #3789 "Lazy load idna library"**, which shares
   *both* exception types and is independently documented as a real code-history ambiguity in
   `docs/EVALUATION.md`'s own caveats section ("misses are cases where two real fixes touch the same code -
   #3780 'lazy load idna' vs #5367"). This is not a seeded or cherry-picked example; it is what the traversal
   returns unprompted, and `test_related_fixes_top_result_is_the_known_lazy_load_idna_pr` pins it.
4. **File/PR fields; can cross-snapshot relationships exist?** `File.path` is a plain string; `PullRequest.id`
   is scoped as `repo:<owner>/<name>:pr:<number>` (see `ingestion/ids.py`). `graph/ingest_neo4j.py` loads with
   `--reset`, which clears the database before loading - this app's data model is one Neo4j database per
   loaded snapshot, so cross-snapshot PR mixing is not structurally possible in the current architecture. The
   related-fix queries do not add an explicit snapshot filter because none of the existing queries in
   `graph/queries.py` do either (same assumption throughout the codebase).
5. **Does the evidence bundle expose everything needed for structural edges?** No - `FixEvidence` had no
   field for the PR's own graph id, and nothing surfaced `MERGED_AS`/`CONTAINS_COMMIT` provenance
   (`basis`/`verification_status`/`source_url`/`retrieved_at`/`extractor_version`) anywhere in the UI, even
   though the graph has it (confirmed live - see `EVIDENCE_EDGES_FOR_PR` sample output below). Added
   `FixEvidence.pr_id` (additive field, populated from the Cypher `pr.id` the queries already returned) and
   one new read-only query, `graph.queries.EVIDENCE_EDGES_FOR_PR`.
6. **Neo4j version and Cypher compatibility?** `5.26.32` Community (shown in the app's own sidebar: "Neo4j
   5.26.32 at bolt://localhost:7687"). All new Cypher (`OPTIONAL MATCH`, map projections, `UNWIND`, pattern
   comprehensions `[(f)<-[:CHANGES]-(p) | p]`, list comprehensions with `CASE`) is standard Neo4j 5.x syntax
   and was run against this exact version, not assumed compatible.
7. **Can an interactive graph component run fully offline?** Not attempted - P0 explicitly only requires the
   selectable provenance inspector (delivered as the "Evidence edges" table/expander under Fix Graph), and the
   spec marks true click-on-edge interactivity P1/optional. No new JS dependency or CDN asset was added.
8. **Are tests skipped? Which ran for real?** None skipped. All 22 `requires_neo4j`-marked tests
   (`test_integration_neo4j.py` + `test_app.py`) executed against the live, snapshot-loaded database.
9. **Do graph relationships and version advice stay correct after the selected candidate changes?** Verified
   live in the browser, not just unit-tested: switching "Inspect candidate" from the #5367/#5414 pair to PR
   #4978 ("Fix double image request", a SELF-linked doc PR with no issue) correctly changed the Related Fixes
   anchor to "Exploring PR #4978", changed the available filter to "Shared file" only (it has no exception
   evidence), and returned a different top candidate (PR #4781) - with no stale data from the previous anchor.
10. **Are slide claims aligned with the final loaded snapshot?** No slide deck was supplied to check against;
    section "Slide/script wording that must change" below lists what a slide author needs to know.

## Evaluation re-run (honest disclosure, not score tuning)

`make demo-check` runs `python -m evaluation.evaluate_retrieval`, which writes `evaluation/results/latest.json`
as a side effect of the command - this is pre-existing behavior of that script, not something added for this
feature. The committed `latest.json` was already stale: it recorded `embedding_model: "hashing-ngrams-v1"`
from a run on a different machine (`cases_file` pointed at `/home/user/...`), while the Neo4j instance actually
running right now is loaded with real `BAAI/bge-small-en-v1.5` embeddings (`make data`'s default). Re-running
made `latest.json` match what is actually loaded, which is what the Evaluation tab in the app reads and
displays - leaving it stale would have been the more misleading option.

| Mode | Hit@1 (old hashing / new BGE) | Correct fix path (old / new) | No-match rejection (old / new) |
|---|---|---|---|
| Keyword | 0.353 / 0.353 | 0.353 / 0.353 | 100% / 83% |
| Vector | 0.529 / 0.471 | 0.529 / 0.412 | 100% / 100% |
| Hybrid | 0.588 / 0.588 | 0.588 / 0.529 | 100% / 83% |
| Hybrid + graph | 0.765 / **0.824** | 0.706 / **0.765** | 100% / 100% |

This is reported in full, including the metrics that got *worse* (keyword/hybrid no-match rejection dropped
from 100% to 83% - one negative control now ranks a weak candidate instead of being cleanly rejected; vector
hit@1 dropped slightly), specifically so this isn't mistaken for cherry-picked improvement. Hybrid+graph
improved because real semantic embeddings replace lexical hashing, as the README already anticipated
("Re-run after `make collect` with `EMBEDDER=fastembed`; those are the numbers to present"). **The 17/6
positive/negative case count, the git-only snapshot's evidence-level caveats, and `docs/EVALUATION.md`'s match
gate description are unchanged** - nothing about the benchmark methodology was altered, only the embedder a
pre-existing script was run with.

If you want the previously-committed (and already machine-mismatched) `latest.json` back:
`git checkout -- evaluation/results/latest.json`. The newly-written
`evaluation/results/retrieval_psf-requests-v2.32.3_BAAI_bge-small-en-v1.5.json` is a new file, not a
modification of an existing one.

## Live evidence-edge sample (PR #5414, used for AC-05)

```
PR #5414 -[REFERENCES]-> Issue #5367   | referenced | branch_name_number, commit_message_text_mention
PR #5414 -[MERGED_AS]->  Commit d0965999 | verified | merge_commit_record
Release v2.27.0 (earliest) -[CONTAINS_COMMIT]-> Commit d0965999 | verified | git_tag_ancestry
Release v2.27.1 -[CONTAINS_COMMIT]-> Commit d0965999 | verified | git_tag_ancestry
PR #5414 -[CHANGES]-> requests/models.py | structural (git diff) | Not available in this snapshot
PR #5414 -[CHANGES]-> tests/test_requests.py | structural (git diff) | Not available in this snapshot
```

`CHANGES` correctly reports "Not available in this snapshot" for basis/source/retrieved/extractor rather than
inventing them - this schema records no provenance properties on that edge type.

## Performance (section 9)

`find_related_fixes` against PR #5414, 5 warm runs: **15.4-31.6 ms** (goal was "aim for under 2s... as a
performance goal, not a claim"; well under it on this snapshot's scale - 368 PRs, 488 `TOUCHES_EXCEPTION`
edges, max per-exception fan-out 20 PRs for `ImportError`). `PROFILE` on `RELATED_FIXES_BY_EXCEPTION` was run
and returned 13 rows with no cardinality red flags; no new index was added because none was needed at this
scale (`EXPLAIN`/`PROFILE` showed the existing `TOUCHES_EXCEPTION`/`HAS_PATCH` traversal is cheap - the
snapshot is small enough that index-backed lookups on `PullRequest.id`/`.number` already dominate).

## Trust-boundary invariants (section 7) - how each was checked

| Invariant | How verified |
|---|---|
| No related PR enters `EvidenceBundle.fixes` | `related_fixes.py` returns a separate `RelatedFix` list; `ui/investigation_view.py` renders it in its own tab, never merged into `bundle.fixes` or passed to `generate_answer` |
| No `CLOSES`/`REFERENCES`/`POSSIBLY_ADDRESSES` written or upgraded | `test_related_fixes_writes_no_new_issue_links` asserts the count is identical before/after a call; all `RELATED_FIXES_*` Cypher is read-only (statically checked by `test_related_fixes_queries_are_read_only_and_parameterized`, which scans for `CREATE`/`MERGE`/`DELETE`/`SET`/`REMOVE`/`DETACH` as whole words outside comments) |
| No-match behavior unchanged | `test_unrelated_error_is_rejected` (pre-existing) still passes in all 4 modes; verified live that the "Unrelated error" sample shows the neutral "Related exploration requires a supported anchor" message, not fabricated suggestions |
| Doesn't bypass the evidence gate or change ranking | `retrieval/rerank.py` was not modified (`git diff` confirms); Related Fix Discovery only runs *after* a primary fix is already chosen, and reads `bundle.primary_fix.pr_id`, never anchor candidates directly |
| Untrusted GitHub strings treated as data | PR titles/URLs are inserted into Streamlit markdown/links the same way the pre-existing code already does (e.g. `fx.pr_title` in `_render_result`); no new `unsafe_allow_html` was introduced |
| All reads parameterized | Every `RELATED_FIXES_*`/`EVIDENCE_EDGES_FOR_PR` query uses `$param` placeholders exclusively; no f-string/format interpolation of PR titles, exception names, or file paths into Cypher text anywhere in `retrieval/related_fixes.py` or `graph/queries.py` |
| `verified` ≠ "patch fixes your error" | `EDGE_EXPLAINS` strings in `ui/graph_view.py` state explicitly what each edge type does and does not prove; `RelatedFix` reasons always say "candidate PR... NOT a verified fix for this issue" |

## Known remaining limitations

- **"Investigate separately" doesn't update the visible text box.** Verified live: clicking it runs a real new
  investigation (new banner, new Related Fixes anchor, new candidates - confirmed by investigating PR #3789
  this way and getting a genuinely different result, PR #5797, with its own `changed-line match: ImportError`
  reasoning) but the "Error message, stack trace or symptom" textarea keeps showing the previous query's text.
  This is not an oversight: `ui/investigation_view.py` only ever sets `st.session_state["query_text"]` from the
  sample buttons, which run *before* `st.text_area(...)` is instantiated in the same script pass (line 73 vs.
  75); Streamlit raises `StreamlitAPIException` if a widget-bound session-state key is set anywhere after that
  widget has already been drawn in the same run, so the "Investigate separately" button - which lives inside a
  tab below the form - cannot safely do the same thing. The investigation itself is correct; only the textarea
  display lags behind. Worth fixing properly (e.g. a dedicated "now showing: <title>" caption) if this goes
  past the conference demo.

- Shared-function matching only runs over files already known to be shared (bounded by design); a related PR
  that shares a function in a file the source PR did *not* touch would not be found. This is a deliberate
  scope limit to avoid a whole-graph hunk scan, not an oversight.
- "Investigate PR #N separately" (P1-C) re-runs a full new investigation using the related PR's own title as
  free text, reusing the existing tested `_run()`/candidate-selection code path (the same mechanism "Inspect
  this candidate" already used). It does not construct an investigation anchored directly on an arbitrary PR
  id, because `Investigator.investigate` takes free text, not a PR id, and changing that was out of scope
  ("keep core behavior stable").
- No Graph Data Science Node Similarity (P2) was attempted - out of scope unless P0/P1 were stable with time
  remaining, and the simple traversal already produces a real, explainable top result.
