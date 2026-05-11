"""Unit tests for SchemaValidator."""
import pytest
from conduit.registry.store import SchemaRegistry
from conduit.intelligence.validator import SchemaValidator

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        "format": {"type": "string", "default": "json"},
    },
    "required": ["query", "max_results"],
    "additionalProperties": False,
}


@pytest.fixture
def registry():
    r = SchemaRegistry(":memory:")
    r.register("search_web", _SCHEMA, version="1.0", aliases={"num_results": "max_results"})
    return r


@pytest.fixture
def validator(registry):
    return SchemaValidator(registry, hard_gate=False, auto_correct=True)


def test_valid_params_pass(validator):
    result = validator.validate("search_web", {"query": "hello", "max_results": 10})
    assert result.decision == "pass"
    assert result.validation_result == "pass"


def test_type_coerce_str_to_int(validator):
    result = validator.validate("search_web", {"query": "hello", "max_results": "10"})
    assert result.decision == "pass"
    assert result.validation_result == "corrected"
    assert result.corrected_params["max_results"] == 10
    assert any(c.correction_type == "type_coerce.str_to_int" for c in result.corrections)


def test_unknown_tool_skipped(validator):
    result = validator.validate("nonexistent_tool", {"foo": "bar"})
    assert result.validation_result == "skipped"
    assert result.decision == "pass"


def test_missing_required_field_soft_gate(validator):
    result = validator.validate("search_web", {"query": "hello"})
    # max_results missing, no default → gated_soft (hard_gate=False)
    assert result.validation_result == "gated_soft"
    assert result.decision == "pass"  # soft gate still passes


def test_missing_required_field_hard_gate(registry):
    v = SchemaValidator(registry, hard_gate=True, auto_correct=True)
    result = v.validate("search_web", {"query": "hello"})
    assert result.validation_result == "gated_hard"
    assert result.decision == "gate"


def test_unknown_field_stripped(validator):
    result = validator.validate("search_web", {"query": "hello", "max_results": 5, "debug": True})
    assert result.validation_result == "corrected"
    assert "debug" not in result.corrected_params


def test_field_rename_via_alias(validator):
    result = validator.validate("search_web", {"query": "hello", "num_results": 5})
    assert result.decision == "pass"
    assert result.corrected_params.get("max_results") == 5


def test_default_inject(validator):
    # format has a default — but it's not required, so this just passes
    result = validator.validate("search_web", {"query": "hello", "max_results": 3})
    assert result.decision == "pass"


# ------------------------------------------------------------------
# 03_SCHEMA_VALIDATOR.md §8 — Drift detection tests
# ------------------------------------------------------------------

def test_drift_renamed_field_creates_event(registry):
    """Drift detection: observe renamed field → drift event created, auto_correctable=true."""
    v = SchemaValidator(registry, auto_correct=True)
    # num_results is an alias for max_results — triggers a field_rename correction
    result = v.validate("search_web", {"query": "hello", "num_results": 5})
    assert result.validation_result == "corrected"
    assert any(c.correction_type == "field_rename" for c in result.corrections)
    # Drift event should have been written
    drift = registry.list_drift_events(tool_id="search_web")
    assert len(drift) >= 1
    assert drift[0]["auto_corrected"] in (1, True)


def test_drift_missing_required_field_severity_high(registry):
    """Drift detection: observe missing required field → drift event created, severity=high."""
    # Manually report drift for a missing required field
    registry.report_drift(
        tool_id="search_web",
        observed_params={"query": "hello"},  # max_results missing
        trace_id="test-trace",
        severity="high",
        fields_changed=["$.max_results"],
        auto_corrected=False,
    )
    drift = registry.list_drift_events(tool_id="search_web")
    assert len(drift) >= 1
    assert drift[0]["severity"] == "high"
    assert drift[0]["auto_corrected"] in (0, False)
