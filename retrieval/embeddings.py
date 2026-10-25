"""Text embeddings.

* ``FastEmbedEmbedder`` - BAAI/bge-small-en-v1.5 (384 dims) via FastEmbed/ONNX. The default.
* ``HashingEmbedder``   - deterministic feature hashing of word + character n-grams (384 dims). Needs no model
  download, so it works fully offline and in CI. It is essentially lexical and must not be presented as a
  semantic model; the active model name is stored in the graph and shown in the UI.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol, Sequence

DIMENSIONS = 384
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|\d+")


class Embedder(Protocol):
    name: str
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _split_identifier(tok: str) -> list[str]:
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", tok).replace("_", " ").split()
    return [p.lower() for p in parts if p]


class HashingEmbedder:
    name = "hashing-ngrams-v1"
    dimensions = DIMENSIONS

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        tokens = []
        for tok in _TOKEN.findall(text[:6000]):
            tokens.append(tok.lower())
            sub = _split_identifier(tok)
            if len(sub) > 1:
                tokens.extend(sub)
        feats = list(tokens)
        feats += [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
        for tok in tokens:
            padded = f"#{tok}#"
            feats += [padded[i : i + 4] for i in range(max(1, len(padded) - 3))]
        for f in feats:
            h = hashlib.blake2b(f.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dimensions
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FastEmbedEmbedder:
    dimensions = DIMENSIONS

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None):
        from fastembed import TextEmbedding  # imported lazily: heavy, and downloads the model on first use

        self.name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(list(texts), batch_size=64)]

    def embed_query(self, text: str) -> list[float]:
        # BGE models expect this instruction prefix on queries (not on passages).
        return next(iter(self._model.query_embed([text]))).tolist()


@lru_cache(maxsize=4)
def get_embedder(kind: str = "fastembed", model_name: str = "BAAI/bge-small-en-v1.5") -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "fastembed":
        import os

        return FastEmbedEmbedder(model_name, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    raise ValueError(f"unknown embedder {kind!r}")


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
