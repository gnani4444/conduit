"""LangGraph adapter — registers ConduitProcessor into the active OTel tracer provider."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def install_for_langgraph(tracer_provider=None) -> None:
    """
    2-line install:
        from conduit.shim.adapters.langgraph import install_for_langgraph
        install_for_langgraph()
    """
    from opentelemetry import trace as otel_trace
    from conduit.shim.processor import ConduitProcessor

    provider = tracer_provider or otel_trace.get_tracer_provider()

    if not hasattr(provider, "add_span_processor"):
        raise RuntimeError(
            "No OTel TracerProvider with add_span_processor found. "
            "Ensure LangSmith tracing is configured before calling install_for_langgraph()."
        )

    processor = ConduitProcessor()
    provider.add_span_processor(processor)
    _register_langgraph_callbacks(processor)
    logger.info("Conduit installed for LangGraph")


def _register_langgraph_callbacks(processor) -> None:
    try:
        from langgraph.callbacks import BaseCallbackHandler  # type: ignore

        class _ConduitCallback(BaseCallbackHandler):
            def on_chain_start(self, serialized, inputs, **kwargs):
                processor.on_agent_start(
                    task_id=str(kwargs.get("run_id", "")),
                    task_goal=str(inputs.get("messages", inputs)),
                    framework="langgraph",
                )

            def on_chain_end(self, outputs, **kwargs):
                processor.on_agent_end(
                    task_id=str(kwargs.get("run_id", "")),
                    outcome="success",
                    framework="langgraph",
                )

            def on_chain_error(self, error, **kwargs):
                processor.on_agent_error(
                    task_id=str(kwargs.get("run_id", "")),
                    error=error,
                    framework="langgraph",
                )

        import langgraph.callbacks as _lg  # type: ignore
        if hasattr(_lg, "_default_callbacks"):
            _lg._default_callbacks.append(_ConduitCallback())
    except ImportError:
        pass  # LangGraph not installed; OTel-only integration still works
