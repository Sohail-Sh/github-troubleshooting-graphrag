// Constraints: one stable id per entity, so repeated ingestion MERGEs instead of duplicating.
CREATE CONSTRAINT repository_id IF NOT EXISTS FOR (n:Repository) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT issue_id IF NOT EXISTS FOR (n:Issue) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT pr_id IF NOT EXISTS FOR (n:PullRequest) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT commit_id IF NOT EXISTS FOR (n:Commit) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT file_id IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT file_revision_id IF NOT EXISTS FOR (n:FileRevision) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT patch_hunk_id IF NOT EXISTS FOR (n:PatchHunk) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT release_id IF NOT EXISTS FOR (n:Release) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT comment_id IF NOT EXISTS FOR (n:Comment) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT error_signature_id IF NOT EXISTS FOR (n:ErrorSignature) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT exception_type_id IF NOT EXISTS FOR (n:ExceptionType) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT stack_frame_id IF NOT EXISTS FOR (n:StackFrame) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (n:Doc) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT doc_chunk_id IF NOT EXISTS FOR (n:DocChunk) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT code_chunk_id IF NOT EXISTS FOR (n:CodeChunk) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT changelog_entry_id IF NOT EXISTS FOR (n:ChangelogEntry) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT searchable_id IF NOT EXISTS FOR (n:Searchable) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT snapshot_meta_id IF NOT EXISTS FOR (n:SnapshotMeta) REQUIRE n.id IS UNIQUE;

// Lookup indexes for the identifiers people type.
CREATE INDEX issue_number IF NOT EXISTS FOR (n:Issue) ON (n.number);
CREATE INDEX pr_number IF NOT EXISTS FOR (n:PullRequest) ON (n.number);
CREATE INDEX commit_sha IF NOT EXISTS FOR (n:Commit) ON (n.sha);
CREATE INDEX file_path IF NOT EXISTS FOR (n:File) ON (n.path);
CREATE INDEX release_tag IF NOT EXISTS FOR (n:Release) ON (n.tag);
CREATE INDEX exception_name IF NOT EXISTS FOR (n:ExceptionType) ON (n.name);

// Keyword search across every searchable evidence node (issues, comments, PRs, patches, notes, docs, code).
CREATE FULLTEXT INDEX searchable_text IF NOT EXISTS
FOR (n:Searchable) ON EACH [n.search_title, n.search_text]
OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}};

// Vector search (384 dims: BAAI/bge-small-en-v1.5, or the offline hashing embedder).
CREATE VECTOR INDEX searchable_embedding IF NOT EXISTS
FOR (n:Searchable) ON (n.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}};
