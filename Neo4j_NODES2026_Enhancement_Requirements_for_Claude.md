# NODES 2026 Demo Enhancement: Evidence Explorer, Related Fix Discovery, and Version-Aware Investigation

**Implementation requirements and execution instructions for Claude Code**  
**Project:** `github-troubleshooting-graphrag-main`  
**Conference:** Neo4j NODES 2026, November 12, 2026  
**Status:** Proposed change specification. The functions and UI described below do **not** all exist yet.  
**Priority:** A working, reproducible five-minute conference demo, not a production-scale rewrite.

---

## 0. Instructions to the coding agent

You have access to an existing Python/Streamlit/Neo4j repository. **Read its actual source and tests before making changes.** Implement the smallest safe additive changes that meet the acceptance criteria. Do not replace working retrieval, ingestion, evidence validation, or version logic without a demonstrated bug. Do not invent results, GitHub links, benchmark improvements, or evidence. When this document references a candidate function or property, confirm it in the code and adapt the implementation to the actual schema.

**Workflow:** (1) inspect repo and establish baseline; (2) make a short implementation plan and identify dependencies; (3) implement Phase 1 and test; (4) implement Phase 2 and test; (5) implement Phase 3 and test; (6) provide exact demo steps and a factual change log. If you cannot run Neo4j or a test, say **NOT RUN** with the missing prerequisite. Never report a skipped integration test as passed. If time is limited, finish the P0 items first and leave P1/P2 clearly marked.

### Required output from Claude

1. Modified project source code, with the existing entry point `streamlit run app.py` maintained.
2. New tests covering related-fix traversal, provenance/uncertainty, gating, version applicability, and Streamlit state when practical.
3. `docs/NODES_2026_ENHANCED_DEMO.md` containing a timed five-minute script, offline commands, expected output examples, and fallback screenshots instructions.
4. `docs/ENHANCEMENT_VALIDATION.md` containing the exact commands run, pass/fail/skipped counts, snapshot ID, embedding model, query outputs and any unresolved warnings.
5. A concise final report including changed files, features delivered, deferred scope, caveats, and any changes needed to the PowerPoint.

**Non-negotiable:** Do not alter benchmark result JSON files or slide claims merely to improve a score; re-run and label evaluations when datasets or embedding models actually change.

---

## 1. Objective and audience experience

### 1.1 Existing story

A developer enters an error. The app retrieves an issue/PR, shows a fix path through evidence in Neo4j, displays changed code and the containing release, and can abstain when evidence is insufficient.

### 1.2 New story

Turn the static fix lookup into an **interactive evidence-driven debugging investigator** with five consecutive acts:

1. **Find:** retrieve a plausible historical fix for a real Requests error.
2. **Verify:** inspect the relationship path, edge strength, and source provenance.
3. **Explore:** discover *other potentially related PRs* by traversing shared exception types, files and, if supported, code-level context. These are leads, not confirmed fixes.
4. **Apply:** contrast the same historical fix for installed Requests `2.26.0` vs `2.31.0`, correctly changing the advice.
5. **Abstain:** demonstrate an unrelated Django error that produces no reliable fix.

The wow factor must come from **explainable Neo4j graph traversals over real ingested evidence**, not arbitrary visual animation or an LLM inventing causal links.

### 1.3 Success definition

A fresh user can run the documented repository, open Troubleshoot, investigate the bundled Requests example, inspect a provenance-rich graph, open a Related Fixes panel with genuine graph-derived candidates or a transparent empty result, change the version, and see the no-match case. A speaker can complete the sequence in five minutes without network access after dependencies and snapshot are provisioned.

---

## 2. Known repository architecture, use these as integration points

These names were verified against the supplied ZIP; verify again on the branch you edit.

