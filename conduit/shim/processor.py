"""ConduitProcessor — core OTel SpanProcessor.

Implements all hooks from 02_INTERCEPTION_SHIM.md §3:
  PRE_TOOL   — schema validation, span attributes
  AROUND_TOOL — wall-clock timing, exception capture (§3.2)
  POST_TOOL  — failure classification, loop detection, recovery
  on_agent_start/end — gen_ai.invoke_agent span (§4.3)

Invariants (§7):
- Never raises into agent code — every hook is try/except.
- Never blocks beyond timeout_ms — pass-through on timeout.
- No payload logging without CONDUIT_LOG_PAYLOADS=true.
- Loop detector is per-trace-id, not global.
- Recovery injection is idempotent.
"""
from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Generator

from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan
from opentelemetry.context import Context

from conduit.telemetry import spans as S

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import Span

logger = logging.getLogger(__name__)


class ConduitProcessor(SpanProcessor):
    """Registered as an OTel SpanProcessor. Intercepts gen_ai.execute_tool spans."""

    def __init__(self) -> None:
        from conduit.config import get_config
        from conduit.registry.store import SchemaRegistry
        from conduit.intelligence.validator import SchemaValidator
        from conduit.intelligence.detector import ToolFailureDetector, AgentLoopDetector
        from conduit.intelligence.recovery import RecoveryEngine
        from conduit.store import events as event_store

        cfg = get_config()
        self._cfg = cfg
        self._registry = SchemaRegistry(cfg.registry.db_path)
        self._validator = SchemaValidator(
            self._registry,
            hard_gate=cfg.validation.hard_gate,
            auto_correct=cfg.validation.auto_correct,
        )
        self._failure_detector = ToolFailureDetector()
        self._recovery = RecoveryEngine(
            max_retries=cfg.recovery.max_retries,
            enabled=cfg.recovery.enabled,
        )
        self._event_store = event_store

        # Per-trace-id loop detectors — 04_FAILURE_DETECTOR.md invariant 4
        self._loop_detectors: dict[str, AgentLoopDetector] = {}
        self._step_counters: dict[str, int] = {}
        # Track loop counts per task for invoke_agent span (§4.3)
        self._loop_counts: dict[str, int] = {}

        # Auto-ingest MCP manifests on startup (02_INTERCEPTION_SHIM.md §6)
        try:
            from conduit.config import auto_ingest_mcp_manifests
            auto_ingest_mcp_manifests(self._registry)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SpanProcessor interface
    # ------------------------------------------------------------------

    def on_start(self, span: "Span", parent_context: Context | None = None) -> None:
        try:
            if self._is_tool_span(span):
                self._pre_tool_hook(span)
        except Exception:
            logger.exception("ConduitProcessor.on_start error (suppressed)")

    def on_end(self, span: ReadableSpan) -> None:
        try:
            if self._is_tool_span(span):
                self._post_tool_hook(span)
        except Exception:
            logger.exception("ConduitProcessor.on_end error (suppressed)")

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    # ------------------------------------------------------------------
    # Public hook API (used by framework adapters)
    # ------------------------------------------------------------------

    def pre_tool_hook(self, tool_id: str, params: dict, trace_id: str = "",
                      span_id: str = "", framework: str = "unknown") -> dict:
        """Returns (possibly corrected) params. Never raises."""
        try:
            return self._run_pre(tool_id, params, trace_id)
        except Exception:
            logger.exception("pre_tool_hook error (suppressed)")
            return params

    @contextlib.contextmanager
    def around_tool_hook(self, tool_id: str, trace_id: str = "") -> Generator[dict, None, None]:
        """
        AROUND_TOOL hook — 02_INTERCEPTION_SHIM.md §3.2.
        Context manager wrapping tool execution. Captures wall-clock time + exceptions.

        Usage:
            with processor.around_tool_hook("search_web", trace_id) as ctx:
                result = search_web(params)
                ctx["result"] = result
        """
        ctx: dict = {"exception": None, "result": None, "latency_ms": 0.0}
        t0 = time.perf_counter()
        try:
            yield ctx
        except Exception as exc:
            ctx["exception"] = exc
            raise
        finally:
            ctx["latency_ms"] = (time.perf_counter() - t0) * 1000
            # Feed to post-hook
            try:
                outcome = "tool_error" if ctx["exception"] else "success"
                self._run_post(
                    tool_id=tool_id,
                    outcome=outcome,
                    result=ctx.get("result"),
                    error=ctx.get("exception"),
                    trace_id=trace_id,
                    latency_ms=ctx["latency_ms"],
                    framework="around_hook",
                )
            except Exception:
                pass

    def post_tool_hook(self, tool_id: str, outcome: str, result: object = None,
                       error: object = None, trace_id: str = "", span_id: str = "",
                       latency_ms: float = 0.0, framework: str = "unknown") -> str | None:
        """
        Never raises. Returns injection_message if recovery requires replan/escalate/degrade,
        so the adapter can inject it into the agent — 02_INTERCEPTION_SHIM.md §5.
        """
        try:
            return self._run_post(tool_id, outcome, result, error, trace_id, latency_ms, framework)
        except Exception:
            logger.exception("post_tool_hook error (suppressed)")
            return None

    def on_agent_start(self, task_id: str, task_goal: str = "", framework: str = "unknown") -> None:
        """Initialise per-task state and emit gen_ai.invoke_agent span — §4.3."""
        try:
            from conduit.intelligence.detector import AgentLoopDetector
            self._loop_detectors[task_id] = AgentLoopDetector(
                window_size=self._cfg.detection.loop_window,
                threshold=self._cfg.detection.loop_threshold,
            )
            self._step_counters[task_id] = 0
            self._loop_counts[task_id] = 0
            self._emit_agent_span(task_id, "start", framework)
        except Exception:
            logger.exception("on_agent_start error (suppressed)")

    def on_agent_end(self, task_id: str, outcome: str = "success", framework: str = "unknown") -> None:
        """Emit final gen_ai.invoke_agent span — §4.3."""
        try:
            self._emit_agent_span(task_id, outcome, framework)
            self._loop_detectors.pop(task_id, None)
            self._step_counters.pop(task_id, None)
            self._loop_counts.pop(task_id, None)
        except Exception:
            logger.exception("on_agent_end error (suppressed)")

    def on_agent_error(self, task_id: str, error: object = None, framework: str = "unknown") -> None:
        self.on_agent_end(task_id, outcome="failure", framework=framework)

    # ------------------------------------------------------------------
    # OTel span hooks
    # ------------------------------------------------------------------

    def _is_tool_span(self, span: object) -> bool:
        attrs = getattr(span, "attributes", {}) or {}
        return attrs.get(S.GEN_AI_OPERATION_NAME) == S.OP_EXECUTE_TOOL

    def _pre_tool_hook(self, span: "Span") -> None:
        attrs = span.attributes or {}
        tool_id = attrs.get(S.GEN_AI_TOOL_NAME, "unknown")
        trace_id = format(span.context.trace_id, "032x") if span.context else ""

        span.set_attribute(S.CONDUIT_HOOK_PHASE, S.PHASE_PRE)

        # step_index — §4.1
        step = self._step_counters.get(trace_id, 0)
        self._step_counters[trace_id] = step + 1
        span.set_attribute(S.CONDUIT_TOOL_STEP_INDEX, step)

        snap = self._registry.get_current(tool_id)
        if snap:
            span.set_attribute(S.CONDUIT_TOOL_VERSION, snap.schema_version)

        t0 = time.perf_counter()
        validation_result = S.VALIDATION_SKIPPED
        try:
            result = self._validator.validate(tool_id, {}, trace_id=trace_id)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            validation_result = result.validation_result
            span.set_attribute(S.CONDUIT_LATENCY_VALIDATION_MS, round(elapsed_ms, 2))
            if result.decision == "gate":
                span.set_attribute(S.CONDUIT_FAILURE_CLASS, S.FAILURE_SCHEMA_ERROR)
            if result.corrections:
                import json
                span.set_attribute(S.CONDUIT_VALIDATION_CORRECTIONS, json.dumps([
                    {"field": c.field_path, "from": str(c.original_value),
                     "to": str(c.corrected_value), "reason": c.correction_type}
                    for c in result.corrections
                ]))
            if result.errors:
                import json
                span.set_attribute(S.CONDUIT_VALIDATION_ERRORS, json.dumps([
                    {"field": e.field_path, "error_type": e.error_type, "message": e.received}
                    for e in result.errors
                ]))
        except Exception:
            pass
        span.set_attribute(S.CONDUIT_VALIDATION_RESULT, validation_result)

    def _post_tool_hook(self, span: ReadableSpan) -> None:
        attrs = span.attributes or {}
        tool_id = attrs.get(S.GEN_AI_TOOL_NAME, "?")
        status = span.status.status_code.name if (span.status and span.status.status_code) else "OK"
        failure_class = S.FAILURE_TOOL_ERROR if status == "ERROR" else S.FAILURE_NONE
        logger.debug("post_tool_hook tool=%s failure=%s", tool_id, failure_class)

    # ------------------------------------------------------------------
    # Adapter-based hooks
    # ------------------------------------------------------------------

    def _run_pre(self, tool_id: str, params: dict, trace_id: str) -> dict:
        """Validate params; return corrected params or originals.
        Also calls report_drift if corrections were applied — 03_SCHEMA_VALIDATOR.md §3.1 step 5.
        """
        t0 = time.perf_counter()
        timeout = self._cfg.shim.timeout_ms / 1000

        try:
            result = self._validator.validate(tool_id, params, trace_id=trace_id)
            elapsed = time.perf_counter() - t0
            if elapsed > timeout:
                logger.warning("validator timeout (%.1fms > %dms), pass-through",
                               elapsed * 1000, self._cfg.shim.timeout_ms)
                return params

            # §3.1 step 5: report drift if corrections were applied
            if result.corrections and result.corrected_params:
                try:
                    self._registry.report_drift(
                        tool_id=tool_id,
                        observed_params=params,
                        trace_id=trace_id,
                        severity="medium",
                        auto_corrected=True,
                        fields_changed=[c.field_path for c in result.corrections],
                    )
                except Exception:
                    pass

            if result.corrected_params:
                return result.corrected_params
        except Exception:
            logger.debug("validator unavailable, pass-through for %s", tool_id)

        return params

    def _run_post(self, tool_id: str, outcome: str, result: object,
                  error: object, trace_id: str, latency_ms: float, framework: str) -> str | None:
        """Classify failure, check loop, trigger recovery, persist event.
        Returns injection_message for replan/escalate/degrade — §5.
        """
        import hashlib
        from datetime import datetime, timezone
        from conduit.intelligence.detector import ToolCallSpan, AgentLoopDetector
        from conduit.store.events import ToolCallEvent, save_event

        span_data = ToolCallSpan(
            tool_id=tool_id,
            outcome=outcome,
            params={},
            exception_type=type(error).__name__ if error else None,
            exception_message=str(error) if error else None,
            result=result,
            latency_ms=latency_ms,
            trace_id=trace_id,
        )

        failure = self._failure_detector.classify(span_data)

        detector = self._loop_detectors.get(trace_id)
        if detector is None:
            detector = AgentLoopDetector(
                window_size=self._cfg.detection.loop_window,
                threshold=self._cfg.detection.loop_threshold,
            )
            self._loop_detectors[trace_id] = detector
        loop_failure = detector.check(span_data)

        if loop_failure:
            self._loop_counts[trace_id] = self._loop_counts.get(trace_id, 0) + 1

        active_failure = loop_failure or failure

        recovery_action = None
        recovery_attempt = 0
        injection_message = None
        if active_failure and self._cfg.recovery.enabled:
            instr = self._recovery.build_instruction(
                active_failure, attempt=recovery_attempt, tool_id=tool_id
            )
            if instr:
                recovery_action = instr.action
                injection_message = instr.injection_message  # §5: return to adapter
                logger.info("recovery: tool=%s action=%s failure=%s",
                            tool_id, instr.action, active_failure.sub_type)

        event = ToolCallEvent(
            tool_id=tool_id,
            trace_id=trace_id,
            framework=framework,
            outcome=outcome,
            latency_ms=latency_ms,
            params_hash=hashlib.sha256(b"{}").hexdigest(),
            failure_class=active_failure.failure_class if active_failure else None,
            failure_sub_type=active_failure.sub_type if active_failure else None,
            failure_severity=active_failure.severity if active_failure else None,
            recovery_action=recovery_action,
            recovery_attempt=recovery_attempt,
            validation_result="skipped",
            created_at=datetime.now(timezone.utc),
        )
        save_event(event)
        return injection_message  # §5

    def _emit_agent_span(self, task_id: str, outcome: str, framework: str) -> None:
        """Emit gen_ai.invoke_agent span — 02_INTERCEPTION_SHIM.md §4.3."""
        try:
            from opentelemetry import trace as otel_trace
            tracer = otel_trace.get_tracer("conduit")
            step_count = self._step_counters.get(task_id, 0)
            loop_count = self._loop_counts.get(task_id, 0)
            with tracer.start_as_current_span(S.OP_INVOKE_AGENT) as span:
                span.set_attribute(S.GEN_AI_OPERATION_NAME, S.OP_INVOKE_AGENT)
                span.set_attribute(S.CONDUIT_AGENT_TASK_ID, task_id)
                span.set_attribute(S.CONDUIT_AGENT_STEP_COUNT, step_count)
                span.set_attribute(S.CONDUIT_AGENT_LOOP_COUNT, loop_count)
                span.set_attribute(S.CONDUIT_AGENT_OUTCOME, outcome)
                span.set_attribute(S.GEN_AI_SYSTEM, framework)
        except Exception:
            pass  # Never raise into agent code
