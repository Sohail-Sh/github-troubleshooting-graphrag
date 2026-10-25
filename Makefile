PY ?= python
SNAPSHOT ?= psf-requests-v2.32.3
REPO ?= https://github.com/psf/requests
REF ?= v2.32.3
SINCE ?= 2017-01-01
UNTIL ?= 2024-06-30
EMBEDDER ?= fastembed

.PHONY: help neo4j model collect collect-git normalize load data evaluate test app demo-check

help:
	@echo "make neo4j        start Neo4j (docker compose)"
	@echo "make model        download the embedding model once (needs internet)"
	@echo "make collect      git + GitHub API collection into data/snapshots/$(SNAPSHOT) (needs GITHUB_TOKEN)"
	@echo "make collect-git  git-only collection (no token, works offline once cloned)"
	@echo "make data         normalize + load the snapshot into Neo4j with embeddings"
	@echo "make evaluate     run the retrieval benchmark"
	@echo "make test         run the test suite"
	@echo "make app          start the Streamlit app"
	@echo "make demo-check   offline rehearsal checks"

neo4j:
	docker compose up -d neo4j

model:
	$(PY) -c "from retrieval.embeddings import get_embedder; e = get_embedder('fastembed'); print(e.name, len(e.embed_query('ok')))"

collect:
	$(PY) -m ingestion.fetch_repository $(REPO) --sources git,api --ref $(REF) --since $(SINCE) --until $(UNTIL) --snapshot-id $(SNAPSHOT)

collect-git:
	$(PY) -m ingestion.fetch_repository $(REPO) --sources git --ref $(REF) --since $(SINCE) --until $(UNTIL) --snapshot-id $(SNAPSHOT)

normalize:
	$(PY) -m ingestion.normalize $(SNAPSHOT)

load:
	$(PY) -m graph.ingest_neo4j $(SNAPSHOT) --reset --embedder $(EMBEDDER)

data: normalize load

evaluate:
	$(PY) -m evaluation.evaluate_retrieval

test:
	$(PY) -m pytest -q

app:
	streamlit run app.py

demo-check:
	$(PY) -m pytest -q tests/test_integration_neo4j.py tests/test_app.py
	$(PY) -m evaluation.evaluate_retrieval