| Existing file | Actual responsibility | Expected change |
| --- | --- | --- |
| `app.py` | Streamlit entry, cached Neo4jStore/Investigator, backend signature, navigation | Keep entry point and existing views; possibly pass feature settings |
| `ui/investigation_view.py` | Query form, candidates, state, Summary/Fix Graph/Code Diff/Evidence/Cypher tabs | Integrate Related Fixes panel, graph details and version comparison |
| `ui/graph_view.py` | Creates Graphviz DOT for existing fix path; URL nodes | Preserve static visualization as fallback; add provenance annotations and/or enhanced inspector |
| `ui/diff_view.py` | Displays evidence-backed patch diffs | Reuse for confirmed fix; avoid presenting related candidates as changed-code proof of this issue |
| `graph/queries.py` | Parameterized Cypher, existing fulltext/vector queries, candidate profiles, fix paths, graph health | Add separate `RELATED_FIXES_*` queries and provenance query; no interpolation of user data |
| `graph/db.py` | `Neo4jStore.query(cypher, **params)` | Reuse query interface; no new DB client |
| `retrieval/pipeline.py` | `Investigator.investigate`, result/bundle/candidate selection | Keep core behavior stable; add narrowly scoped read-only related-fix service or lazy resolver |
| `retrieval/fix_path.py` | Expands a selected issue/PR into verified/referenced/inferred fix path | Reuse selected primary PR, avoid cross-issue contamination |
| `retrieval/rerank.py` | Ranking and gating signals | **Do not modify scoring or thresholds** for this feature |
| `explanation/evidence_bundle.py` | Grounded evidence and primary fix representation | Reuse existing primary-fix identity; do not treat related items as established fix evidence |
| `config.py` | Neo4j and embedding settings | Optional feature flags/limits only |
| `graph/schema.cypher` and `graph/ingest_neo4j.py` | Actual node/relationship creation and indexes | Avoid schema changes if query-only solution works |
| `tests/test_integration_neo4j.py` | Neo4j-backed integration assertions | Add optional live related-fix and version tests |
| `tests/test_app.py`, `tests/test_retrieval_unit.py` | App and retrieval tests | Add unit tests and state safety checks |
| `docs/DEMO_SCRIPT.md` | Existing conference instructions | Preserve; add an enhanced script or clearly update references |

**Existing graph facts relevant to the feature:** `(:PullRequest)-[:HAS_PATCH]->(:PatchHunk)-[:TOUCHES_EXCEPTION]->(:ExceptionType)`; `(:PullRequest)-[:CHANGES]->(:File)`; `(:PullRequest)-[:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]->(:Issue)`; `(:PullRequest)-[:MERGED_AS]->(:Commit)<-[:CONTAINS_COMMIT]-(:Release)`. Current fix graph uses `st.graphviz_chart` and `fix_path_dot`. Existing Query/Evidence tabs show executed Cypher and source links. Existing investigation results include `result.bundle`, candidates, query parameters, and backend snapshot signature.

**Important implementation observation:** `FIX_PATH_FOR_ISSUE` collects PR-linked hunks and several releases, while `ui/graph_view.py` currently draws file nodes rather than individual patch hunk nodes. The new evidence explorer must respect that distinction rather than pretending every visual edge is identical to a stored database edge.

---

## 3. Prioritization and scope

### P0: Must work for conference

- P0-A: Related Fix Discovery from actual Neo4j relationships, with explicit evidence and clear noncausal language.
- P0-B: Evidence-rich fix graph with visible strength/provenance and a usable way to inspect relationship details. Existing Graphviz fallback retained.
- P0-C: Visible version applicability for installed `2.26.0` vs `2.31.0`, using **existing** version logic and live result data.
- P0-D: No-match behavior preserved, no newly introduced leakage of related PRs into primary verified answer.
- P0-E: New unit and integration tests; documented, repeatable five-minute demo.

### P1: Should have if P0 is stable

- P1-A: Interactive relationship details via selectable edge/card, with a dedicated provenance sidebar or table.
- P1-B: Related PR comparison, expandable evidence path and changed-file overlap list.
- P1-C: One-click or clearly labeled button to investigate a related PR **as a separate candidate**, maintaining the original investigation context.
- P1-D: Parameterized Cypher shown beside the related-fix results, with recorded parameters, timing, and returned row count.

### P2: Only if feasible after all tests pass

- Neo4j Graph Data Science Node Similarity for PR neighbors using shared evidence graph structure.
- Rich force-directed browser visualization (requires carefully assessed dependency, offline assets, sanitization, and fallback).
- Persisted relatedness edges or embeddings for PRs, only with explicit differentiation between derived similarity and factual GitHub claims.

**Out of scope:** A new RAG framework, an autonomous agent, cross-repository generalization, generative code fixes, changing collection snapshots solely to improve the demo, an LLM-generated ranking score, changing the meaning of `CLOSES`, or producing new benchmark accuracy numbers without evaluation.

---

## 4. P0-A: Related Fix Discovery (functional specification)

### 4.1 User journey

