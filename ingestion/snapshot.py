"""Reproducible, versioned evidence snapshots.

Layout::

    data/snapshots/<snapshot_id>/
        manifest.json          # repo, timestamps, counts, collection status/errors, checksums
        raw/git/*.jsonl        # evidence collected from the local git clone
        raw/api/*.jsonl        # evidence collected from the GitHub REST/GraphQL APIs
        graph/nodes.jsonl      # normalized graph (output of ingestion.normalize)
        graph/relationships.jsonl
        embeddings/<model>.jsonl
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Snapshot:
    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, snapshots_dir: Path, snapshot_id: str, **manifest: Any) -> "Snapshot":
        root = snapshots_dir / snapshot_id
        root.mkdir(parents=True, exist_ok=True)
        existing = {}
        if (root / "manifest.json").exists():
            existing = json.loads((root / "manifest.json").read_text())
        snap = cls(root, {**existing, "snapshot_id": snapshot_id, **manifest})
        snap.manifest.setdefault("collections", {})
        snap.manifest.setdefault("errors", [])
        snap.save_manifest()
        return snap

    @classmethod
    def open(cls, path: Path) -> "Snapshot":
        manifest = json.loads((path / "manifest.json").read_text())
        return cls(path, manifest)

    @property
    def id(self) -> str:
        return self.manifest["snapshot_id"]

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def write(self, source: str, name: str, rows: Iterable[dict[str, Any]]) -> int:
        n = write_jsonl(self.path("raw", source, f"{name}.jsonl"), rows)
        self.manifest["collections"].setdefault(source, {})[name] = n
        return n

    def read(self, source: str, name: str) -> list[dict[str, Any]]:
        return list(read_jsonl(self.path("raw", source, f"{name}.jsonl")))

    def has(self, source: str, name: str) -> bool:
        return self.path("raw", source, f"{name}.jsonl").exists()

    def record_error(self, stage: str, message: str) -> None:
        self.manifest["errors"].append({"stage": stage, "message": message, "at": utc_now()})

    def finalize_checksums(self) -> None:
        checksums = {}
        for p in sorted(self.root.rglob("*.jsonl")):
            if p.relative_to(self.root).parts[0] == "embeddings":
                continue  # derived cache, rebuilt per embedding model
            checksums[str(p.relative_to(self.root))] = sha256_file(p)
        self.manifest["checksums"] = checksums
        self.save_manifest()

    def verify_checksums(self) -> list[str]:
        """Return the files whose content no longer matches the manifest."""
        bad = []
        for rel, digest in self.manifest.get("checksums", {}).items():
            p = self.root / rel
            if not p.exists() or sha256_file(p) != digest:
                bad.append(rel)
        return bad

    def save_manifest(self) -> None:
        (self.root / "manifest.json").write_text(json.dumps(self.manifest, indent=2, sort_keys=True))
