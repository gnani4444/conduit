"""Conduit configuration — loads conduit.yaml + environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


@dataclass
class ShimConfig:
    mode: str = "in_process"
    timeout_ms: int = 5
    fallback: str = "pass_through"
    log_payloads: bool = False


@dataclass
class ValidationConfig:
    hard_gate: bool = False
    auto_correct: bool = True
    correction_types: list[str] = field(default_factory=lambda: ["type_coerce", "field_rename", "optional_strip"])


@dataclass
class DetectionConfig:
    loop_threshold: int = 3
    loop_window: int = 10
    timeout_ms: int = 30000


@dataclass
class RecoveryConfig:
    enabled: bool = True
    actions: list[str] = field(default_factory=lambda: ["retry", "replan", "escalate"])
    max_retries: int = 2


@dataclass
class TelemetryConfig:
    otel_endpoint: str = "http://localhost:4317"
    service_name: str = "conduit-agent"


@dataclass
class RegistryConfig:
    db_path: str = "./conduit.db"
    mcp_manifest_paths: list[str] = field(default_factory=list)  # 02_INTERCEPTION_SHIM.md §6


@dataclass
class DashboardConfig:
    enabled: bool = True
    port: int = 7432
    host: str = "127.0.0.1"


@dataclass
class ConduitConfig:
    shim: ShimConfig = field(default_factory=ShimConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)


def load_config(path: str | None = None) -> ConduitConfig:
    cfg = ConduitConfig()
    config_path = Path(path or "conduit.yaml")

    if config_path.exists() and _HAS_YAML:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        if "shim" in data:
            cfg.shim = ShimConfig(**{k: v for k, v in data["shim"].items() if hasattr(ShimConfig, k)})
        if "validation" in data:
            cfg.validation = ValidationConfig(**{k: v for k, v in data["validation"].items() if hasattr(ValidationConfig, k)})
        if "detection" in data:
            cfg.detection = DetectionConfig(**{k: v for k, v in data["detection"].items() if hasattr(DetectionConfig, k)})
        if "recovery" in data:
            cfg.recovery = RecoveryConfig(**{k: v for k, v in data["recovery"].items() if hasattr(RecoveryConfig, k)})
        if "telemetry" in data:
            cfg.telemetry = TelemetryConfig(**{k: v for k, v in data["telemetry"].items() if hasattr(TelemetryConfig, k)})
        if "registry" in data:
            cfg.registry = RegistryConfig(**{k: v for k, v in data["registry"].items() if hasattr(RegistryConfig, k)})
        if "dashboard" in data:
            cfg.dashboard = DashboardConfig(**{k: v for k, v in data["dashboard"].items() if hasattr(DashboardConfig, k)})

    # Environment variable overrides
    if os.getenv("CONDUIT_LOG_PAYLOADS", "").lower() == "true":
        cfg.shim.log_payloads = True
    if ep := os.getenv("CONDUIT_OTEL_ENDPOINT"):
        cfg.telemetry.otel_endpoint = ep
    if db := os.getenv("CONDUIT_DB_PATH"):
        cfg.registry.db_path = db

    return cfg


# Module-level singleton
_config: ConduitConfig | None = None


def get_config() -> ConduitConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def auto_ingest_mcp_manifests(registry) -> int:
    """Auto-ingest MCP manifests listed in registry.mcp_manifest_paths (02_INTERCEPTION_SHIM.md §6)."""
    from conduit.registry.mcp import ingest_mcp_manifest, discover_mcp_servers
    cfg = get_config()
    total = 0
    for path in cfg.registry.mcp_manifest_paths:
        try:
            total += ingest_mcp_manifest(registry, path)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("MCP manifest ingest failed for %s: %s", path, e)
    total += discover_mcp_servers(registry)
    return total
