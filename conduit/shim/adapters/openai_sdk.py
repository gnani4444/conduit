"""OpenAI Agents SDK adapter — registers Conduit via add_trace_processor()."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def install_for_openai_sdk() -> None:
    """
    2-line install:
        from conduit.shim.adapters.openai_sdk import install_for_openai_sdk
        install_for_openai_sdk()
    """
    try:
        from agents import add_trace_processor  # type: ignore
    except ImportError:
        raise RuntimeError("OpenAI Agents SDK not installed. Run: pip install openai-agents")

    from conduit.shim.processor import ConduitProcessor
    processor = ConduitProcessor()

    class _OpenAITraceProcessor:
        def on_trace_start(self, trace):
            processor.on_agent_start(
                task_id=trace.trace_id,
                task_goal=str(getattr(trace, "metadata", {}).get("input", "")),
                framework="openai_sdk",
            )

        def on_span_start(self, span):
            if getattr(getattr(span, "span_data", None), "type", None) == "function":
                processor.pre_tool_hook(
                    tool_id=span.span_data.name,
                    params=span.span_data.input or {},
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    framework="openai_sdk",
                )

        def on_span_end(self, span):
            if getattr(getattr(span, "span_data", None), "type", None) == "function":
                processor.post_tool_hook(
                    tool_id=span.span_data.name,
                    outcome="success" if span.error is None else "tool_error",
                    result=getattr(span.span_data, "output", None),
                    error=span.error,
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    latency_ms=getattr(span, "duration_ms", 0.0),
                    framework="openai_sdk",
                )

        def on_trace_end(self, trace):
            processor.on_agent_end(
                task_id=trace.trace_id,
                outcome="success" if trace.error is None else "failure",
                framework="openai_sdk",
            )

    add_trace_processor(_OpenAITraceProcessor())
    logger.info("Conduit installed for OpenAI Agents SDK")
