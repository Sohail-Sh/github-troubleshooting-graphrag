# Evaluation notes

## What is measured

| Metric | Meaning |
|---|---|
| Hit@1 / Hit@5 | expected report ranked 1st / in the top 5. A hit is the expected issue, or the expected PR when the system anchors on the PR |
| MRR@5 | mean reciprocal rank, cut at 5 |
| Correct fix path | the answer names the expected PR, the expected source file, the expected earliest release, and does not overstate the link (an inferred link is never shown as verified). Measured on the returned evidence bundle, not on the ranking |
| No-match rejection | share of negative controls answered with "No sufficiently supported historical fix found" |
| False rejections | positives the gate rejected |
| Latency | wall clock per investigation (retrieval + re-ranking + graph expansion), excluding explanation generation |

Ranking and fix-path correctness are kept apart on purpose: graph expansion can make the evidence complete
without changing which report ranks first, and vice versa.

## The match gate

A candidate is a *strong match* when its exception type is not contradicted and either

* `match_support = term_coverage + 0.15·exception_match + 0.10·frame_match ≥ MIN_TERM_COVERAGE` (0.5), or
* Neo4j-normalized cosine `≥ MIN_VECTOR_SIMILARITY` (0.80) and `term_coverage ≥ 0.3`.

`term_coverage` is the share of key query terms (exception types + distinctive message words) that appear in
the candidate's retrieved evidence. The same gate runs in every mode so rejection rates are comparable.
Neo4j reports cosine as `(1 + cos) / 2`, so 0.80 corresponds to cos ≈ 0.6. **Re-check this threshold
after switching to BGE embeddings**: the hashing embedder's similarity distribution is different.

## Graph re-ranking (hybrid + graph mode)

`0.40·text + 0.20·coverage + 0.15·exception + 0.10·frames + 0.15·evidence`, minus 0.15 when the user's version
already contains the fix and 0.10 when the exception type is contradicted. Graph-derived inputs:

* exception types touched by the fixing patch (`(:PatchHunk)-[:TOUCHES_EXCEPTION]->(:ExceptionType)`) and
  mentioned in the report;
* stack-frame files/functions vs. files changed by the fix (`CHANGES`) and hunk sections;
* evidence strength of the issue→PR link (verified 1.0, referenced 0.7, inferred 0.35), +0.1 when the
  fix appears in release notes (`(:ChangelogEntry)-[:MENTIONS]->(:PullRequest)`);
* sweeping PRs: hits inside PRs that change many files (release prep, vendoring, reformatting) are
  down-weighted by `1 / (1 + log2(files/8))`, and such a PR is weak fix evidence.

The scores are retrieval scores, not probabilities that a fix applies, and the UI never presents them as such.

## Caveats of the committed results

1. **Git-only snapshot.** No issue titles, bodies or comments; the only issue→PR links are textual
   (`referenced`) or heuristic (`inferred`). Several misses are cases where two real fixes touch the same code
   (e.g. #3780 "lazy load idna" vs #5367) and only the issue text could separate them.
2. **Hashing embedder.** Lexical feature hashing, used because the sandbox could not download the BGE model.
   "Vector" results are therefore not semantic search.
3. **Authored queries.** The 17 positive queries describe each symptom the way a user might; they were
   written for this benchmark from the fix commits and release notes. Once the API snapshot has issue text,
   add cases whose query is the traceback from the original report (`"query_source": "issue_report"`).
4. **Small n.** 17 positives / 6 negatives: report the per-case table alongside the aggregates.

## Re-running for the talk

```bash
make collect SINCE=2019-01-01      # with GITHUB_TOKEN
make data EMBEDDER=fastembed
make evaluate                      # writes evaluation/results/latest.json (shown in the Evaluation view)
```
