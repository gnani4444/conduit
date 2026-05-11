"""Failure Pattern Store — ToolCallEvent persistence."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS tool_call_events (
    id                    INTEGER PRIMARY KEY,
    event_id              TEXT NOT NULL UNIQUE,
    trace_id              TEXT NOT NULL,
    span_id               TEXT NOT NULL DEFAULT '',
    tool_id               TEXT NOT NULL,
    tool_version          TEXT,
    step_index            INTEGER NOT NULL DEFAULT 0,
    framework             TEXT NOT NULL,
    params_hash           TEXT NOT NULL,
    params_schema_version TEXT,
    outcome               TEXT NOT NULL,
    failure_class         TEXT,
    failure_sub_type      TEXT,
    failure_severity      TEXT,
    recovery_action       TEXT,
    recovery_attempt      INTEGER DEFAULT 0,
    recovery_succeeded    BOOLEAN,
    validation_result     TEXT NOT NULL DEFAULT 'skipped',
    corrections_applied   TEXT,
    latency_ms            REAL,
    validation_latency_ms REAL,
    created_at            DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_tool    ON tool_call_events(tool_id);
CREATE INDEX IF NOT EXISTS idx_events_outcome ON tool_call_events(outcome);
CREATE INDEX IF NOT EXISTS idx_events_created ON tool_call_events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_trace   ON tool_call_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_failure ON tool_call_events(failure_class, failure_sub_type);
"""


@dataclass
class ToolCallEvent:
    tool_id: str
    trace_id: str
    framework: str
    outcome: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    span_id: str = ""
    tool_version: str = ""
    step_index: int = 0
    params_hash: str = ""
    params_schema_version: str = ""
    failure_class: str | None = None
    failure_sub_type: str | None = None
    failure_severity: str | None = None
    recovery_action: str | None = None
    recovery_attempt: int = 0
    recovery_succeeded: bool | None = None
    validation_result: str = "skipped"
    corrections_applied: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    validation_latency_ms: float = 0.0
    # Opt-in only
    params_raw: dict | None = None
    result_summary: str | None = None


_lock = threading.Lock()
_db_path: str = "./conduit.db"


def set_db_path(path: str) -> None:
    global _db_path
    _db_path = path


def _connect() -> sqlite3.Connection:
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_db_path, check_same_thread=False)
    con.executescript(_CREATE_EVENTS)
    con.commit()
    return con


def save_event(event: ToolCallEvent) -> None:
    with _lock:
        con = _connect()
        con.execute(
            "INSERT OR IGNORE INTO tool_call_events "
            "(event_id, trace_id, span_id, tool_id, tool_version, step_index, framework, "
            "params_hash, params_schema_version, outcome, failure_class, failure_sub_type, "
            "failure_severity, recovery_action, recovery_attempt, recovery_succeeded, "
            "validation_result, corrections_applied, latency_ms, validation_latency_ms, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.event_id, event.trace_id, event.span_id, event.tool_id,
                event.tool_version, event.step_index, event.framework,
                event.params_hash, event.params_schema_version, event.outcome,
                event.failure_class, event.failure_sub_type, event.failure_severity,
                event.recovery_action, event.recovery_attempt, event.recovery_succeeded,
                event.validation_result, json.dumps(event.corrections_applied),
                event.latency_ms, event.validation_latency_ms,
                event.created_at.isoformat(),
            ),
        )
        con.commit()
        con.close()


def query_events(
    tool_id: str | None = None,
    outcome: str | None = None,
    failure_class: str | None = None,
    limit: int = 100,
) -> list[dict]:
    with _lock:
        con = _connect()
        clauses, args = [], []
        if tool_id:
            clauses.append("tool_id=?"); args.append(tool_id)
        if outcome:
            clauses.append("outcome=?"); args.append(outcome)
        if failure_class:
            clauses.append("failure_class=?"); args.append(failure_class)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = con.execute(
            f"SELECT * FROM tool_call_events {where} ORDER BY created_at DESC LIMIT ?",
            args + [limit],
        ).fetchall()
        cols = [d[0] for d in con.execute(f"SELECT * FROM tool_call_events {where} LIMIT 0", args).description or []]
        con.close()

    if not rows:
        return []
    # Re-fetch with column names
    with _lock:
        con = _connect()
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT * FROM tool_call_events {where} ORDER BY created_at DESC LIMIT ?",
            args + [limit],
        ).fetchall()
        con.close()
    return [dict(r) for r in rows]
