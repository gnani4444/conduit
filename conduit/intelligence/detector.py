"""Failure Detector — classifies tool failures and detects agent loops."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ToolCallSpan:
    tool_id: str
    outcome: str
    params: dict = field(default_factory=dict)
    exception_type: str | None = None
    exception_message: str | None = None
    http_status: int | None = None
    result: Any = None
    latency_ms: float = 0.0
    trace_id: str = ""
    span_id: str = ""
    step_index: int = 0


@dataclass
class FailureClassification:
    failure_class: str
    sub_type: str
    severity: str
    evidence: dict = field(default_factory=dict)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = ""
    span_id: str = ""
    step_index: int = 0


class ToolFailureDetector:
    """Classifies tool call failures from span data."""

    def classify(self, span: ToolCallSpan) -> FailureClassification | None:
        if span.outcome == "success":
            return None

        # Check HTTP status codes first (may occur without exception_type)
        if span.http_status == 429:
            return FailureClassification(
                failure_class="tool_error", sub_type="tool_error.rate_limit",
                severity="medium",
                evidence={"http_status": 429},
                trace_id=span.trace_id, span_id=span.span_id,
            )
        if span.http_status in (401, 403):
            return FailureClassification(
                failure_class="tool_error", sub_type="tool_error.auth",
                severity="critical",
                evidence={"http_status": span.http_status},
                trace_id=span.trace_id, span_id=span.span_id,
            )

        if span.exception_type:
            if "Timeout" in span.exception_type or "timeout" in span.exception_type.lower():
                return FailureClassification(
                    failure_class="tool_error", sub_type="tool_error.timeout",
                    severity="high",
                    evidence={"exception": span.exception_type, "latency_ms": span.latency_ms},
                    trace_id=span.trace_id, span_id=span.span_id, step_index=span.step_index,
                )
            if "Auth" in span.exception_type:
                return FailureClassification(
                    failure_class="tool_error", sub_type="tool_error.auth",
                    severity="critical",
                    evidence={"exception": span.exception_type},
                    trace_id=span.trace_id, span_id=span.span_id,
                )
            return FailureClassification(
                failure_class="tool_error", sub_type="tool_error.execution",
                severity="high",
                evidence={"exception": span.exception_type, "message": span.exception_message},
                trace_id=span.trace_id, span_id=span.span_id, step_index=span.step_index,
            )

        if span.result is None or span.result == "" or span.result == []:
            return FailureClassification(
                failure_class="tool_error", sub_type="tool_error.empty_result",
                severity="low",
                evidence={"result": repr(span.result)},
                trace_id=span.trace_id, span_id=span.span_id,
            )

        return None


class AgentLoopDetector:
    """Detects identical and cycling tool call loops within a task."""

    def __init__(self, window_size: int = 10, threshold: int = 3) -> None:
        self.window_size = window_size
        self.threshold = threshold
        self._call_history: deque[str] = deque(maxlen=window_size)

    def check(self, span: ToolCallSpan) -> FailureClassification | None:
        call_sig = f"{span.tool_id}:{_stable_hash(span.params)}"
        self._call_history.append(call_sig)

        # Identical loop
        count = sum(1 for s in self._call_history if s == call_sig)
        if count >= self.threshold:
            return FailureClassification(
                failure_class="agent_loop", sub_type="agent_loop.identical",
                severity="high",
                evidence={
                    "call_signature": call_sig,
                    "count_in_window": count,
                    "window_size": self.window_size,
                },
                trace_id=span.trace_id, span_id=span.span_id, step_index=span.step_index,
            )

        # Tool cycling: A→B→A→B
        if len(self._call_history) >= 4:
            recent = list(self._call_history)[-4:]
            if recent[0] == recent[2] and recent[1] == recent[3] and recent[0] != recent[1]:
                return FailureClassification(
                    failure_class="agent_loop", sub_type="agent_loop.tool_cycling",
                    severity="medium",
                    evidence={"pattern": [s.split(":")[0] for s in recent]},
                    trace_id=span.trace_id, span_id=span.span_id,
                )

        return None

    def reset(self) -> None:
        """Reset per-task state. Call at on_agent_start."""
        self._call_history.clear()


def _stable_hash(params: dict) -> str:
    serialized = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]
