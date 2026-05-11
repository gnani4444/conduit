"""Recovery Engine — selects and builds recovery instructions for detected failures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from conduit.intelligence.detector import FailureClassification

# (failure_class, sub_type, attempt) → action
# "*" matches any sub_type
_RECOVERY_MATRIX: dict[tuple, str] = {
    ("schema_error", "*", 0): "retry_corrected",
    ("schema_error", "*", 1): "escalate",

    ("tool_error", "tool_error.timeout", 0): "retry",
    ("tool_error", "tool_error.timeout", 1): "degrade",
    ("tool_error", "tool_error.rate_limit", 0): "retry_backoff",
    ("tool_error", "tool_error.auth", 0): "escalate",
    ("tool_error", "tool_error.execution", 0): "retry",
    ("tool_error", "tool_error.execution", 1): "replan",
    ("tool_error", "tool_error.execution", 2): "escalate",
    ("tool_error", "tool_error.empty_result", 0): "replan",

    ("agent_loop", "agent_loop.identical", 0): "replan",
    ("agent_loop", "agent_loop.identical", 1): "escalate",
    ("agent_loop", "agent_loop.tool_cycling", 0): "replan",
}

_REPLAN_SUGGESTIONS: dict[str, list[str]] = {
    "agent_loop.identical": [
        "Modify the query or parameters before retrying.",
        "Use a different tool to accomplish the same goal.",
        "Return a partial result if the full result is unavailable.",
    ],
    "agent_loop.tool_cycling": [
        "Break the cycle by choosing a different approach.",
        "Consolidate the two tools into a single step.",
    ],
    "tool_error.empty_result": [
        "The data may not exist; consider reporting 'not found'.",
        "Try a broader or different query.",
    ],
    "tool_error.execution": [
        "Verify the tool parameters are correct.",
        "Try an alternative tool for the same intent.",
    ],
    "schema_error.drift": [
        "The tool API may have changed; try with simplified parameters.",
    ],
}

_REPLAN_TEMPLATE = """\
ORCHESTRATION RECOVERY NOTICE

The previous tool call failed or produced a loop condition. Details:
- Tool: {tool_id}
- Failure: {failure_sub_type}
- Attempt: {attempt_number}
- Error: {error_summary}

Suggested approaches:
{suggestions}

Please adapt your approach. Do not retry the same call with the same parameters."""


@dataclass
class RecoveryInstruction:
    action: str
    delay_ms: int = 0
    corrected_params: dict | None = None
    injection_message: str | None = None
    webhook_payload: dict | None = None
    emit_span: bool = True


class RecoveryEngine:
    """Selects and builds recovery instructions based on failure classification."""

    def __init__(self, max_retries: int = 2, enabled: bool = True) -> None:
        self.max_retries = max_retries
        self.enabled = enabled
        # Track injections for idempotency: (trace_id, step_index, sub_type) → count
        self._injections: dict[tuple, int] = {}

    def select_action(self, classification: FailureClassification, attempt: int = 0) -> str:
        fc = classification.failure_class
        st = classification.sub_type

        # Exact match first
        key = (fc, st, attempt)
        if key in _RECOVERY_MATRIX:
            return _RECOVERY_MATRIX[key]

        # Wildcard sub_type
        key_wild = (fc, "*", attempt)
        if key_wild in _RECOVERY_MATRIX:
            return _RECOVERY_MATRIX[key_wild]

        # Beyond max retries → escalate
        if attempt >= self.max_retries:
            return "escalate"

        return "replan"

    def build_instruction(
        self,
        classification: FailureClassification,
        attempt: int = 0,
        tool_id: str = "unknown",
        corrected_params: dict | None = None,
    ) -> RecoveryInstruction | None:
        if not self.enabled:
            return None

        # Idempotency check
        idem_key = (classification.trace_id, classification.step_index, classification.sub_type)
        if self._injections.get(idem_key, 0) > 0 and attempt == 0:
            return None
        self._injections[idem_key] = self._injections.get(idem_key, 0) + 1

        action = self.select_action(classification, attempt)

        if action in ("retry", "retry_corrected", "retry_backoff"):
            delay = 0
            if action == "retry_backoff":
                delay = min(1000 * (2 ** attempt), 30_000)
            return RecoveryInstruction(
                action=action, delay_ms=delay, corrected_params=corrected_params,
            )

        if action in ("replan", "degrade", "escalate"):
            suggestions = _REPLAN_SUGGESTIONS.get(classification.sub_type, ["Adapt your approach."])
            message = _REPLAN_TEMPLATE.format(
                tool_id=tool_id,
                failure_sub_type=classification.sub_type,
                attempt_number=attempt,
                error_summary=str(classification.evidence),
                suggestions="\n".join(f"- {s}" for s in suggestions),
            )
            return RecoveryInstruction(action=action, injection_message=message)

        return RecoveryInstruction(action=action)
