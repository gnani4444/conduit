"""Unit tests for ToolFailureDetector and AgentLoopDetector."""
import pytest
from conduit.intelligence.detector import (
    ToolFailureDetector, AgentLoopDetector, ToolCallSpan
)


def make_span(tool="search_web", params=None, outcome="success",
              exception_type=None, http_status=None, result=None):
    return ToolCallSpan(
        tool_id=tool,
        outcome=outcome,
        params=params or {"q": "X"},
        exception_type=exception_type,
        http_status=http_status,
        result=result,
    )


# ---- ToolFailureDetector ----

def test_success_returns_none():
    d = ToolFailureDetector()
    assert d.classify(make_span(outcome="success")) is None


def test_timeout_classified():
    d = ToolFailureDetector()
    r = d.classify(make_span(outcome="error", exception_type="TimeoutError"))
    assert r.sub_type == "tool_error.timeout"
    assert r.severity == "high"


def test_auth_classified():
    d = ToolFailureDetector()
    r = d.classify(make_span(outcome="error", exception_type="AuthError"))
    assert r.sub_type == "tool_error.auth"
    assert r.severity == "critical"


def test_rate_limit_classified():
    d = ToolFailureDetector()
    r = d.classify(make_span(outcome="error", http_status=429))
    assert r.sub_type == "tool_error.rate_limit"


def test_empty_result_classified():
    d = ToolFailureDetector()
    r = d.classify(make_span(outcome="error", result=None))
    assert r.sub_type == "tool_error.empty_result"
    assert r.severity == "low"


# ---- AgentLoopDetector ----

def test_loop_detector_fires_at_threshold():
    d = AgentLoopDetector(window_size=10, threshold=3)
    assert d.check(make_span()) is None   # call 1
    assert d.check(make_span()) is None   # call 2
    result = d.check(make_span())          # call 3
    assert result is not None
    assert result.failure_class == "agent_loop"
    assert result.sub_type == "agent_loop.identical"


def test_loop_detector_different_params_no_fire():
    d = AgentLoopDetector(threshold=3)
    d.check(make_span(params={"q": "A"}))
    d.check(make_span(params={"q": "B"}))
    result = d.check(make_span(params={"q": "C"}))
    assert result is None


def test_loop_detector_resets_between_tasks():
    d = AgentLoopDetector(threshold=3)
    d.check(make_span())
    d.check(make_span())
    d.reset()
    d.check(make_span())
    d.check(make_span())
    result = d.check(make_span())
    assert result is not None  # counter reset → fires at 3 again


def test_tool_cycling_detected():
    d = AgentLoopDetector(threshold=10)  # high threshold so identical loop doesn't fire
    d.check(make_span(tool="tool_a", params={"x": 1}))
    d.check(make_span(tool="tool_b", params={"x": 2}))
    d.check(make_span(tool="tool_a", params={"x": 1}))
    result = d.check(make_span(tool="tool_b", params={"x": 2}))
    assert result is not None
    assert result.sub_type == "agent_loop.tool_cycling"
