"""Neo4j connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from neo4j import Driver, GraphDatabase

from config import PROJECT_ROOT, Settings, get_settings


@lru_cache(maxsize=4)
def _driver(uri: str, user: str, password: str) -> Driver:
    return GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")


class Neo4jStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.driver = _driver(self.settings.neo4j_uri, self.settings.neo4j_user, self.settings.neo4j_password)
        self.database = self.settings.neo4j_database

    def query(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        records, _, _ = self.driver.execute_query(cypher, params, database_=self.database)
        return [r.data() for r in records]

    def write(self, cypher: str, **params: Any) -> None:
        with self.session() as s:
            s.execute_write(lambda tx: tx.run(cypher, params).consume())

    @contextmanager
    def session(self) -> Iterator[Any]:
        with self.driver.session(database=self.database) as s:
            yield s

    def ping(self) -> tuple[bool, str]:
        try:
            info = self.driver.get_server_info()
            return True, f"{info.agent} at {self.settings.neo4j_uri}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as-is
            return False, f"{type(exc).__name__}: {exc}"

    def apply_schema(self) -> None:
        text = (PROJECT_ROOT / "graph" / "schema.cypher").read_text()
        statements = []
        for stmt in text.split(";"):
            stmt = "\n".join(line for line in stmt.splitlines() if not line.strip().startswith("//")).strip()
            if stmt:
                statements.append(stmt)
        for stmt in statements:
            self.write(stmt)
        self.query("CALL db.awaitIndexes(300)")
