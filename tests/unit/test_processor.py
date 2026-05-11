"""Unit tests for ConduitProcessor."""
import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Point conduit config to a temp db so tests don't write to ./conduit.db."""
    import conduit.config as cfg_mod
    cfg_mod._config = None  # reset singleton
    monkeypatch.setenv("CONDUIT_DB_PATH", str(tmp_path / "test.db"))
    yield
    cfg_mod._config = None


def make_mock_span(operation: str = "execute_tool", tool_name: str = "search_web"):
    span = MagicMock()
    _attrs = {
        "gen_ai.operation.name": operation,
        "gen_ai.tool.name": tool_name,
    }
    span.attributes = _attrs
    span.context = MagicMock()
    span.context.trace_id = 0
    span.status = MagicMock()
    span.status.status_code = MagicMock()
    span.status.status_code.name = "OK"

    def set_attribute(k, v):
        _attrs[k] = v

    span.set_attribute.side_effect = set_attribute
    return span


def test_processor_intercepts_tool_span():
    from conduit.shim.processor import ConduitProcessor
    processor = ConduitProcessor()
    span = make_mock_span(operation="execute_tool", tool_name="search_web")
    processor.on_start(span)
    assert span.attributes["conduit.hook_phase"] == "pre"


def test_processor_ignores_non_tool_spans():
    from conduit.shim.processor import ConduitProcessor
    processor = ConduitProcessor()
    span = make_mock_span(operation="chat")
    processor.on_start(span)
    assert "conduit.hook_phase" not in span.attributes


def test_processor_sets_validation_skipped_without_validator():
    from conduit.shim.processor import ConduitProcessor
    processor = ConduitProcessor()
    span = make_mock_span()
    processor.on_start(span)
    # No schema registered → skipped
    assert span.attributes.get("conduit.validation.result") == "skipped"


def test_processor_on_start_never_raises():
    from conduit.shim.processor import ConduitProcessor
    processor = ConduitProcessor()
    bad_span = MagicMock()
    bad_span.attributes = None
    bad_span.context = None
    # Must not raise
    processor.on_start(bad_span)
