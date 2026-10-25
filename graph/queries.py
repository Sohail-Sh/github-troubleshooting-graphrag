"""Parameterized Cypher used by the retrieval engine. Shown verbatim in the UI's Cypher tab."""

KEYWORD_SEARCH = """
// Keyword retrieval: Lucene full-text over every searchable evidence node
CALL db.index.fulltext.queryNodes('searchable_text', $query, {limit: $k})
YIELD node, score
RETURN node.id AS id, node.search_kind AS kind, node.search_title AS title, score
"""

VECTOR_SEARCH = """
// Vector retrieval: cosine similarity over the same nodes' embeddings
CALL db.index.vector.queryNodes('searchable_embedding', $k, $embedding)
YIELD node, score
RETURN node.id AS id, node.search_kind AS kind, node.search_title AS title, score
"""

# Every search hit is resolved to the historical report ("anchor") it is evidence for.
# A hit on a patch, PR, commit or release note is traced through the graph to the issue it is linked to;
# a merged PR with no linked issue is its own anchor. Docs and code chunks are context, never anchors.
RESOLVE_ANCHORS = """
UNWIND $ids AS hid
MATCH (n:Searchable {id: hid})
CALL {
  WITH n
  WITH n WHERE n:Issue
  RETURN n AS anchor, 'issue text' AS via
  UNION
  WITH n
  MATCH (i:Issue)-[:HAS_COMMENT]->(n)
  RETURN i AS anchor, 'issue comment' AS via
  UNION
  WITH n
  MATCH (pr:PullRequest)-[:HAS_PATCH]->(n)
  OPTIONAL MATCH (pr)-[:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]->(i:Issue)
  RETURN coalesce(i, pr) AS anchor, 'patch hunk' AS via
  UNION
  WITH n
  WITH n WHERE n:PullRequest
  OPTIONAL MATCH (n)-[:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]->(i:Issue)
  RETURN coalesce(i, n) AS anchor, 'pull request' AS via
  UNION
  WITH n
  MATCH (n)-[:MENTIONS]->(x)
  OPTIONAL MATCH (x:PullRequest)-[:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]->(i:Issue)
  RETURN coalesce(i, x) AS anchor, 'release note' AS via
}
RETURN hid AS hit_id, anchor.id AS anchor_id, via,
       labels(anchor)[0] AS anchor_label,
       n.search_text AS text,
       coalesce(n.pr_changed_files, n.changed_file_count, 1) AS breadth,
       vector.similarity.cosine(n.embedding, $embedding) AS vector_similarity
"""

