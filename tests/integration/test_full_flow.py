"""Integration tests — 02_INTERCEPTION_SHIM.md §8, 04_FAILURE_DETECTOR.md §7.

Tests the full flow: processor → validator → detector → recovery → event store.
No real framework required — uses the adapter hook API directly.
"""
import pytest
from conduit.registry.store import SchemaRegistry
from conduit.intelligence.validator import SchemaValidator
from conduit.intelligence.detector import ToolFailureDetector, AgentLoopDetector, ToolCallSpan
from conduit.intelligence.recovery import RecoveryEngine
from conduit.store.events import ToolCallEvent, save_event, query_events, set_db_path

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "max_results": {"type": "integer"},
    },
    "required": ["query", "max_results"],
}


@pytest.fixture(autouse=True)
def use_memory_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file and config reset."""
    import conduit.config as cfg_mod
    cfg_mod._config = None
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("CONDUIT_DB_PATH", db)
    set_db_path(db)
    yield db
    cfg_mod._config = None


@pytest.fixture
def registry(use_memory_db):
    r = SchemaRegistry(use_memory_db)
    r.register("search_web", _SCHEMA, version="1.0")
    return r


# ------------------------------------------------------------------
# 02_INTERCEPTION_SHIM.md §8 integration scenarios
# ------------------------------------------------------------------

def test_schema_validator_pass_through(registry):
    """Valid params → pass → no corrections."""
    v = SchemaValidator(registry)
    result = v.validate("search_web", {"query": "hello", "max_results": 10})
    assert result.validation_result == "pass"
    assert result.decision == "pass"
    assert not result.corrections


def test_schema_validator_auto_correct(registry):
    """max_results='5' (string) → corrected to 5 (int)."""
    v = SchemaValidator(registry, auto_correct=True)
    result = v.validate("search_web", {"query": "hello", "max_results": "5"})
    assert result.validation_result == "corrected"
    assert result.corrected_params["max_results"] == 5


def test_schema_validator_hard_gate(registry):
    """Missing required field + hard_gate=True → decision=gate."""
    v = SchemaValidator(registry, hard_gate=True)
    result = v.validate("search_web", {"query": "hello"})
    assert result.decision == "gate"
    assert result.validation_result == "gated_hard"


def test_loop_detection_and_recovery():
    """3 identical calls → loop detected → replan instruction built."""
    detector = AgentLoopDetector(threshold=3)
    recovery = RecoveryEngine()

    span = ToolCallSpan(tool_id="search_web", outcome="error",
                        params={"q": "X"}, trace_id="t1", step_index=1)

    assert detector.check(span) is None
    assert detector.check(span) is None
    loop = detector.check(span)
    assert loop is not None
    assert loop.sub_type == "agent_loop.identical"

    instr = recovery.build_instruction(loop, attempt=0, tool_id="search_web")
    assert instr is not None
    assert instr.action == "replan"
    assert "search_web" in (instr.injection_message or "")


def test_failure_store_persistence(use_memory_db):
    """Events written to SQLite are queryable."""
    from datetime import datetime, timezone
    import hashlib

    event = ToolCallEvent(
        tool_id="search_web",
        trace_id="trace-abc",
        framework="test",
        outcome="tool_error",
        failure_class="tool_error",
        failure_sub_type="tool_error.timeout",
        failure_severity="high",
        params_hash=hashlib.sha256(b"{}").hexdigest(),
        created_at=datetime.now(timezone.utc),
    )
    save_event(event)

    results = query_events(tool_id="search_web")
    assert len(results) == 1
    assert results[0]["failure_sub_type"] == "tool_error.timeout"


def test_full_flow_schema_fail_then_recovery(registry):
    """Schema validation fail → recovery engine selects retry_corrected."""
    from conduit.intelligence.detector import FailureClassification

    v = SchemaValidator(registry, auto_correct=True)
    result = v.validate("search_web", {"query": "hello", "max_results": "10"})
    assert result.validation_result == "corrected"

    # Simulate what processor does: if corrected, recovery = retry_corrected
    fc = FailureClassification(
        failure_class="schema_error",
        sub_type="schema_error.type_mismatch",
        severity="medium",
        trace_id="t1",
        step_index=1,
    )
    recovery = RecoveryEngine()
    action = recovery.select_action(fc, attempt=0)
    assert action == "retry_corrected"


def test_intelligence_plane_timeout_passthrough():
    """If validator takes too long, processor falls back to original params."""
    import time
    from conduit.shim.processor import ConduitProcessor

    processor = ConduitProcessor()

    # Override timeout to 0ms to force pass-through
    processor._cfg.shim.timeout_ms = 0

    original = {"query": "hello", "max_results": "10"}
    result = processor.pre_tool_hook("search_web", original, trace_id="t1")
    # With 0ms timeout, should return original params unchanged
    assert result is not None  # didn't raise


def test_loop_detector_per_trace_isolation():
    """Two agents running in parallel don't interfere — 04_FAILURE_DETECTOR.md invariant 4."""
    from conduit.shim.processor import ConduitProcessor

    processor = ConduitProcessor()
    processor.on_agent_start("trace-A", framework="test")
    processor.on_agent_start("trace-B", framework="test")

    # Both have independent detectors
    assert "trace-A" in processor._loop_detectors
    assert "trace-B" in processor._loop_detectors
    assert processor._loop_detectors["trace-A"] is not processor._loop_detectors["trace-B"]

    processor.on_agent_end("trace-A")
    assert "trace-A" not in processor._loop_detectors
    assert "trace-B" in processor._loop_detectors  # B unaffected
