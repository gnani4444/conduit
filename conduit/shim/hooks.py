"""Standalone hook functions — thin wrappers around ConduitProcessor."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_processor: "ConduitProcessor | None" = None  # type: ignore[name-defined]


def _get_processor():
    global _processor
    if _processor is None:
        from conduit.shim.processor import ConduitProcessor
        _processor = ConduitProcessor()
    return _processor


def pre_tool_hook(tool_id: str, params: dict, trace_id: str = "", framework: str = "unknown") -> dict:
    """Fire before tool execution. Returns (possibly corrected) params."""
    return _get_processor().pre_tool_hook(tool_id, params, trace_id=trace_id, framework=framework)


def post_tool_hook(
    tool_id: str,
    outcome: str,
    result: Any = None,
    error: Any = None,
    trace_id: str = "",
    latency_ms: float = 0.0,
    framework: str = "unknown",
) -> None:
    """Fire after tool execution."""
    _get_processor().post_tool_hook(
        tool_id, outcome, result=result, error=error,
        trace_id=trace_id, latency_ms=latency_ms, framework=framework,
    )


def on_agent_start(task_id: str, task_goal: str = "", framework: str = "unknown") -> None:
    _get_processor().on_agent_start(task_id, task_goal, framework)


def on_agent_end(task_id: str, outcome: str = "success", framework: str = "unknown") -> None:
    _get_processor().on_agent_end(task_id, outcome, framework)