CANDIDATE_PROFILE = """
// Graph features used for re-ranking: errors, frames, fix links, changed files, releases
UNWIND $anchor_ids AS aid
MATCH (a {id: aid})
OPTIONAL MATCH (a)-[:MENTIONS_ERROR]->(:ErrorSignature)-[:OF_TYPE]->(et1:ExceptionType)
OPTIONAL MATCH (a)-[:MENTIONS_EXCEPTION]->(et2:ExceptionType)
OPTIONAL MATCH (a)-[:HAS_STACK_FRAME]->(sf:StackFrame)
WITH a, collect(DISTINCT et1.name) + collect(DISTINCT et2.name) AS report_exceptions,
     collect(DISTINCT sf.function_name) AS report_functions, collect(DISTINCT sf.file_path) AS report_files
OPTIONAL MATCH (pr:PullRequest)-[link:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]->(a)
WITH a, report_exceptions, report_functions, report_files,
     collect(DISTINCT {pr: pr.id, type: type(link), status: link.verification_status}) AS links
WITH a, report_exceptions, report_functions, report_files,
     [l IN links WHERE l.pr IS NOT NULL] + CASE WHEN a:PullRequest THEN [{pr: a.id, type: 'SELF', status: 'self'}] ELSE [] END AS links
UNWIND (CASE WHEN size(links) = 0 THEN [null] ELSE links END) AS link
OPTIONAL MATCH (fix:PullRequest {id: link.pr})
OPTIONAL MATCH (fix)-[:CHANGES]->(f:File)
OPTIONAL MATCH (fix)-[:HAS_PATCH]->(h:PatchHunk)
OPTIONAL MATCH (h)-[te:TOUCHES_EXCEPTION]->(et3:ExceptionType)
OPTIONAL MATCH (fix)-[:MERGED_AS]->(:Commit)<-[rc:CONTAINS_COMMIT {earliest_containing: true}]-(rel:Release)
WITH a, report_exceptions, report_functions, report_files, link,
     collect(DISTINCT f.path) AS changed_files, collect(DISTINCT h.section) AS hunk_sections,
     collect(DISTINCT et3.name) AS fix_exceptions, collect(DISTINCT rel.tag) AS earliest_release,
     fix.changed_file_count AS fix_file_count,
     EXISTS { (:ChangelogEntry)-[:MENTIONS]->(fix) } AS in_release_notes
RETURN a.id AS anchor_id, labels(a)[0] AS label, a.number AS number, a.title AS title, a.url AS url,
       coalesce(a.hydrated, true) AS hydrated, a.state AS state, a.created_at AS created_at,
       report_exceptions, report_functions, report_files,
       collect(CASE WHEN link IS NULL THEN null ELSE link {.*, changed_files: changed_files,
                    hunk_sections: hunk_sections, fix_exceptions: fix_exceptions,
                    earliest_release: earliest_release, file_count: fix_file_count,
                    in_release_notes: in_release_notes} END) AS fixes
"""

FIX_PATH_FOR_ISSUE = """
// Fix path: Issue <- fixing PR -> merge commit -> changed code -> releases containing the fix
MATCH (i:Issue {id: $anchor_id})
OPTIONAL MATCH (i)-[:HAS_COMMENT]->(c:Comment)
WITH i, collect(c {.id, .body, .author, .url, .created_at})[0..20] AS comments
OPTIONAL MATCH (pr:PullRequest)-[link:CLOSES|REFERENCES|POSSIBLY_ADDRESSES]->(i)
WITH i, comments, pr, collect(link {type: type(link), .*}) AS links
OPTIONAL MATCH (pr)-[:MERGED_AS]->(mc:Commit)
OPTIONAL MATCH (mc)<-[rc:CONTAINS_COMMIT]-(rel:Release)
WITH i, comments, pr, links, mc, rc, rel ORDER BY rel.published_at
WITH i, comments, pr, links, mc,
     collect(rel {.tag, .url, .published_at, earliest: rc.earliest_containing, basis: rc.basis,
                  source_url: rc.source_url})[0..5] AS releases,
     count(rel) AS containing_release_count
OPTIONAL MATCH (pr)-[ch:CHANGES]->(f:File)
WITH i, comments, pr, links, mc, releases, containing_release_count,
     collect(f {.path, status: ch.status, additions: ch.additions, deletions: ch.deletions}) AS files
OPTIONAL MATCH (pr)-[:HAS_PATCH]->(h:PatchHunk)
OPTIONAL MATCH (h)-[te:TOUCHES_EXCEPTION]->(et:ExceptionType)
WITH i, comments, pr, links, mc, releases, containing_release_count, files, h,
     collect(DISTINCT et.name) AS hunk_exceptions
WITH i, comments, pr, links, mc, releases, containing_release_count, files,
     collect(h {.id, .path, .diff, .section, .additions, .deletions, .url, exceptions: hunk_exceptions}) AS hunks
OPTIONAL MATCH (note:ChangelogEntry)-[:MENTIONS]->(x) WHERE x = pr OR x = i
RETURN i {.id, .number, .title, .body, .url, .state, .created_at, .closed_at, .labels, .hydrated,
          .type_confirmed} AS issue,
       comments,
       pr {.id, .number, .title, .body, .url, .merged_at, .merge_commit_sha, .head_ref, .author} AS pr,
       links,
       mc {.sha, .short_sha, .url, .committed_at, .message} AS merge_commit,
       releases, containing_release_count, files, hunks,
       collect(DISTINCT note {.id, .text, .version, .category, .date}) AS release_notes
"""

