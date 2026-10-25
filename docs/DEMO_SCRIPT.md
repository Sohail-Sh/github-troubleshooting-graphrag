# NODES 2026 demo script (30 minutes, two speakers)

**Before the talk:** the snapshot is collected and loaded, the model is cached, the venv is installed,
`make demo-check` passes with Wi-Fi turned off, and screenshots/a screen recording of every step are in the
slide deck as backup.

| Time | Speaker | Segment |
|---|---|---|
| 0:00–3:00 | A | The problem: a stack trace in hand, the fix buried in a 5-year-old issue thread. Search returns *text*; engineers need a *path* (issue → PR → code → release). |
| 3:00–8:00 | B | Ingestion: one URL in, a versioned snapshot out. Show the **Repository** view: counts, the three link levels, snapshot id and collection warnings. |
| 8:00–13:00 | A | The graph model in Neo4j Browser (queries below). Provenance on every edge; REFERENCES is never promoted to CLOSES. |
| 13:00–21:00 | B | **Troubleshoot** view, live: the headline UnicodeError → Fix Graph → Code Diff → Evidence → Cypher. Then the "Unrelated error" sample → explicit no-match. |
| 21:00–26:00 | A | **Evaluation** view: keyword vs vector vs hybrid vs hybrid + graph; ranking vs fix-path correctness; caveats. |
| 26:00–30:00 | both | Lessons, limits, Q&A. |

## Live sequence (Troubleshoot view)

1. Click **Full traceback**, set *Library version* `2.26.0`, *Hybrid + graph re-ranking*, **Investigate**.
   - Banner: *Historical fix found*. Point out retrieval score vs match support vs evidence badge (three
     different things; none is a probability).
   - **Fix Graph**: Issue #5367 ← PR #5414 → `requests/models.py`, commit `d0965999`, release **v2.27.0
     (earliest)**, release note. Dashed edge = *referenced*, solid = *verified*.
   - **Code Diff**: `elif host.startswith((u'*', u'.')):` raising `InvalidURL`.
   - **Summary → Applicability**: "Your version 2.26.0 predates v2.27.0".
   - **Cypher**: the exact parameterized queries that ran, with timings.
2. Switch the mode to **Keyword** and re-run: show where #5367 lands without the graph.
3. Set *Library version* `2.31.0`: the candidate is flagged *already contains the fix*.
4. Click **Unrelated error**: *No sufficiently supported historical fix found*, with next steps.
5. (Optional) Toggle **LLM explanation**: same evidence, Claude's wording, every claim cited; mention the
   validator that rejects any issue/PR/version/SHA not in the evidence.

## Neo4j Browser queries

```cypher
// 1. The milestone fix path
MATCH path = (i:Issue {number: 5367})<-[link:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]-(pr:PullRequest)
             -[:MERGED_AS]->(c:Commit)<-[:CONTAINS_COMMIT {earliest_containing: true}]-(r:Release)
OPTIONAL MATCH files = (pr)-[:HAS_PATCH]->(:PatchHunk)-[:IN_FILE]->(:File)
RETURN path, files
```

```cypher
// 2. Provenance on the link
MATCH (:PullRequest {number: 5414})-[l]->(:Issue {number: 5367})
RETURN type(l), l.verification_status, l.basis, l.bases, l.source_url, l.retrieved_at, l.extractor_version
```

```cypher
// 3. Evidence levels across the repository
MATCH (:PullRequest)-[l:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]->(:Issue)
RETURN type(l) AS link, l.verification_status AS level, count(*) AS n ORDER BY n DESC
```

```cypher
// 4. Which fixes touched code that raises InvalidURL, and when they shipped
MATCH (e:ExceptionType {name: 'InvalidURL'})<-[:TOUCHES_EXCEPTION {in_changed_lines: true}]-(h:PatchHunk)
      <-[:HAS_PATCH]-(pr:PullRequest)-[:MERGED_AS]->(c)<-[:CONTAINS_COMMIT {earliest_containing: true}]-(r)
RETURN pr.number, pr.title, h.path, r.tag ORDER BY r.published_at
```

```cypher
// 5. Hybrid retrieval primitives
CALL db.index.fulltext.queryNodes('searchable_text', 'UnicodeError^3 idna label', {limit: 5})
YIELD node, score RETURN node.search_kind, node.search_title, score
```

## If something breaks on stage

* Neo4j down → `docker compose up -d neo4j`, then switch to the recorded video while it starts.
* LLM unavailable → the app falls back to the deterministic explanation automatically (a warning says so).
* Anything else → backup screenshots in the deck, in the same order as the live sequence.