After a successful investigation, show a subsection/tab named **Related Fixes (graph discovery)**. The section is anchored to the *selected primary fix PR* from the evidence bundle, **not merely the top raw retrieval hit**. Display the anchor as `Exploring PR #5414` with an outbound source URL. If the investigation has no primary fix or no reliable match, do not show related PR suggestions as though they solve the input. Instead show a neutral message explaining that related exploration requires a supported anchor.

Each related result must display:

- PR number and title, linked to source (from actual stored properties).
- Relationship basis: `Shared exception`, `Shared changed file`, optionally `Shared patch section/function` if verifiable.
- Exact supporting exception names and file paths, and a path or provenance source when available.
- A **bounded, explainable relatedness score**, explicitly marked a heuristic, not fix probability.
- An evidence/limitation label: `Related candidate, NOT a verified fix for this issue`.
- Earliest containing release **only if directly supported for that related PR**; otherwise `Unknown`.
- Unambiguous `View evidence` / `Investigate separately` affordance if safe and implemented.

### 4.2 Traversal requirements

Implement a read-only backend, suggested module `retrieval/related_fixes.py`, e.g.:

```python
@dataclass(frozen=True)
class RelatedFix:
    pr_id: str
    pr_number: int
    title: str
    url: str | None
    shared_exceptions: tuple[str, ...]
    shared_files: tuple[str, ...]
    shared_sections: tuple[str, ...]
    score: float
    reasons: tuple[str, ...]
    evidence_paths: tuple[dict, ...]
    earliest_release: str | None


def find_related_fixes(store, selected_pr_id: str, *, limit: int = 8) -> list[RelatedFix]:
    ...
```

This is a **proposed** API. Follow actual project conventions, and prefer minimal additions.

Use at least these independent graph-based signals:

**Signal A: Shared exception type**

`(sourcePR)-[:HAS_PATCH]->(sourceHunk)-[:TOUCHES_EXCEPTION]->(exception)<-[:TOUCHES_EXCEPTION]-(candidateHunk)<-[:HAS_PATCH]-(candidatePR)`.

Prefer a `TOUCHES_EXCEPTION` relationship whose `in_changed_lines=true` if that property exists and is populated. If missing, describe the evidence precisely as a *hunk mention* rather than claiming the changed lines raised that exception. Consider whether the existing ingestion marks exception mentions in context lines; inspect that implementation.

**Signal B: Shared file touched**

`(sourcePR)-[:CHANGES]->(file)<-[:CHANGES]-(candidatePR)`.

A shared file is weak evidence because many unrelated PRs change `requests/models.py`. Prevent a busy file from overwhelming results. At minimum, cap file-only matches, prefer exception corroboration, and discount paths such as lockfiles, generated files and large vendored trees. Never claim shared file = same bug.

**Signal C: Shared patch section/function (optional if sufficiently reliable)**

Compare `PatchHunk.section` or parsed function tokens from hunks **only when the actual data has meaningful sections**. Matching an empty string, generic header, `__init__`, or noisy unrelated tokens must not count as meaningful support. Do not infer direct PR causality.

### 4.3 Safe Cypher shape

Add `RELATED_FIXES_BY_EXCEPTION` and `RELATED_FIXES_BY_FILE` or one modular query to `graph/queries.py`. Adapt exact property names after inspecting schema. Parameterize `$selected_pr_id`, `$limit`, thresholds. Avoid string-building with user-controlled data.

Example direction only (not a copy-paste guaranteed query):

```cypher
MATCH (src:PullRequest {id: $selected_pr_id})-[:HAS_PATCH]->(h1:PatchHunk)
      -[r1:TOUCHES_EXCEPTION]->(e:ExceptionType)
      <-[r2:TOUCHES_EXCEPTION]-(h2:PatchHunk)<-[:HAS_PATCH]-(dst:PullRequest)
WHERE dst <> src
  AND (coalesce(r1.in_changed_lines, false) = true)
  AND (coalesce(r2.in_changed_lines, false) = true)
RETURN dst.id AS id, dst.number AS number, dst.title AS title, dst.url AS url,
       collect(DISTINCT e.name) AS shared_exceptions
ORDER BY size(shared_exceptions) DESC, number ASC
LIMIT $limit
```

**Important:** If the actual snapshot lacks `in_changed_lines` flags, do not use this strict query without checking coverage, since it could return nothing. Use a clearly described fallback query and label evidence accordingly. Confirm what `r1` and `r2` actually store.

