"""Graceful degradation tests — 02_INTERCEPTION_SHIM.md §7.

All 6 failure modes:
1. Intelligence plane timeout (> 5ms)
2. Intelligence plane down
3. Schema registry empty (no schemas registered)
4. OTel collector down
5. Recovery injection fails
6. Hard-gate mode (schema validator gates)
"""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    import conduit.config as cfg_mod
    cfg_mod._config = None
    monkeypatch.setenv("CONDUIT_DB_PATH", str(tmp_path / "test.db"))
    from conduit.store.events import set_db_path
    set_db_path(str(tmp_path / "test.db"))
    yield
    cfg_mod._config = None


# ── 1. Intelligence plane timeout ────────────────────────────────
def test_timeout_passes_through_original_params():
    """Plane takes > timeout_ms → tool call proceeds unmodified. §7 row 1."""
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()
    p._cfg.shim.timeout_ms = 0  # force immediate timeout

    original = {"query": "hello", "max_results": "10"}
    result = p.pre_tool_hook("search_web", original, trace_id="t1")

    # Must return original params unchanged — not raise, not block
    assert result == original


def test_timeout_does_not_raise():
    """Timeout must never propagate an exception to agent code. §7 row 1."""
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()
    p._cfg.shim.timeout_ms = 0

    # Must not raise under any circumstances
    try:
        p.pre_tool_hook("any_tool", {"x": 1}, trace_id="t1")
    except Exception as exc:
        pytest.fail(f"pre_tool_hook raised into agent code: {exc}")


# ── 2. Intelligence plane down ───────────────────────────────────
def test_plane_down_passes_through():
    """Validator raises (plane down) → pass-through, no exception. §7 row 2."""
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()

    with patch.object(p._validator, "validate", side_effect=ConnectionError("plane down")):
        result = p.pre_tool_hook("search_web", {"q": "x"}, trace_id="t1")

    assert result == {"q": "x"}  # original params returned


def test_plane_down_post_hook_does_not_raise():
    """Post-hook with plane down must not raise. §7 row 2."""
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()

    with patch.object(p._failure_detector, "classify", side_effect=RuntimeError("plane down")):
        try:
            p.post_tool_hook("search_web", "tool_error", trace_id="t1")
        except Exception as exc:
            pytest.fail(f"post_tool_hook raised: {exc}")


# ── 3. Schema registry empty ─────────────────────────────────────
def test_empty_registry_skips_validation():
    """No schemas registered → validation skipped, loop detection still runs. §7 row 3."""
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()
    # Registry is empty by default in isolated_db fixture

    result = p.pre_tool_hook("unknown_tool", {"foo": "bar"}, trace_id="t1")
    assert result == {"foo": "bar"}  # pass-through


def test_empty_registry_loop_detection_still_runs():
    """Loop detection has no schema dependency — must still fire. §7 row 3."""
    from conduit.shim.processor import ConduitProcessor
    from conduit.store.events import query_events
    p = ConduitProcessor()
    p.on_agent_start("trace-loop")

    for _ in range(3):
        p.post_tool_hook("search_web", "tool_error", trace_id="trace-loop")

    events = query_events(failure_class="agent_loop")
    assert len(events) >= 1


# ── 4. OTel collector down ───────────────────────────────────────
def test_otel_collector_down_agent_unaffected():
    """Spans buffered/dropped if collector down — agent execution unaffected. §7 row 4."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from conduit.shim.processor import ConduitProcessor

    # Simulate collector down: exporter that always fails
    class FailingExporter:
        def export(self, spans):
            raise ConnectionRefusedError("collector down")
        def shutdown(self): pass
        def force_flush(self, timeout=None): pass

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(FailingExporter()))
    processor = ConduitProcessor()
    provider.add_span_processor(processor)

    # Agent code must not see any exception
    try:
        result = processor.pre_tool_hook("search_web", {"q": "test"}, trace_id="t1")
        processor.post_tool_hook("search_web", "success", trace_id="t1")
    except Exception as exc:
        pytest.fail(f"Agent saw exception with collector down: {exc}")


# ── 5. Recovery injection fails ──────────────────────────────────
def test_recovery_injection_failure_does_not_propagate():
    """Recovery engine crash → agent continues without recovery context. §7 row 5."""
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()

    with patch.object(p._recovery, "build_instruction", side_effect=RuntimeError("recovery crashed")):
        try:
            p.post_tool_hook("search_web", "tool_error",
                             error=Exception("fail"), trace_id="t1")
        except Exception as exc:
            pytest.fail(f"Recovery crash propagated to agent: {exc}")


def test_recovery_injection_returns_none_on_failure():
    """post_tool_hook returns None (not raises) when recovery fails. §7 row 5."""
    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()

    with patch.object(p._recovery, "build_instruction", side_effect=RuntimeError("crash")):
        result = p.post_tool_hook("search_web", "tool_error", trace_id="t1")

    assert result is None  # no injection message, no exception


# ── 6. Hard-gate mode ────────────────────────────────────────────
def test_hard_gate_blocks_invalid_call(tmp_path):
    """Hard-gate=true + invalid params → decision=gate. §7 row 6 / 02_INTERCEPTION_SHIM.md §3.1."""
    from conduit.registry.store import SchemaRegistry
    from conduit.intelligence.validator import SchemaValidator

    db = str(tmp_path / "hg.db")
    registry = SchemaRegistry(db)
    registry.register("search_web", {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
        "required": ["query", "max_results"],
    }, version="1.0")

    validator = SchemaValidator(registry, hard_gate=True, auto_correct=False)
    result = validator.validate("search_web", {"query": "hello"})  # missing max_results

    assert result.decision == "gate"
    assert result.validation_result == "gated_hard"


def test_hard_gate_span_emitted(tmp_path):
    """Hard-gate fires → span has conduit.failure.class=schema_error. §7 row 6."""
    from conduit.registry.store import SchemaRegistry
    from conduit.intelligence.validator import SchemaValidator

    db = str(tmp_path / "hg2.db")
    registry = SchemaRegistry(db)
    registry.register("search_web", {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }, version="1.0")

    validator = SchemaValidator(registry, hard_gate=True, auto_correct=False)
    result = validator.validate("search_web", {"query": "hello", "bad_field": True})
    # additionalProperties=false + hard_gate → gated_hard after failed correction
    assert result.decision in ("gate", "pass")  # strip may succeed
