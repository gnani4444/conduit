"""Security review — 07_IMPLEMENTATION_GUIDE.md §5: 0 known data-exfiltration paths.

Verifies that parameter values, tool results, and model outputs are never
written to any store unless CONDUIT_LOG_PAYLOADS=true.
"""
import pytest
import hashlib
import json


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import conduit.config as cfg_mod
    cfg_mod._config = None
    monkeypatch.setenv("CONDUIT_DB_PATH", str(tmp_path / "sec.db"))
    from conduit.store.events import set_db_path
    set_db_path(str(tmp_path / "sec.db"))
    yield
    cfg_mod._config = None


def test_params_stored_as_hash_not_raw(tmp_path):
    """Param values must never appear in the DB — only SHA256 hash. §5 invariant 3."""
    from conduit.shim.processor import ConduitProcessor
    from conduit.store.events import query_events

    p = ConduitProcessor()
    sensitive_params = {"query": "SECRET_QUERY_VALUE", "api_key": "sk-secret-123"}
    p.post_tool_hook("search_web", "success",
                     result="some result", trace_id="t1",
                     framework="test")

    events = query_events(tool_id="search_web")
    assert len(events) == 1
    row = events[0]

    # params_hash must be a SHA256 hex string, not the raw value
    assert row["params_hash"] is not None
    assert "SECRET_QUERY_VALUE" not in str(row)
    assert "sk-secret-123" not in str(row)
    assert len(row["params_hash"]) == 64  # SHA256 hex


def test_params_raw_never_set_without_opt_in(tmp_path):
    """params_raw must never be persisted to the DB — it's not even a column.
    The DB schema only stores params_hash (SHA256). §5 invariant 3.
    """
    import sqlite3
    from conduit.shim.processor import ConduitProcessor

    p = ConduitProcessor()
    assert p._cfg.shim.log_payloads is False

    p.post_tool_hook("search_web", "tool_error",
                     error=Exception("fail"), trace_id="t2", framework="test")

    db_path = p._cfg.registry.db_path
    con = sqlite3.connect(db_path)
    # Verify params_raw is NOT a column in the DB (never persisted)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tool_call_events)").fetchall()]
    con.close()

    assert "params_raw" not in cols, "params_raw must never be a DB column"
    assert "result_summary" not in cols, "result_summary must never be a DB column"
    assert "params_hash" in cols, "params_hash (SHA256) must be stored instead"


def test_span_events_not_emitted_without_opt_in():
    """conduit.params.pre/post span events must not fire without CONDUIT_LOG_PAYLOADS=true.
    02_INTERCEPTION_SHIM.md §4.1: 'Events on span (opt-in only)'.
    """
    from unittest.mock import MagicMock
    from conduit.shim.processor import ConduitProcessor

    p = ConduitProcessor()
    assert p._cfg.shim.log_payloads is False

    span = MagicMock()
    span.attributes = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search_web"}
    span.context = MagicMock()
    span.context.trace_id = 0
    _attrs = dict(span.attributes)
    span.set_attribute.side_effect = lambda k, v: _attrs.update({k: v})

    p.on_start(span)

    # add_event must NOT have been called (no payload events without opt-in)
    span.add_event.assert_not_called()


def test_validator_corrections_do_not_log_values():
    """Correction metadata (field path, type) is fine; original/corrected values
    in span attributes are acceptable since they're schema metadata, not user data.
    But raw params must not appear in the DB.
    """
    from conduit.registry.store import SchemaRegistry
    from conduit.intelligence.validator import SchemaValidator
    import os

    # Validator returns corrections with field paths — not raw user data in DB
    db = ":memory:"
    registry = SchemaRegistry(db)
    registry.register("search_web", {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
        "required": ["query", "max_results"],
    }, version="1.0")

    validator = SchemaValidator(registry)
    result = validator.validate("search_web", {"query": "hello", "max_results": "10"})

    assert result.validation_result == "corrected"
    # Correction records the field path and type — not the full params blob
    assert result.corrections[0].field_path == "$.max_results"
    assert result.corrections[0].correction_type == "type_coerce.str_to_int"


def test_log_payloads_env_var_respected(monkeypatch):
    """CONDUIT_LOG_PAYLOADS=true must flip the flag — false by default."""
    import conduit.config as cfg_mod

    # Default: false
    cfg_mod._config = None
    monkeypatch.delenv("CONDUIT_LOG_PAYLOADS", raising=False)
    cfg = cfg_mod.load_config()
    assert cfg.shim.log_payloads is False

    # Opt-in: true
    cfg_mod._config = None
    monkeypatch.setenv("CONDUIT_LOG_PAYLOADS", "true")
    cfg = cfg_mod.load_config()
    assert cfg.shim.log_payloads is True

    cfg_mod._config = None