Use deduplication by PR stable ID. Ensure source PR is excluded, ties are deterministic, and the same candidate appearing through multiple shared files/exceptions becomes one result with aggregated reasons. Enforce per-query bounds and explicit `LIMIT` for demo responsiveness. Avoid cardinality explosion from joining all hunk/file combinations in one huge query; prefer separate queries and merge in Python, or pre-aggregate each branch using Cypher subqueries.

### 4.4 Relatedness scoring

Provide an interpretable, deterministic relative score. Suggested P0 scoring **subject to data inspection**:

- `+3` for each distinct shared changed-line exception type (cap at two).
- `+1` for a shared exception only mentioned in hunk text/context (cap at two).
- `+1` for meaningful shared patch section/function (cap at two).
- `+0.25` for each shared changed file (cap at two); decrease or exclude high-degree files.
- Small bonus for more than one distinct type of evidence.

Normalize to a 0–100 **relative discovery score** if useful, but explicitly label `heuristic relatedness, not a probability, not a proof of a fix`. Unit tests must verify ordering and caps. A simpler score is acceptable if explained, reproducible, and does not rank file-only matches above corroborated exception matches by accident. **Do not change `retrieval/rerank.py`'s primary fix scoring.**

### 4.5 Candidate eligibility and labels

- Only include real `PullRequest` nodes with valid positive PR numbers.
- Restrict to source repository/snapshot; current app uses one loaded snapshot, but still ensure no cross-snapshot mixing if the data model allows it.
- Candidate does not need an Issue link to appear as a *related PR*. If one exists, show link level and provenance separately, without promoting it to a fix for the **source** issue.
- An inferred or referenced link is not a verified closing link.
- A result with shared file only should clearly say `Weak relationship: same changed file`.
- No relation returned? Show `No related PRs found under current evidence rules`, not a fabricated or fallback vector-similar item silently relabeled graph-related.
- If Neo4j fails, show a brief error, retain the existing investigated answer, and do not display cached related data for a different snapshot.

### 4.6 Snapshot-aware caching

Existing `app.py` computes a `backend_signature` using URI/snapshot/loaded timestamp. Any new cached related results must be keyed by that signature plus source PR ID and feature configuration. Clear or hide related results on: different snapshot, new investigation, changed selected candidate, or no-match. Preserve unrelated form input and current tabs where possible.

---

## 5. P0-B: Interactive and provenance-rich evidence graph

### 5.1 Core objective

Improve the existing `Fix Graph` tab so audiences can answer **"Why does this arrow exist?"**. Keep the visual readable on a projector and do not overload it with every hunk/commit in a large PR.

### 5.2 Minimum acceptable P0 design (works with Graphviz)

Retain `st.graphviz_chart(fix_path_dot(bundle))`, but add next to/below it a selectable **Evidence Edges** table or cards, with:

- From node, relationship type, to node.
- Strength: verified/referenced/inferred/structural/derived ancestry.
- Evidence basis, source URL, retrieved time and extractor version, where the actual relationship exposes them.
- Explanation of what the relationship *does and does not prove*.
- A click-through to the source evidence when available.
- A visual legend: solid/dashed/dotted for issue-PR evidence only; structural relationships should have their own clear indication, not all labeled verified by implication.

Use `st.selectbox`, `st.dataframe` row selection if compatible, or expanders. **Actual interactive click-on-edge is P1, not required for P0.** This is important because the existing Graphviz chart supports URL nodes, not a general Python callback per edge.

### 5.3 Required graph nodes and relationships

At minimum, visualize Issue, PullRequest, Merge Commit, File or PatchHunk, and Release. Show `REFERENCES` or `CLOSES` exactly as the live graph says. `MERGED_AS` and `CONTAINS_COMMIT` should be separate from issue-PR strength classification. If patch hunks are visualized, use the stored `HAS_PATCH` and `IN_FILE` edges accurately; do not draw one composite edge and claim it's a direct stored relationship without labeling it a simplified view.

Graph edge provenance is already in the underlying result for PR→Issue via `links`. For other edges, create a **read-only parameterized Cypher** service to load provenance if needed. Do not infer `retrieved_at` or `basis` when absent. Display `Not available in this snapshot`.

### 5.4 Version and evidence consistency

- The bundled git-only example shows a `REFERENCES` link for PR #5414 → issue #5367. Do not silently relabel to verified `CLOSES` just because the live GitHub website says so.
- If an API-enriched snapshot is loaded and the graph actually contains validated `CLOSES`, show that stronger edge with its real source.
- Labels shown in Fix Graph, Evidence tab, Summary and Related Fixes must not contradict one another.
- Visual paths must match query results and relationship directions.
- Keep graph readable at typical 1366×768 presentation resolution; use concise labels and a capped number of files.

