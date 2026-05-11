"""CrewAI adapter — hooks into CrewAI event system (>= 0.80.0)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def install_for_crewai(crew_or_flow=None) -> None:
    """
    Install:
        from conduit.shim.adapters.crewai import install_for_crewai
        install_for_crewai(crew)
    """
    from conduit.shim.processor import ConduitProcessor
    processor = ConduitProcessor()

    try:
        from crewai.utilities.events import (  # type: ignore
            on_tool_usage_started,
            on_tool_usage_finished,
            on_tool_usage_error,
            on_crew_started,
            on_crew_finished,
        )

        @on_tool_usage_started
        def _tool_start(source, event):
            processor.pre_tool_hook(
                tool_id=event.tool_name,
                params=event.tool_input or {},
                trace_id=str(id(source)),
                framework="crewai",
            )

        @on_tool_usage_finished
        def _tool_finish(source, event):
            processor.post_tool_hook(
                tool_id=event.tool_name,
                outcome="success",
                result=event.tool_output,
                trace_id=str(id(source)),
                framework="crewai",
            )

        @on_tool_usage_error
        def _tool_error(source, event):
            processor.post_tool_hook(
                tool_id=event.tool_name,
                outcome="tool_error",
                error=str(event.error),
                trace_id=str(id(source)),
                framework="crewai",
            )

        @on_crew_started
        def _crew_start(source, event):
            processor.on_agent_start(
                task_id=str(id(source)),
                task_goal=str(getattr(event, "inputs", "") or ""),
                framework="crewai",
            )

        @on_crew_finished
        def _crew_finish(source, event):
            processor.on_agent_end(task_id=str(id(source)), outcome="success", framework="crewai")

        logger.info("Conduit installed for CrewAI (>= 0.80.0)")

    except ImportError:
        if crew_or_flow is not None:
            _install_crewai_legacy(crew_or_flow, processor)
        else:
            raise RuntimeError("CrewAI not installed or version < 0.80.0. Pass crew object for legacy support.")


def _install_crewai_legacy(crew, processor) -> None:
    """Minimal fallback for CrewAI < 0.80.0."""
    original = getattr(crew, "_execute_task", None)
    if original is None:
        logger.warning("CrewAI legacy: _execute_task not found, skipping instrumentation")
        return

    def _patched(task, agent, context):
        processor.pre_tool_hook(tool_id=str(task), params={}, framework="crewai_legacy")
        result = original(task, agent, context)
        processor.post_tool_hook(tool_id=str(task), outcome="success", result=result, framework="crewai_legacy")
        return result

    crew._execute_task = _patched
    logger.info("Conduit installed for CrewAI (legacy)")
