"""Telemetry collector — launches otelcol and provides the /ingest HTTP endpoint."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_OTELCOL_CONFIG_TEMPLATE = """\
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"
      http:
        endpoint: "0.0.0.0:4318"

processors:
  attributes/redact_params:
    actions:
      - key: "conduit.params.raw"
        action: delete
      - key: "conduit.result.raw"
        action: delete
  resource:
    attributes:
      - key: "conduit.version"
        value: "0.1.0"
        action: insert
  batch:
    timeout: 200ms
    send_batch_size: 512

exporters:
  otlphttp/conduit:
    endpoint: "http://localhost:7431"
    tls:
      insecure: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [attributes/redact_params, resource, batch]
      exporters: [otlphttp/conduit]
"""


class OtelCollector:
    """Manages a local otelcol process."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._config_file: str | None = None

    def start(self) -> bool:
        """Start otelcol. Returns True if started successfully."""
        otelcol = self._find_otelcol()
        if not otelcol:
            logger.warning("otelcol not found in PATH — telemetry collection disabled")
            return False

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        tmp.write(_OTELCOL_CONFIG_TEMPLATE)
        tmp.close()
        self._config_file = tmp.name

        try:
            self._proc = subprocess.Popen(
                [otelcol, "--config", self._config_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("otelcol started (pid=%d)", self._proc.pid)
            return True
        except Exception as e:
            logger.warning("Failed to start otelcol: %s", e)
            return False

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            self._proc = None
        if self._config_file:
            Path(self._config_file).unlink(missing_ok=True)
            self._config_file = None

    def _find_otelcol(self) -> str | None:
        import shutil
        for name in ("otelcol", "otelcol-contrib"):
            path = shutil.which(name)
            if path:
                return path
        return None


# FastAPI ingest endpoint (mounted by dashboard app)
def make_ingest_router():
    from fastapi import APIRouter, Request
    from conduit.store.events import ToolCallEvent, save_event
    from datetime import datetime, timezone
    import hashlib

    router = APIRouter()

    @router.post("/ingest")
    async def ingest(request: Request):
        """Receive spans from otelcol and persist to SQLite."""
        try:
            body = await request.body()
            data = json.loads(body) if body else {}
            # Parse OTLP JSON export format (simplified)
            for resource_span in data.get("resourceSpans", []):
                for scope_span in resource_span.get("scopeSpans", []):
                    for span in scope_span.get("spans", []):
                        attrs = {a["key"]: a.get("value", {}) for a in span.get("attributes", [])}
                        op = _attr_str(attrs.get("gen_ai.operation.name"))
                        if op != "execute_tool":
                            continue
                        event = ToolCallEvent(
                            tool_id=_attr_str(attrs.get("gen_ai.tool.name")) or "unknown",
                            trace_id=span.get("traceId", ""),
                            span_id=span.get("spanId", ""),
                            framework=_attr_str(attrs.get("gen_ai.system")) or "unknown",
                            outcome="success" if span.get("status", {}).get("code") != 2 else "tool_error",
                            validation_result=_attr_str(attrs.get("conduit.validation.result")) or "skipped",
                            failure_class=_attr_str(attrs.get("conduit.failure.class")),
                            params_hash=hashlib.sha256(b"{}").hexdigest(),
                            created_at=datetime.now(timezone.utc),
                        )
                        save_event(event)
        except Exception as e:
            logger.warning("ingest error: %s", e)
        return {"ok": True}

    return router


def _attr_str(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return val.get("stringValue") or val.get("intValue") or val.get("boolValue")