### 5.5 P1 interactive upgrade (optional)

For a true click-select visualization, evaluate an appropriate Streamlit-compatible component or small purpose-built UI that runs **offline**, does not require external CDNs, and exposes selected node/edge IDs. Do not introduce a heavyweight JS dependency merely for animation. Use strict allowlists/sanitization for data imported from GitHub (titles, URLs, diff text). Existing graph remains the fallback if the interactive component fails. If there is no robust option, deliver the P0 selectable provenance inspector instead.

---

## 6. P0-C: Version-aware fix applicability showcase

### 6.1 Requirement

Expose an obvious comparison for the **same** example error:

- Installed library `2.26.0`: identified historical fix first shipped in `v2.27.0`; show `Upgrade to 2.27.0 or a compatible later release` only if the code's version logic and metadata support it.
- Installed library `2.31.0`: that historical fix is already included; show `This historical fix is already present. Investigate another cause or version/environment difference.` Avoid promising it resolves every related symptom.

### 6.2 Implementation constraint

The current investigation form already accepts `Library version` and the reranker tracks `already_fixed_in_user_version`. **Prefer reuse and UI emphasis** rather than writing an independent version comparison algorithm. Use supported version comparison instead of lexicographic strings (e.g. `2.9` vs `2.10`). Honor the metadata flag `release_history_complete` and the existing distinction between containing-release and earliest-containing-release.

If prereleases, branch backports, fork versions, missing tags, or incomplete history cannot be evaluated reliably, display `Cannot determine applicability from available release ancestry` with a reason. Do not conflate commit ancestry with confirmation that the runtime behavior is fixed in every environment.

### 6.3 UX

Offer either two guided sample buttons (`Show 2.26.0` / `Show 2.31.0`) or an easily visible current-version card. If both cases are shown simultaneously, ensure they were produced by the actual library's applicability logic, not a hard-coded prose comparison. Re-run with same selected query and updated version; mark the installed version being evaluated prominently.

The `Hide fixes already in my version` option must not be toggled in a way that makes the example disappear unexpectedly during the demo. Display that toggle's impact clearly.

---

## 7. P0-D: Preserve trust boundaries and no-match behavior

Relatedness is not causality. It must be impossible for a related PR to acquire primary-fix status solely because it shares a file or exception.

Mandatory invariants:

1. No related PRs are attached to `EvidenceBundle.fixes` unless independently supported by the original issue's explicit fix-path relationships and selection rules.
2. No `CLOSES` or `REFERENCES` edges are written or upgraded from a Related Fixes traversal.
3. No explanation generator gets related candidates presented as authoritative fixes for the original issue.
4. Existing `no_reliable_match` status and unrelated Django negative control must behave identically before/after the feature.
5. Related Fixes does not bypass the existing evidence gate or change retrieval ranking.
6. All untrusted GitHub strings must be treated as display data, not executable HTML, code, Cypher, or LLM instructions.
7. All database reads must be parameterized; there must be no browser-accessible arbitrary Cypher execution introduced by this feature.
8. Treat `verified` as GitHub-verification of a historical relationship, **not** proof that the patch fixes the current user's error.

If a user explicitly chooses `Investigate related PR`, create a **new investigation context** or a clearly separated inspection panel, with that PR's own provenance and a disclaimer. Do not quietly replace the initial finding while leaving the original query and status on screen.

---

## 8. Proposed UI composition and user interactions

Recommended layout for the `Troubleshoot` view after submission:

**Header:** Historical fix found / weak match / no reliable match; query mode, timestamp/timing, snapshot ID, embedder identity.

**Primary result:** issue and PR links, evidence strength badge, changed files and earliest containing release when supported.

**Investigation tabs:**

1. `Summary` (existing)
2. `Fix Graph` (improved visual and relationship inspector)
3. `Related Fixes` (**new**, P0)
4. `Code Diff` (existing)
5. `Evidence` (existing)
6. `Cypher` (existing, plus any separate related-fix query trace)

**Related Fixes tab:**

