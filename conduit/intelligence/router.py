"""Tool Router — v0.2 stub.

01_ARCHITECTURE.md §3: "Tool Router: Input=intent string + tool registry,
Output=ranked tool list + confidence, Sync < 5ms, v0.2"

Not implemented in v0.1. Returns None for all routing requests so the
shim passes through unmodified. The interface is defined here so adapters
can call it without version-checking.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RoutingResult:
    suggested_tool: str
    confidence: float          # 0.0–1.0
    alternatives: list[str]
    reasoning: str = ""


class ToolRouter:
    """
    v0.2 stub — semantic tool routing based on intent embeddings.
    Returns None until the routing model is trained (requires data from
    the Failure Pattern Store — see 00_PRD_MASTER.md §6 "Out of scope").
    """

    def route(self, intent: str, available_tools: list[str]) -> RoutingResult | None:
        """
        v0.1: always returns None (pass-through).
        v0.2: will return ranked tool suggestions based on intent embeddings.
        """
        return None  # Not implemented until v0.2

    def record_outcome(self, tool_id: str, intent: str, outcome: str) -> None:
        """
        Record routing outcome for future model training.
        v0.1: no-op. v0.2: writes to routing training store.
        """
        pass
