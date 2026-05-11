"""Performance benchmarks — 02_INTERCEPTION_SHIM.md §8.

Contracts:
- 1,000 pass-through calls (plane healthy, all pass): p99 < 3ms
- 1,000 calls with auto-correction: p99 < 6ms
- 1,000 calls with plane down: p99 < 0.5ms

07_IMPLEMENTATION_GUIDE.md §5:
- p99 shim overhead < 5ms (with plane healthy)
- p99 shim overhead < 0.5ms (with plane down, pass-through)
"""
import time
import statistics
import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import conduit.config as cfg_mod
    cfg_mod._config = None
    monkeypatch.setenv("CONDUIT_DB_PATH", str(tmp_path / "bench.db"))
    from conduit.store.events import set_db_path
    set_db_path(str(tmp_path / "bench.db"))
    yield
    cfg_mod._config = None


def _p99(times: list[float]) -> float:
    return statistics.quantiles(times, n=100)[98]  # 99th percentile


def test_p99_pass_through_healthy_plane_under_5ms(tmp_path):
    """1,000 calls, schema registered, params valid → p99 < 5ms. §8 + §5 DoD."""
    from conduit.shim.processor import ConduitProcessor
    from conduit.registry.store import SchemaRegistry
    import os

    db = str(tmp_path / "bench.db")
    os.environ["CONDUIT_DB_PATH"] = db

    import conduit.config as cfg_mod
    cfg_mod._config = None

    p = ConduitProcessor()
    p._registry.register("search_web", {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
        "required": ["query", "max_results"],
    }, version="1.0")

    params = {"query": "hello", "max_results": 10}
    times = []
    N = 1000

    for i in range(N):
        t0 = time.perf_counter()
        p.pre_tool_hook("search_web", params, trace_id=f"t{i}")
        times.append((time.perf_counter() - t0) * 1000)

    p99 = _p99(times)
    avg = statistics.mean(times)
    print(f"\n  pass-through healthy: avg={avg:.3f}ms  p99={p99:.3f}ms  (limit=5ms)")
    assert p99 < 5.0, f"p99={p99:.2f}ms exceeds 5ms limit"


def test_p99_with_autocorrection_under_6ms(tmp_path):
    """1,000 calls with type mismatch → auto-corrected → p99 < 6ms. §8."""
    import os
    db = str(tmp_path / "bench2.db")
    os.environ["CONDUIT_DB_PATH"] = db

    import conduit.config as cfg_mod
    cfg_mod._config = None

    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()
    p._registry.register("search_web", {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
        "required": ["query", "max_results"],
    }, version="1.0")

    params = {"query": "hello", "max_results": "10"}  # string → needs coercion
    times = []

    for i in range(1000):
        t0 = time.perf_counter()
        p.pre_tool_hook("search_web", params, trace_id=f"t{i}")
        times.append((time.perf_counter() - t0) * 1000)

    p99 = _p99(times)
    avg = statistics.mean(times)
    print(f"\n  auto-correction: avg={avg:.3f}ms  p99={p99:.3f}ms  (limit=6ms)")
    assert p99 < 6.0, f"p99={p99:.2f}ms exceeds 6ms limit"


def test_p99_plane_down_under_0_5ms(tmp_path):
    """1,000 calls with plane down (validator raises) → p99 < 0.5ms. §5 DoD."""
    import os
    from unittest.mock import patch
    db = str(tmp_path / "bench3.db")
    os.environ["CONDUIT_DB_PATH"] = db

    import conduit.config as cfg_mod
    cfg_mod._config = None

    from conduit.shim.processor import ConduitProcessor
    p = ConduitProcessor()

    params = {"query": "hello"}
    times = []

    with patch.object(p._validator, "validate", side_effect=ConnectionError("plane down")):
        for i in range(1000):
            t0 = time.perf_counter()
            p.pre_tool_hook("search_web", params, trace_id=f"t{i}")
            times.append((time.perf_counter() - t0) * 1000)

    p99 = _p99(times)
    avg = statistics.mean(times)
    print(f"\n  plane down pass-through: avg={avg:.3f}ms  p99={p99:.3f}ms  (limit=0.5ms)")
    assert p99 < 0.5, f"p99={p99:.3f}ms exceeds 0.5ms limit"