FIX_PATH_FOR_PR = """
// Fix path for a merged PR that has no linked issue
MATCH (pr:PullRequest {id: $anchor_id})
OPTIONAL MATCH (pr)-[:MERGED_AS]->(mc:Commit)
OPTIONAL MATCH (mc)<-[rc:CONTAINS_COMMIT]-(rel:Release)
WITH pr, mc, rc, rel ORDER BY rel.published_at
WITH pr, mc,
     collect(rel {.tag, .url, .published_at, earliest: rc.earliest_containing, basis: rc.basis,
                  source_url: rc.source_url})[0..5] AS releases,
     count(rel) AS containing_release_count
OPTIONAL MATCH (pr)-[ch:CHANGES]->(f:File)
WITH pr, mc, releases, containing_release_count,
     collect(f {.path, status: ch.status, additions: ch.additions, deletions: ch.deletions}) AS files
OPTIONAL MATCH (pr)-[:HAS_PATCH]->(h:PatchHunk)
OPTIONAL MATCH (h)-[te:TOUCHES_EXCEPTION]->(et:ExceptionType)
WITH pr, mc, releases, containing_release_count, files, h, collect(DISTINCT et.name) AS hunk_exceptions
WITH pr, mc, releases, containing_release_count, files,
     collect(h {.id, .path, .diff, .section, .additions, .deletions, .url, exceptions: hunk_exceptions}) AS hunks
OPTIONAL MATCH (note:ChangelogEntry)-[:MENTIONS]->(pr)
RETURN null AS issue, [] AS comments,
       pr {.id, .number, .title, .body, .url, .merged_at, .merge_commit_sha, .head_ref, .author} AS pr,
       [{type: 'SELF', verification_status: 'self', basis: 'merged_pull_request', source_url: pr.url}] AS links,
       mc {.sha, .short_sha, .url, .committed_at, .message} AS merge_commit,
       releases, containing_release_count, files, hunks,
       collect(DISTINCT note {.id, .text, .version, .category, .date}) AS release_notes
"""

SUPPORTING_CONTEXT = """
// Docs and source code near the query (context only; never treated as a fix)
UNWIND $ids AS hid
MATCH (n:Searchable {id: hid}) WHERE n:DocChunk OR n:CodeChunk
RETURN n.id AS id, labels(n)[0] AS label, n.search_title AS title, n.text AS text, n.url AS url
"""

GRAPH_HEALTH = """
MATCH (m:SnapshotMeta) WITH m LIMIT 1
CALL { MATCH (i:Issue) RETURN count(i) AS issues, sum(CASE WHEN i.hydrated THEN 1 ELSE 0 END) AS hydrated_issues }
CALL { MATCH (p:PullRequest) RETURN count(p) AS merged_prs }
CALL { MATCH ()-[r:CLOSES]->() RETURN count(r) AS verified_links }
CALL { MATCH ()-[r:REFERENCES]->() RETURN count(r) AS referenced_links }
CALL { MATCH ()-[r:POSSIBLY_ADDRESSES]->() RETURN count(r) AS inferred_links }
CALL { MATCH (h:PatchHunk) RETURN count(h) AS patch_hunks }
CALL { MATCH (r:Release) RETURN count(r) AS releases }
CALL { MATCH (s:Searchable) RETURN count(s) AS searchable, count(s.embedding) AS embedded }
RETURN m {.*} AS meta, issues, hydrated_issues, merged_prs, verified_links, referenced_links, inferred_links,
       patch_hunks, releases, searchable, embedded
"""

INDEX_STATUS = """
SHOW INDEXES YIELD name, type, state, populationPercent
WHERE name IN ['searchable_text', 'searchable_embedding']
RETURN name, type, state, populationPercent
"""
