"""Unit tests for RecoveryEngine."""
import pytest
from conduit.intelligence.detector import FailureClassification
from conduit.intelligence.recovery import RecoveryEngine


def make_fc(failure_class="tool_error", sub_type="tool_error.execution",
            severity="high", trace_id="t1", step_index=1):
    return FailureClassification(
        failure_class=failure_class, sub_type=sub_type,
        severity=severity, trace_id=trace_id, step_index=step_index,
    )


def test_schema_error_attempt0_retry_corrected():
    e = RecoveryEngine()
    assert e.select_action(make_fc("schema_error", "schema_error.type_mismatch"), 0) == "retry_corrected"


def test_schema_error_attempt1_escalate():
    e = RecoveryEngine()
    assert e.select_action(make_fc("schema_error", "schema_error.type_mismatch"), 1) == "escalate"


def test_timeout_attempt0_retry():
    e = RecoveryEngine()
    assert e.select_action(make_fc("tool_error", "tool_error.timeout"), 0) == "retry"


def test_timeout_attempt1_degrade():
    e = RecoveryEngine()
    assert e.select_action(make_fc("tool_error", "tool_error.timeout"), 1) == "degrade"


def test_auth_error_immediate_escalate():
    e = RecoveryEngine()
    assert e.select_action(make_fc("tool_error", "tool_error.auth"), 0) == "escalate"


def test_loop_replan():
    e = RecoveryEngine()
    assert e.select_action(make_fc("agent_loop", "agent_loop.identical"), 0) == "replan"


def test_build_instruction_replan_has_message():
    e = RecoveryEngine()
    fc = make_fc("agent_loop", "agent_loop.identical")
    instr = e.build_instruction(fc, attempt=0, tool_id="search_web")
    assert instr is not None
    assert instr.action == "replan"
    assert instr.injection_message is not None
    assert "search_web" in instr.injection_message


def test_idempotency_second_injection_skipped():
    e = RecoveryEngine()
    fc = make_fc("agent_loop", "agent_loop.identical", trace_id="t99", step_index=5)
    instr1 = e.build_instruction(fc, attempt=0)
    instr2 = e.build_instruction(fc, attempt=0)  # same key
    assert instr1 is not None
    assert instr2 is None  # idempotent


def test_retry_backoff_delay():
    e = RecoveryEngine()
    fc = make_fc("tool_error", "tool_error.rate_limit")
    instr = e.build_instruction(fc, attempt=0)
    assert instr.action == "retry_backoff"
    assert instr.delay_ms >= 0
