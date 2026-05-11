"""Drift detection helpers — compares observed params against registered schema."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FieldChange:
    field: str
    change_type: str   # "type_change" | "field_rename" | "new_required" | "field_removed"
    severity: str      # "low" | "medium" | "high" | "critical"
    auto_correctable: bool


def detect_drift(schema: dict, observed_params: dict, aliases: dict) -> list[FieldChange]:
    """Compare observed params against schema; return list of detected changes."""
    changes: list[FieldChange] = []
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    for field, value in observed_params.items():
        if field in aliases:
            changes.append(FieldChange(
                field=field, change_type="field_rename",
                severity="medium", auto_correctable=True,
            ))
            continue
        if field not in props:
            if not schema.get("additionalProperties", True):
                changes.append(FieldChange(
                    field=field, change_type="field_removed",
                    severity="low", auto_correctable=True,
                ))
            continue
        expected_type = props[field].get("type")
        if expected_type and not _type_matches(value, expected_type):
            changes.append(FieldChange(
                field=field, change_type="type_change",
                severity="medium", auto_correctable=_is_coercible(value, expected_type),
            ))

    for req_field in required:
        if req_field not in observed_params:
            changes.append(FieldChange(
                field=req_field, change_type="new_required",
                severity="high", auto_correctable=False,
            ))

    return changes


def _type_matches(value: object, expected: str) -> bool:
    mapping = {"integer": int, "number": (int, float), "string": str,
               "boolean": bool, "array": list, "object": dict}
    t = mapping.get(expected)
    return isinstance(value, t) if t else True


def _is_coercible(value: object, target_type: str) -> bool:
    if target_type == "integer":
        try:
            int(str(value)); return True  # noqa: E702
        except (ValueError, TypeError):
            return False
    if target_type in ("number", "float"):
        try:
            float(str(value)); return True  # noqa: E702
        except (ValueError, TypeError):
            return False
    if target_type == "boolean":
        return str(value).lower() in ("true", "false", "1", "0")
    return False