- Headline: `Explore PRs with shared graph evidence`.
- Context: `Source PR #...`, `Repository`, `Snapshot`, `Embedding model` (if displayed; note this discovery uses graph, not an embedder).
- Explanation: `These are structurally related leads, not verified resolutions of your current bug.`
- Filter: `All`, `Shared exception`, `Shared file`, optionally `Shared function`, using exact captured reasons.
- Limit: default 5 or 8 results; no unbounded rendering.
- Candidate cards/table: `PR`, `Title`, `Shared evidence`, `Relatedness score`, `Earliest containing release`, `Source`.
- `Expand evidence`: show why discovered and explicit paths; source GitHub links and relationship details.
- If none: accurate empty state plus possible explanation (`git-only snapshot`, sparse graph, changed-line filters, etc.), without pretending evidence exists.

**Projector usability:** headings and labels visible at 100% browser zoom; avoid requiring scrolling through long raw diff text to expose the moment of discovery; important labels should be visible immediately. Do not add unnecessary charts or flashy decorations.

---

## 9. Data handling and performance

- Aim for under 2 seconds per related-fix query on the bundled snapshot **after Neo4j is warm**, as a performance goal, not a claim. Measure and report actual timings.
- Enforce hard query result cap (e.g. 20), UI cap (e.g. 8), and avoid unbounded graph expansion.
- Deduplicate candidate PRs and supporting exceptions/files.
- Avoid N+1 database calls per candidate; fetch in batches.
- Use existing Neo4j indexes/labels; add indexes only when query plan proves a need.
- Use `EXPLAIN`/`PROFILE` on critical queries (do not modify DB with these) and report any red flags.
- Preserve Streamlit caching keyed by snapshot identity. Explicitly test that switching snapshot or candidate cannot leak previous related results.
- Offline operation: do not request GitHub API, Claude, a browser CDN, external fonts or embedding downloads during the live presentation sequence. Preload dependencies/snapshot in advance.
- Graceful degradation: if related-fix feature fails, show the original Fix Graph and Summary intact, with a nonfatal warning.

---

## 10. Required automated testing

### 10.1 Unit tests for related-fix service (P0)

Build tests with a fake store returning stable rows or isolated small graph fixtures. Cover:

- Source PR excluded.
- Distinct exception and file reason aggregation and deduplication.
- Candidate related by shared exception ranks above file-only candidate (subject to documented scoring rules).
- High-degree/common files do not dominate.
- Empty exception arrays, missing titles/URLs, missing `in_changed_lines` properties handled safely.
- No arbitrary PR from another repo/snapshot included where such scoping is represented.
- Tie ordering is deterministic.
- Query parameters used; malicious title/query text cannot become Cypher source.
- Limit is enforced.
- Relatedness label never claims `CLOSES` for source issue.
- No database writes performed.

### 10.2 Integration tests with Neo4j (P0)

On the project's bundled Requests snapshot (if service available):

1. Load snapshot and query a selected PR such as #5414.
2. Verify returned candidates correspond to **real** PR IDs/numbers that exist in loaded graph.
3. For each returned reason, verify the actual graph path exists using independent Cypher, not just the same service logic.
4. Verify dedupe and stable ordering in repeated queries.
5. Verify no new `CLOSES|REFERENCES|POSSIBLY_ADDRESSES` edges are added.
6. Verify original issue #5367 → PR #5414 → commit `d0965999` → earliest `v2.27.0` behavior is unchanged.
7. Verify Django negative control still returns `no_reliable_match` in all retrieval modes.
8. Verify version `2.26.0` vs `2.31.0` applicability state is correct using actual engine output.

If no same-exception candidates exist, that is **not a test failure**. Record zero results, then verify shared-file candidates if any. Never hard-code invented related PR numbers as expected results.

### 10.3 UI tests (P0/P1)

- Tab exists only in proper result context or shows a harmless empty state.
- New search clears previous related results.
- New selected candidate changes the related anchor.
- Snapshot signature change invalidates all cached investigation and related results.
- Neo4j unavailable: the original UI does not crash.
- No-match never displays related candidates as fixes.
- Existing Summary, Evidence, Code Diff, Graph and Cypher tabs still render.
- URLs are source links only, display text is escaped/sanitized.
- Live sample change from 2.26.0 to 2.31.0 refreshes displayed applicability.

### 10.4 Commands and honest reporting

Run, at minimum, if prerequisites exist:

```bash
python -m compileall -q app.py ui graph retrieval explanation tests
python -m pytest -q
make demo-check
```

For a clean integration environment, follow README/compose instructions to start Neo4j, load a known snapshot, and rerun relevant integration tests. If a test is skipped due to a missing service or optional dependency, report `SKIPPED` with reason, **not** pass. Verify no regressions in the 17-case evaluation **without modifying baseline files**. If benchmark results differ, investigate and report the discrepancy; do not hide it.

