"""Schema Registry — SQLite-backed store for tool schemas and drift events."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class SchemaSnapshot:
    tool_id: str
    schema_version: str
    json_schema: dict
    known_aliases: dict = field(default_factory=dict)
    source: str = "manual"
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_validated_at: datetime | None = None


@dataclass
class DriftEvent:
    drift_id: str
    tool_id: str
    detected_at: datetime
    trace_id: str
    severity: str
    fields_changed: list
    auto_corrected: bool
    correction_map: dict
    schema_version_at_time: str


_CREATE_SCHEMAS = """
CREATE TABLE IF NOT EXISTS schemas (
    id            INTEGER PRIMARY KEY,
    tool_id       TEXT NOT NULL,
    version       TEXT NOT NULL,
    json_schema   TEXT NOT NULL,
    aliases       TEXT,
    source        TEXT NOT NULL,
    is_current    BOOLEAN NOT NULL DEFAULT 1,
    registered_at DATETIME NOT NULL,
    last_validated DATETIME,
    UNIQUE(tool_id, version)
);
CREATE INDEX IF NOT EXISTS idx_schemas_tool_current ON schemas(tool_id, is_current);
"""

_CREATE_DRIFT = """
CREATE TABLE IF NOT EXISTS drift_events (
    id                     INTEGER PRIMARY KEY,
    drift_id               TEXT NOT NULL UNIQUE,
    tool_id                TEXT NOT NULL,
    detected_at            DATETIME NOT NULL,
    trace_id               TEXT,
    severity               TEXT NOT NULL,
    fields_changed         TEXT NOT NULL,
    auto_corrected         BOOLEAN NOT NULL,
    correction_map         TEXT,
    schema_version_at_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drift_tool     ON drift_events(tool_id);
CREATE INDEX IF NOT EXISTS idx_drift_detected ON drift_events(detected_at);
"""


class SchemaRegistry:
    """Thread-safe SQLite-backed schema registry with in-memory cache."""

    _CACHE_TTL = 60  # seconds

    def __init__(self, db_path: str = "./conduit.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[SchemaSnapshot, float]] = {}  # tool_id → (snap, ts)
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _close(self, con: sqlite3.Connection) -> None:
        if self._db_path != ":memory:":
            con.close()

    def register(self, tool_id: str, schema: dict, version: str = "1.0.0",
                 aliases: dict | None = None, source: str = "manual") -> None:
        with self._lock:
            con = self._connect()
            con.execute("UPDATE schemas SET is_current=0 WHERE tool_id=?", (tool_id,))
            con.execute(
                "INSERT OR REPLACE INTO schemas "
                "(tool_id, version, json_schema, aliases, source, is_current, registered_at) "
                "VALUES (?,?,?,?,?,1,?)",
                (tool_id, version, json.dumps(schema),
                 json.dumps(aliases or {}), source,
                 datetime.now(timezone.utc).isoformat()),
            )
            con.commit()
            self._close(con)
            self._cache.pop(tool_id, None)  # invalidate

    def get_current(self, tool_id: str) -> SchemaSnapshot | None:
        # Check cache
        if tool_id in self._cache:
            snap, ts = self._cache[tool_id]
            if time.monotonic() - ts < self._CACHE_TTL:
                return snap

        with self._lock:
            con = self._connect()
            row = con.execute(
                "SELECT tool_id, version, json_schema, aliases, source, registered_at "
                "FROM schemas WHERE tool_id=? AND is_current=1",
                (tool_id,),
            ).fetchone()
            self._close(con)

        if row is None:
            return None

        snap = SchemaSnapshot(
            tool_id=row[0],
            schema_version=row[1],
            json_schema=json.loads(row[2]),
            known_aliases=json.loads(row[3] or "{}"),
            source=row[4],
            registered_at=datetime.fromisoformat(row[5]),
        )
        self._cache[tool_id] = (snap, time.monotonic())
        return snap

    def report_drift(self, tool_id: str, observed_params: dict, trace_id: str = "",
                     severity: str = "medium", fields_changed: list | None = None,
                     auto_corrected: bool = False, correction_map: dict | None = None) -> None:
        import uuid
        snap = self.get_current(tool_id)
        version = snap.schema_version if snap else "unknown"
        with self._lock:
            con = self._connect()
            con.execute(
                "INSERT OR IGNORE INTO drift_events "
                "(drift_id, tool_id, detected_at, trace_id, severity, fields_changed, "
                "auto_corrected, correction_map, schema_version_at_time) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), tool_id, datetime.now(timezone.utc).isoformat(),
                 trace_id, severity, json.dumps(fields_changed or []),
                 auto_corrected, json.dumps(correction_map or {}), version),
            )
            con.commit()
            self._close(con)

    def list_schemas(self) -> list[SchemaSnapshot]:
        with self._lock:
            con = self._connect()
            rows = con.execute(
                "SELECT tool_id, version, json_schema, aliases, source, registered_at "
                "FROM schemas WHERE is_current=1"
            ).fetchall()
            self._close(con)
        return [
            SchemaSnapshot(
                tool_id=r[0], schema_version=r[1],
                json_schema=json.loads(r[2]), known_aliases=json.loads(r[3] or "{}"),
                source=r[4], registered_at=datetime.fromisoformat(r[5]),
            )
            for r in rows
        ]

    def list_drift_events(self, tool_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            con = self._connect()
            if tool_id:
                rows = con.execute(
                    "SELECT * FROM drift_events WHERE tool_id=? ORDER BY detected_at DESC LIMIT ?",
                    (tool_id, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM drift_events ORDER BY detected_at DESC LIMIT ?", (limit,)
                ).fetchall()
            self._close(con)
        cols = ["id", "drift_id", "tool_id", "detected_at", "trace_id", "severity",
                "fields_changed", "auto_corrected", "correction_map", "schema_version_at_time"]
        return [dict(zip(cols, r)) for r in rows]

    def validate(self, tool_id: str, params: dict, trace_id: str = ""):
        """Convenience method — 03_SCHEMA_VALIDATOR.md §5 Python SDK.
        Delegates to SchemaValidator so callers don't need to import it separately.
        """
        from conduit.intelligence.validator import SchemaValidator
        return SchemaValidator(self).validate(tool_id, params, trace_id=trace_id)

    def ingest_mcp_manifest(self, path: str) -> int:
        """Ingest an MCP server manifest — 03_SCHEMA_VALIDATOR.md §5 Python SDK."""
        from conduit.registry.mcp import ingest_mcp_manifest as _ingest
        return _ingest(self, path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._db_path == ":memory:":
            return self._mem_con  # type: ignore[attr-defined]
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        if self._db_path == ":memory:":
            self._mem_con = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_con.row_factory = sqlite3.Row
        con = self._connect()
        con.executescript(_CREATE_SCHEMAS)
        con.executescript(_CREATE_DRIFT)
        con.commit()
        if self._db_path != ":memory:":
            self._close(con)
