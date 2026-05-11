"""Schema Validator — validates and auto-corrects tool call parameters."""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from conduit.registry.store import SchemaRegistry

logger = logging.getLogger(__name__)


@dataclass
class Correction:
    correction_type: str   # e.g. "type_coerce.str_to_int"
    field_path: str
    original_value: Any
    corrected_value: Any
    confidence: float = 1.0


@dataclass
class ValidationError:
    field_path: str
    error_type: str        # "type_mismatch" | "required_missing" | "unknown_field" | "range_violation"
    expected: str
    received: str
    auto_correctable: bool
    correction_applied: Correction | None = None


@dataclass
class ValidationResult:
    decision: str                          # "pass" | "gate"
    validation_result: str                 # "pass" | "corrected" | "gated_soft" | "gated_hard" | "skipped"
    corrected_params: dict | None = None
    corrections: list[Correction] = field(default_factory=list)
    errors: list[ValidationError] = field(default_factory=list)
    reason: str = ""


class SchemaValidator:
    """Validates tool call parameters against the schema registry."""

    def __init__(self, registry: SchemaRegistry, hard_gate: bool = False,
                 auto_correct: bool = True) -> None:
        self._registry = registry
        self._hard_gate = hard_gate
        self._auto_correct = auto_correct
        self._compiled: dict[str, jsonschema.Validator] = {}

    def validate(self, tool_id: str, params: dict, trace_id: str = "") -> ValidationResult:
        snap = self._registry.get_current(tool_id)
        if snap is None:
            return ValidationResult(decision="pass", validation_result="skipped",
                                    reason="no_schema")

        schema = snap.json_schema
        aliases = snap.known_aliases

        # Fast path: valid as-is
        errors = self._collect_errors(schema, params)
        if not errors:
            return ValidationResult(decision="pass", validation_result="pass",
                                    corrected_params=params)

        if not self._auto_correct:
            return self._gate_result(errors, params)

        # Attempt corrections
        corrected = copy.deepcopy(params)
        applied: list[Correction] = []
        validation_errors: list[ValidationError] = []

        for err in errors:
            correction = self._try_correct(err, corrected, schema, aliases)
            if correction:
                applied.append(correction)
                ve = ValidationError(
                    field_path=err.json_path, error_type=self._classify_error(err),
                    expected=str(err.validator), received=str(err.instance),
                    auto_correctable=True, correction_applied=correction,
                )
            else:
                ve = ValidationError(
                    field_path=err.json_path, error_type=self._classify_error(err),
                    expected=str(err.validator), received=str(err.instance),
                    auto_correctable=False,
                )
            validation_errors.append(ve)

        # Re-validate after corrections
        remaining = self._collect_errors(schema, corrected)
        if not remaining:
            # Report drift if corrections were needed
            if applied:
                self._registry.report_drift(
                    tool_id=tool_id, observed_params=params, trace_id=trace_id,
                    severity="medium", auto_corrected=True,
                    fields_changed=[c.field_path for c in applied],
                )
            return ValidationResult(
                decision="pass", validation_result="corrected",
                corrected_params=corrected, corrections=applied,
                errors=validation_errors,
            )

        # Still invalid after corrections
        return self._gate_result(validation_errors, params)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _collect_errors(self, schema: dict, params: dict) -> list:
        validator = self._get_validator(schema)
        return list(validator.iter_errors(params))

    def _get_validator(self, schema: dict) -> jsonschema.Validator:
        key = json.dumps(schema, sort_keys=True)
        if key not in self._compiled:
            cls = jsonschema.Draft7Validator
            cls.check_schema(schema)
            self._compiled[key] = cls(schema)
        return self._compiled[key]

    def _try_correct(self, err, params: dict, schema: dict, aliases: dict) -> Correction | None:
        """Attempt a single correction. Mutates params in place on success."""
        path = list(err.absolute_path)

        # Type coercion — path points to the field
        if err.validator == "type" and path:
            field_name = path[-1]
            expected_type = err.validator_value
            current = params.get(field_name)
            coerced = self._coerce(current, expected_type)
            if coerced is not None:
                params[field_name] = coerced
                src = type(current).__name__
                # Normalize target: "integer" → "int", "number" → "float"
                tgt = {"integer": "int", "number": "float", "boolean": "bool"}.get(expected_type, expected_type)
                return Correction(
                    correction_type=f"type_coerce.{src}_to_{tgt}",
                    field_path=f"$.{field_name}",
                    original_value=current,
                    corrected_value=coerced,
                )

        # Missing required field — try alias lookup or default inject
        if err.validator == "required":
            # jsonschema reports missing field name in err.validator_value as a list
            missing_fields = err.validator_value if isinstance(err.validator_value, list) else [str(err.validator_value)]
            for missing in missing_fields:
                # Check alias: old_name → missing
                for old_name, new_name in aliases.items():
                    if new_name == missing and old_name in params:
                        params[missing] = params.pop(old_name)
                        return Correction(
                            correction_type="field_rename",
                            field_path=f"$.{old_name}",
                            original_value=old_name,
                            corrected_value=missing,
                        )
                # Inject default
                prop_schema = schema.get("properties", {}).get(missing, {})
                if "default" in prop_schema:
                    params[missing] = prop_schema["default"]
                    return Correction(
                        correction_type="default_inject",
                        field_path=f"$.{missing}",
                        original_value=None,
                        corrected_value=prop_schema["default"],
                    )

        # Strip unknown fields (additionalProperties violation)
        # jsonschema reports the offending field name in err.message
        if err.validator == "additionalProperties":
            # Extract field name from message: "Additional properties are not allowed ('debug' was unexpected)"
            import re
            match = re.search(r"'([^']+)' (?:was|were) unexpected", err.message)
            if match:
                field_name = match.group(1)
                if field_name in params:
                    del params[field_name]
                    return Correction(
                        correction_type="optional_strip",
                        field_path=f"$.{field_name}",
                        original_value=field_name,
                        corrected_value=None,
                    )

        return None

    def _coerce(self, value: Any, target_type: str) -> Any:
        try:
            if target_type == "integer":
                return int(str(value))
            if target_type in ("number",):
                return float(str(value))
            if target_type == "boolean":
                s = str(value).lower()
                if s in ("true", "1"):
                    return True
                if s in ("false", "0"):
                    return False
        except (ValueError, TypeError):
            pass
        return None

    def _classify_error(self, err) -> str:
        mapping = {
            "type": "type_mismatch",
            "required": "required_missing",
            "additionalProperties": "unknown_field",
            "minimum": "range_violation",
            "maximum": "range_violation",
        }
        return mapping.get(err.validator, "unknown")

    def _gate_result(self, errors: list, params: dict) -> ValidationResult:
        decision = "gate" if self._hard_gate else "pass"
        result = "gated_hard" if self._hard_gate else "gated_soft"
        ve_list = [
            e if isinstance(e, ValidationError) else ValidationError(
                field_path=getattr(e, "json_path", "?"),
                error_type=self._classify_error(e),
                expected=str(getattr(e, "validator", "?")),
                received=str(getattr(e, "instance", "?")),
                auto_correctable=False,
            )
            for e in errors
        ]
        return ValidationResult(decision=decision, validation_result=result,
                                errors=ve_list, corrected_params=params)