---

## 11. Conference presentation and documentation changes

Produce `docs/NODES_2026_ENHANCED_DEMO.md` with:

### Five-minute recommended sequence

| Time | Action | What the audience should learn |
| --- | --- | --- |
| 0:00–1:05 | Run Requests `UnicodeError` sample with version `2.26.0` | The system finds a grounded historical fix |
| 1:05–2:00 | Open Fix Graph and evidence edge inspector | Neo4j shows *why* the links exist, not merely connected dots |
| 2:00–3:15 | Open Related Fixes and inspect one real path **only if populated** | Graph traversal reveals other potentially relevant code history, without asserting causality |
| 3:15–4:15 | Set version `2.31.0` and rerun same query | Same historical fix, different applicability advice |
| 4:15–5:00 | Use unrelated Django error and show no-match | A useful debugging system must be able to say "not enough evidence" |

If no compelling related fixes are found in the snapshot, **do not fabricate or seed artificial results**. Instead use the existing rich provenance and version contrast as the primary wow moment, and describe the related traversal as limited by available graph evidence. Optionally identify a second *real* source PR with better graph-connected neighbors from the existing snapshot, with documented selection criteria and without hard-coded fake nodes.

### Preflight checklist

- Neo4j reachable and loaded with expected snapshot.
- Repository view confirms source, API status, embedding model, link strengths and snapshot signature.
- `make demo-check` and all actual integration tests pass, or explicit gaps recorded.
- No remote service required during demo; Claude toggle off / deterministic explanation.
- Correct real traceback captured for Requests 2.26.0; replace the existing constructed `Full traceback` sample if validated.
- Verify PR #5414 label is `REFERENCES` in git-only snapshot; do not show API-upgraded `CLOSES` unless graph genuinely contains it.
- Identify one **verified real** related candidate and capture its actual exception/file path and PR link, or use a no-result alternative.
- Screenshots/video recorded for all steps, aligned to actual outputs.
- Browser, resolution, font size and network-off test complete.
- A rollback path exists (`git` branch/tag and known-good demo images).

### Accuracy notes for slide authors

- The deck says BGE embeddings; the bundled offline benchmark was produced with a **hashing n-gram** embedder. Do not attribute those metrics to BGE.
- The evaluation has 17 positive Requests cases and six negative controls; do not imply cross-repository generalization.
- The git-only example's issue–PR link is `REFERENCES`, not API-verified `CLOSES`.
- The slide's constructed traceback may not match real Requests 2.26.0 stack frames. Replace after direct reproduction.
- Avoid claims such as `graph search proves the bug is fixed`, `all related PRs solve the error`, `LLM cannot hallucinate`, or `Neo4j is the only database that can do this`.
- Suggested revised demo takeaway: **"We don't just retrieve a fix. We can inspect its evidence, explore related history, test applicability, and refuse unsupported conclusions."**

---

## 12. Feature acceptance criteria / definition of done

Mark each AC as PASS / FAIL / NOT RUN with evidence in `docs/ENHANCEMENT_VALIDATION.md`.

| ID | Acceptance criterion | Priority |
| --- | --- | --- |
| AC-01 | Existing app starts with `streamlit run app.py`; existing views and core investigation remain functional | P0 |
| AC-02 | Related Fixes anchored to selected primary PR, with real graph-derived candidates or honest empty state | P0 |
| AC-03 | Each related result explains actual shared exception/file/section evidence and does **not** claim to fix original issue | P0 |
| AC-04 | Parameterized bounded Cypher, deduplication, deterministic ordering, no DB mutation | P0 |
| AC-05 | Main Fix Graph plus provenance inspector shows correct edge types, sources and missing metadata explicitly | P0 |
| AC-06 | `2.26.0` / `2.31.0` version behavior is visible and grounded in engine logic | P0 |
| AC-07 | Original issue #5367 path and existing no-match behavior unchanged | P0 |
| AC-08 | Related evidence never enters primary evidence bundle or LLM as verified fix for source issue | P0 |
| AC-09 | Related data invalidated when selected anchor or snapshot changes | P0 |
| AC-10 | Unit tests pass and Neo4j integration tests run when environment supports them; report skips honestly | P0 |
| AC-11 | Documented five-minute offline demo, source links and fallback | P0 |
| AC-12 | Existing benchmark outputs unchanged unless independently rerun and documented | P0 |
| AC-13 | Selectable node/edge details with reliable offline interactive component | P1 |
| AC-14 | Related PR can be inspected separately without implying fix status | P1 |
| AC-15 | Optional GDS Node Similarity adds genuine evaluated value over simple traversals | P2 |

