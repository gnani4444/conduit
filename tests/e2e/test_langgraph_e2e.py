"""E2E adapter tests — 07_IMPLEMENTATION_GUIDE.md §7.

Tests the full pipeline end-to-end using mock agents that simulate
LangGraph and OpenAI SDK behaviour via the adapter hook API.
No real framework install required.
"""
import pytest
import hashlib
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import conduit.config as cfg_mod
    cfg_mod._config = None
    monkeypatch.setenv("CONDUIT_DB_PATH", str(tmp_path / "e2e.db"))
    from conduit.store.events import set_db_path
    set_db_path(str(tmp_path / "e2e.db"))
    yield
    cfg_mod._config = None


def _make_processor_with_schema(db_path: str):
    """Create a ConduitProcessor with search_web schema registered."""
    import os
    os.environ["CONDUIT_DB_PATH"] = db_path
    import conduit.config as cfg_mod
    cfg_mod._config = None
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()
    p._registry.register("search_web", {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query", "max_results"],
    }, version="1.0")
    return p


# ── Scenario 1: Schema validation auto-correct ───────────────────

def test_schema_validation_auto_correct_e2e(tmp_path):
    """
    Mock agent calls search_web with max_results='5' (string).
    Conduit intercepts → validator corrects to int 5 → event in DB.
    07_IMPLEMENTATION_GUIDE.md §7 scenario 1.
    """
    from conduit.store.events import query_events

    p = _make_processor_with_schema(str(tmp_path / "e2e.db"))
    p.on_agent_start("trace-1", task_goal="search for news", framework="mock_langgraph")

    # Agent calls pre_tool_hook with wrong type
    corrected = p.pre_tool_hook(
        "search_web",
        {"query": "AI news", "max_results": "5"},  # string — should be int
        trace_id="trace-1",
        framework="mock_langgraph",
    )

    # Conduit corrected the type
    assert corrected["max_results"] == 5
    assert isinstance(corrected["max_results"], int)

    # Agent executes tool with corrected params, reports success
    p.post_tool_hook("search_web", "success",
                     result=["result1", "result2"],
                     trace_id="trace-1", latency_ms=120.0,
                     framework="mock_langgraph")

    p.on_agent_end("trace-1", outcome="success", framework="mock_langgraph")

    # Event in DB with corrected validation result
    events = query_events(tool_id="search_web")
    assert len(events) >= 1


# ── Scenario 2: Loop detection → replan injected ─────────────────

def test_loop_detection_e2e(tmp_path):
    """
    Mock agent calls search_web 3 times with identical params.
    Conduit detects loop at call 3 → returns replan injection_message.
    07_IMPLEMENTATION_GUIDE.md §7 scenario 2.
    """
    from conduit.store.events import query_events

    p = _make_processor_with_schema(str(tmp_path / "e2e.db"))
    p.on_agent_start("trace-loop", task_goal="find data", framework="mock_openai_sdk")

    params = {"query": "X", "max_results": 10}
    injection = None

    for i in range(3):
        p.pre_tool_hook("search_web", params, trace_id="trace-loop")
        msg = p.post_tool_hook(
            "search_web", "tool_error",
            result=None,
            error=Exception("empty"),
            trace_id="trace-loop",
            latency_ms=100.0,
            framework="mock_openai_sdk",
        )
        if msg:
            injection = msg  # recovery injection_message

    # Loop detected at call 3 → replan message returned
    assert injection is not None, "Expected replan injection_message at call 3"
    assert "search_web" in injection
    assert "adapt" in injection.lower() or "loop" in injection.lower() or "retry" in injection.lower()

    # Loop event persisted
    events = query_events(failure_class="agent_loop")
    assert len(events) >= 1
    assert events[0]["failure_sub_type"] == "agent_loop.identical"

    p.on_agent_end("trace-loop", outcome="failure")


# ── Scenario 3: Pass-through when plane down ─────────────────────

def test_passthrough_when_plane_down_e2e(tmp_path):
    """
    Validator raises (plane down) → all tool calls pass through unmodified.
    No exception propagated to agent. Spans still emitted.
    07_IMPLEMENTATION_GUIDE.md §7 scenario 3.
    """
    from conduit.store.events import query_events

    p = _make_processor_with_schema(str(tmp_path / "e2e.db"))

    with patch.object(p._validator, "validate", side_effect=ConnectionError("plane down")):
        p.on_agent_start("trace-down", framework="mock_crewai")

        for i in range(3):
            original = {"query": f"query-{i}", "max_results": "10"}
            result = p.pre_tool_hook("search_web", original, trace_id="trace-down")
            # Must return original params unchanged
            assert result == original

            # post_tool_hook must not raise
            p.post_tool_hook("search_web", "success",
                             trace_id="trace-down", latency_ms=50.0,
                             framework="mock_crewai")

        p.on_agent_end("trace-down", outcome="success")

    # Events still persisted (tool name, outcome) even with plane down
    events = query_events(tool_id="search_web")
    assert len(events) == 3
    assert all(e["validation_result"] == "skipped" for e in events)


# ── Scenario 4: OpenAI SDK adapter hook flow ─────────────────────

def test_openai_sdk_adapter_hook_flow(tmp_path):
    """
    Simulate the OpenAI SDK adapter calling pre/post hooks as it would
    via on_span_start/on_span_end. Verifies the adapter contract from
    05_TELEMETRY_AND_ADAPTERS.md §4.2.
    """
    from conduit.store.events import query_events

    p = _make_processor_with_schema(str(tmp_path / "e2e.db"))

    # Simulate OpenAI SDK adapter flow
    p.on_agent_start("sdk-trace-1", task_goal="get weather", framework="openai_sdk")

    # on_span_start fires pre_tool_hook
    corrected = p.pre_tool_hook(
        "get_weather",
        {"location": "London", "units": "celsius"},
        trace_id="sdk-trace-1",
        span_id="span-1",
        framework="openai_sdk",
    )
    assert corrected is not None  # didn't raise

    # on_span_end fires post_tool_hook
    p.post_tool_hook(
        "get_weather",
        outcome="success",
        result="22°C, sunny",
        trace_id="sdk-trace-1",
        span_id="span-1",
        latency_ms=45.0,
        framework="openai_sdk",
    )

    p.on_agent_end("sdk-trace-1", outcome="success", framework="openai_sdk")

    events = query_events(tool_id="get_weather")
    assert len(events) == 1
    assert events[0]["outcome"] == "success"
    assert events[0]["framework"] == "openai_sdk"


# ── Scenario 5: around_tool_hook context manager ─────────────────

def test_around_tool_hook_captures_exception(tmp_path):
    """
    around_tool_hook wraps tool execution, captures exception,
    feeds into post-hook. 02_INTERCEPTION_SHIM.md §3.2.
    """
    from conduit.store.events import query_events

    p = _make_processor_with_schema(str(tmp_path / "e2e.db"))

    with pytest.raises(ValueError):
        with p.around_tool_hook("search_web", trace_id="trace-around") as ctx:
            raise ValueError("tool crashed")

    # Exception was captured in ctx
    assert ctx["exception"] is not None
    assert isinstance(ctx["exception"], ValueError)
    # Event persisted as tool_error
    events = query_events(tool_id="search_web")
    assert len(events) == 1
    assert events[0]["outcome"] == "tool_error"
