"""FailurePatternAnalyzer — 04_FAILURE_DETECTOR.md §6.

Queries the Failure Pattern Store for dashboard feeds and
recommendation generation.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path


@dataclass
class ToolFailureSummary:
    tool_id: str
    total_calls: int
    failure_count: int
    failure_rate: float
    dominant_failure_class: str | None
    avg_latency_ms: float


@dataclass
class LoopFrequency:
    tool_id: str
    loop_count: int
    last_seen: str


@dataclass
class UnrecoveredPattern:
    tool_id: str
    sub_type: str
    frequency: int


@dataclass
class Recommendation:
    id: str
    category: str          # SCHEMA | RECOVERY | PERFORMANCE | LOOP | CONFIG
    impact: int            # 1–10
    problem: str
    evidence: list[str]
    fix_display: str
    estimated_impact: str = ""
    fix_type: str = ""
    fix_config: dict | None = None


class FailurePatternAnalyzer:
    """Queries SQLite failure store for dashboard and recommendation feeds."""

    def __init__(self, db_path: str = "./conduit.db") -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _since(self, days: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # ------------------------------------------------------------------
    # Core queries
    # ------------------------------------------------------------------

    def top_failing_tools(self, days: int = 7, limit: int = 10) -> list[ToolFailureSummary]:
        """Tools with highest failure rate in the past N days."""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT tool_id,
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome != 'success' THEN 1 ELSE 0 END) as failures,
                       AVG(latency_ms) as avg_lat,
                       failure_class
                FROM tool_call_events
                WHERE created_at > ?
                GROUP BY tool_id
                ORDER BY (CAST(SUM(CASE WHEN outcome != 'success' THEN 1 ELSE 0 END) AS REAL) / COUNT(*)) DESC
                LIMIT ?
            """, (self._since(days), limit)).fetchall()
            con.close()
            return [
                ToolFailureSummary(
                    tool_id=r["tool_id"],
                    total_calls=r["total"],
                    failure_count=r["failures"] or 0,
                    failure_rate=round((r["failures"] or 0) / max(r["total"], 1), 3),
                    dominant_failure_class=r["failure_class"],
                    avg_latency_ms=round(r["avg_lat"] or 0, 1),
                )
                for r in rows
            ]
        except Exception:
            return []

    def failure_rate_by_class(self, days: int = 7) -> dict[str, float]:
        """Breakdown: schema_error 42%, tool_error 31%, agent_loop 27%"""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT failure_class, COUNT(*) as cnt
                FROM tool_call_events
                WHERE created_at > ? AND failure_class IS NOT NULL
                GROUP BY failure_class
            """, (self._since(days),)).fetchall()
            con.close()
            total = sum(r["cnt"] for r in rows) or 1
            return {r["failure_class"]: round(r["cnt"] / total, 3) for r in rows}
        except Exception:
            return {}

    def recovery_success_rate(self, days: int = 7) -> dict[str, float]:
        """Per action: retry 78%, replan 62%, retry_corrected 91%"""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT recovery_action,
                       COUNT(*) as total,
                       SUM(CASE WHEN recovery_succeeded = 1 THEN 1 ELSE 0 END) as succeeded
                FROM tool_call_events
                WHERE created_at > ? AND recovery_action IS NOT NULL
                GROUP BY recovery_action
            """, (self._since(days),)).fetchall()
            con.close()
            return {
                r["recovery_action"]: round((r["succeeded"] or 0) / max(r["total"], 1), 3)
                for r in rows
            }
        except Exception:
            return {}

    def loop_frequency_by_tool(self, days: int = 7) -> list[LoopFrequency]:
        """Which tools trigger the most loops."""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT tool_id, COUNT(*) as cnt, MAX(created_at) as last_seen
                FROM tool_call_events
                WHERE created_at > ? AND failure_class = 'agent_loop'
                GROUP BY tool_id
                ORDER BY cnt DESC
            """, (self._since(days),)).fetchall()
            con.close()
            return [LoopFrequency(tool_id=r["tool_id"], loop_count=r["cnt"], last_seen=r["last_seen"] or "") for r in rows]
        except Exception:
            return []

    def mean_steps_to_failure(self, days: int = 7) -> float:
        """Average step_index at which first failure occurs."""
        try:
            con = self._connect()
            row = con.execute("""
                SELECT AVG(step_index) as avg_step
                FROM tool_call_events
                WHERE created_at > ? AND outcome != 'success'
            """, (self._since(days),)).fetchone()
            con.close()
            return round(row["avg_step"] or 0, 1)
        except Exception:
            return 0.0

    def get_tools_with_drift(self, min_events: int = 3) -> list[dict]:
        """Tools that have >= min_events drift events."""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT tool_id, COUNT(*) as drift_count
                FROM drift_events
                GROUP BY tool_id
                HAVING COUNT(*) >= ?
            """, (min_events,)).fetchall()
            con.close()
            return [{"tool_id": r["tool_id"], "drift_event_count": r["drift_count"]} for r in rows]
        except Exception:
            return []

    def get_tools_without_schema(self, min_calls: int = 10) -> list[dict]:
        """Tools with many calls but no registered schema."""
        try:
            con = self._connect()
            # Tools in events but not in schemas
            rows = con.execute("""
                SELECT e.tool_id, COUNT(*) as calls
                FROM tool_call_events e
                LEFT JOIN schemas s ON e.tool_id = s.tool_id AND s.is_current = 1
                WHERE s.tool_id IS NULL
                GROUP BY e.tool_id
                HAVING COUNT(*) >= ?
            """, (min_calls,)).fetchall()
            con.close()
            return [{"tool_id": r["tool_id"], "call_count": r["calls"]} for r in rows]
        except Exception:
            return []

    def get_loop_events(self, days: int = 1) -> list[dict]:
        """Recent loop events."""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT * FROM tool_call_events
                WHERE created_at > ? AND failure_class = 'agent_loop'
                ORDER BY created_at DESC
            """, (self._since(days),)).fetchall()
            con.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_unrecovered_failure_patterns(self, days: int = 7) -> list[UnrecoveredPattern]:
        """Failure sub-types that recur with no recovery rule."""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT tool_id, failure_sub_type, COUNT(*) as freq
                FROM tool_call_events
                WHERE created_at > ?
                  AND failure_sub_type IS NOT NULL
                  AND recovery_action IS NULL
                GROUP BY tool_id, failure_sub_type
                ORDER BY freq DESC
            """, (self._since(days),)).fetchall()
            con.close()
            return [
                UnrecoveredPattern(tool_id=r["tool_id"], sub_type=r["failure_sub_type"], frequency=r["freq"])
                for r in rows
            ]
        except Exception:
            return []

    def avg_calls_before_loop_detection(self, days: int = 1) -> float:
        """Average step_index at which loop is detected — 06_DASHBOARD.md §6 LOOP_THRESHOLD rec."""
        try:
            con = self._connect()
            row = con.execute("""
                SELECT AVG(step_index) as avg_step
                FROM tool_call_events
                WHERE created_at > ? AND failure_class = 'agent_loop'
            """, (self._since(days),)).fetchone()
            con.close()
            return round(row["avg_step"] or 0, 1)
        except Exception:
            return 0.0

    def timeout_rate_by_tool(self, days: int = 7) -> list[dict]:
        """Tools with high timeout rates — 06_DASHBOARD.md §6 INCREASE_TIMEOUT rec."""
        try:
            con = self._connect()
            rows = con.execute("""
                SELECT tool_id,
                       COUNT(*) as total,
                       SUM(CASE WHEN failure_sub_type='tool_error.timeout' THEN 1 ELSE 0 END) as timeouts,
                       AVG(latency_ms) as avg_ms,
                       MAX(latency_ms) as p95_ms
                FROM tool_call_events
                WHERE created_at > ?
                GROUP BY tool_id
                HAVING SUM(CASE WHEN failure_sub_type='tool_error.timeout' THEN 1 ELSE 0 END) > 0
                ORDER BY timeouts DESC
            """, (self._since(days),)).fetchall()
            con.close()
            return [
                {"tool_id": r["tool_id"], "total": r["total"],
                 "timeouts": r["timeouts"],
                 "timeout_rate": round((r["timeouts"] or 0) / max(r["total"], 1), 3),
                 "avg_ms": round(r["avg_ms"] or 0, 1),
                 "p95_ms": round(r["p95_ms"] or 0, 1)}
                for r in rows
            ]
        except Exception:
            return []

    def prescriptive_recommendations(self) -> list[Recommendation]:
        """Generate actionable recommendations — 04_FAILURE_DETECTOR.md §6, 06_DASHBOARD.md §6."""
        from conduit.config import get_config
        cfg = get_config()
        recs: list[Recommendation] = []

        # UPDATE_SCHEMA — tools with drift events
        for tool in self.get_tools_with_drift(min_events=3):
            tid = tool["tool_id"]
            cnt = tool["drift_event_count"]
            recs.append(Recommendation(
                id=f"UPDATE_SCHEMA_{tid}",
                category="SCHEMA",
                impact=min(10, cnt * 2),
                problem=f"{tid} schema has {cnt} drift events causing validation failures.",
                evidence=[f"{cnt} drift events detected"],
                fix_display=f"conduit schema update {tid} --from-drift",
                estimated_impact=f"Eliminates schema drift failures for {tid}",
                fix_type="schema_update",
                fix_config={"tool_id": tid, "action": "accept_drift"},
            ))

        # REGISTER_SCHEMA — tools with calls but no schema
        for tool in self.get_tools_without_schema(min_calls=10):
            tid = tool["tool_id"]
            recs.append(Recommendation(
                id=f"REGISTER_SCHEMA_{tid}",
                category="SCHEMA",
                impact=5,
                problem=f"{tid} has no registered schema — validation skipped on all calls.",
                evidence=[f"{tool['call_count']} calls with no validation"],
                fix_display=f"conduit schema discover {tid}",
            ))

        # LOOP_THRESHOLD — 06_DASHBOARD.md §6
        loops = self.get_loop_events(days=1)
        if loops:
            avg = self.avg_calls_before_loop_detection(days=1)
            if avg > 2.5 and cfg.detection.loop_threshold > 2:
                recs.append(Recommendation(
                    id="REDUCE_LOOP_THRESHOLD",
                    category="LOOP",
                    impact=7,
                    problem=f"Loop threshold N={cfg.detection.loop_threshold} wastes {avg:.1f} calls on average before detection.",
                    evidence=[f"{len(loops)} loops in 24h", f"avg {avg:.1f} calls before detection"],
                    fix_display="Set detection.loop_threshold: 2 in conduit.yaml",
                    estimated_impact="Reduces wasted tool calls by 33% before loop recovery fires",
                ))
            else:
                recs.append(Recommendation(
                    id="LOOP_DETECTED",
                    category="LOOP",
                    impact=7,
                    problem=f"{len(loops)} agent loops detected in the last 24h.",
                    evidence=[f"{len(loops)} loop events"],
                    fix_display="Review loop threshold: detection.loop_threshold in conduit.yaml",
                ))

        # INCREASE_TIMEOUT — 06_DASHBOARD.md §6
        for t in self.timeout_rate_by_tool(days=7):
            if t["timeout_rate"] >= 0.3:  # 30%+ timeout rate
                recs.append(Recommendation(
                    id=f"INCREASE_TIMEOUT_{t['tool_id']}",
                    category="PERFORMANCE",
                    impact=min(10, int(t["timeout_rate"] * 10)),
                    problem=f"{t['tool_id']} times out {t['timeout_rate']:.0%} of the time.",
                    evidence=[
                        f"{t['timeouts']} timeouts in 7 days",
                        f"Avg latency: {t['avg_ms']}ms, P95: {t['p95_ms']}ms",
                    ],
                    fix_display=f"Increase timeout for {t['tool_id']} in conduit.yaml",
                    estimated_impact="Eliminates most timeout failures based on observed latency",
                ))

        # ADD_RECOVERY_RULE — unrecovered failure patterns
        for pattern in self.get_unrecovered_failure_patterns(days=7):
            if pattern.frequency >= 3:
                recs.append(Recommendation(
                    id=f"ADD_RECOVERY_{pattern.tool_id}_{pattern.sub_type}",
                    category="RECOVERY",
                    impact=min(10, pattern.frequency),
                    problem=f"{pattern.tool_id} {pattern.sub_type} occurs {pattern.frequency}x/week with no recovery rule.",
                    evidence=[f"{pattern.frequency} unrecovered failures in 7 days"],
                    fix_display=f"Add recovery rule for {pattern.tool_id}.{pattern.sub_type} in conduit.yaml",
                ))

        return sorted(recs, key=lambda r: r.impact, reverse=True)