---

## 13. Suggested implementation sequence (small PRs / checkpoints)

**Checkpoint 0. Baseline:** Confirm branch/status, inspect schema and `TOUCHES_EXCEPTION` properties, capture current `python -m pytest -q`, `make demo-check` and initial evaluation. Do not rewrite unrelated files.

**Checkpoint 1. Backend:** Add `RELATED_FIXES` Cypher and `retrieval/related_fixes.py`; unit-test graph rows, deduplication, scoring, bounds and uncertainty. Independently inspect real candidate paths against bundled snapshot.

**Checkpoint 2. UI:** Add `Related Fixes` tab and evidence cards/empty state in `ui/investigation_view.py`. Ensure proper snapshot and selected PR state; no-match preservation.

**Checkpoint 3. Evidence graph:** Improve Graphviz edge labeling and add selectable provenance inspector using actual result/link properties or a new read-only query. Preserve current chart if optional interactive UX unavailable.

**Checkpoint 4. Version demo:** Add obvious comparison affordances based on existing `Investigator.investigate`; test both versions without hardcoding result prose.

**Checkpoint 5. Rehearsal:** Run full Neo4j-backed app, capture actual query and related candidate outputs, time the flow, verify offline mode and source links, update demo doc and validation report.

**Checkpoint 6. Optional:** Only after passing P0, explore Graph Data Science Node Similarity or a richer graph UI. Prove usefulness with actual paths and performance measurements before enabling by default.

---

## 14. Questions Claude must resolve from source rather than assume

1. Does `TOUCHES_EXCEPTION.in_changed_lines` exist consistently on this snapshot, and which parser creates it?
2. Does `PatchHunk.section` contain meaningful function-level data for the sample PRs?
3. Does the existing snapshot contain any same-exception related PRs for #5414? If not, which *real* PR is a more compelling honest demonstration?
4. What are the actual `File` path and `PullRequest` repository/snapshot fields? Can cross-snapshot relationships exist in the current database?
5. Does the current evidence bundle expose all provenance fields needed for structural edges? If not, which ones require separate Cypher?
6. What is the exact deployed Neo4j version, and does it support the proposed Cypher constructs? Verify queries in that version.
7. Can any optional graph interaction tool run fully offline in the team's Streamlit environment without unsafe HTML or external JavaScript?
8. Are tests being skipped? Why? Which integration tests were actually executed against Neo4j?
9. Do graph relationships and version advice remain correct after selected-candidate changes?
10. Are the repository's screenshots/slide claims fully aligned with the final loaded snapshot and model?

Report each answer in `docs/ENHANCEMENT_VALIDATION.md` with code locations, query output or an explicit unverified caveat.

---

## 15. What NOT to do

- Do not make a related PR look like a verified closure of the source issue.
- Do not insert made-up PRs, issues, releases, exceptions, evidence paths or test fixtures into the live dataset to stage a wow moment.
- Do not copy a sample Cypher query blindly without checking property names and real data coverage.
- Do not promise Graph Data Science gains without implementing and measuring them.
- Do not modify the user-facing library version advice by string comparison alone.
- Do not promote a GitHub `REFERENCES` edge to `CLOSES` without validated GitHub source evidence present in the loaded graph.
- Do not break the deterministic explanation mode or make Claude a runtime dependency for the live talk.
- Do not suppress warnings, skip failures silently, or overwrite baseline benchmark result files.
- Do not change the visible UI from trustworthy to sensational. The presentation should impress through traceability and clarity.

---

## 16. Final message requested from Claude after implementation

Return a compact summary with the following headings:

1. **Implemented** (P0, P1 and P2 separated)
2. **Files modified**
3. **Exact Cypher used and why**
4. **Real related-fix examples found in the snapshot** (or `none`, with honest explanation)
5. **Tests and commands executed** (PASS / FAIL / SKIPPED / NOT RUN)
6. **Known remaining limitations**
7. **How to launch and rehearse the enhanced demo**
8. **Slide/script wording that must change**

The deliverable is ready only when the team can reproduce the full conference sequence locally, with no fabricated evidence, and verify every new relationship directly in Neo4j.
