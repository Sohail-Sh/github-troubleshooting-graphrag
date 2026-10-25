"""Central configuration, read from environment variables (optionally a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
EXTRACTOR_VERSION = "1.0"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "password"))
    neo4j_database: str = field(default_factory=lambda: _env("NEO4J_DATABASE", "neo4j"))

    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    snapshots_dir: Path = field(
        default_factory=lambda: Path(_env("SNAPSHOTS_DIR", str(PROJECT_ROOT / "data" / "snapshots")))
    )
    http_cache_dir: Path = field(
        default_factory=lambda: Path(_env("HTTP_CACHE_DIR", str(PROJECT_ROOT / "data" / "http_cache")))
    )

    # "fastembed" (BAAI/bge-small-en-v1.5, 384 dims) or "hashing" (offline, lexical fallback)
    embedder: str = field(default_factory=lambda: _env("EMBEDDER", "fastembed"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    embedding_dimensions: int = 384

    # "anthropic" or "none" (deterministic explanations only)
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "none"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-opus-5-5"))

    # Retrieval gates (tuned on evaluation/benchmark_cases.json; see docs/EVALUATION.md)
    min_term_coverage: float = field(default_factory=lambda: float(_env("MIN_TERM_COVERAGE", "0.5")))
    min_vector_similarity: float = field(default_factory=lambda: float(_env("MIN_VECTOR_SIMILARITY", "0.80")))
    rrf_k: int = 60


def get_settings() -> Settings:
    return Settings()
