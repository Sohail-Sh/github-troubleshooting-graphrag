import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("EMBEDDER", "hashing")
os.environ.setdefault("LLM_PROVIDER", "none")


def neo4j_available() -> bool:
    try:
        from graph.db import Neo4jStore

        ok, _ = Neo4jStore().ping()
        return ok and bool(Neo4jStore().query("MATCH (m:SnapshotMeta) RETURN m LIMIT 1"))
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(not neo4j_available(), reason="needs Neo4j with the psf/requests snapshot loaded")
