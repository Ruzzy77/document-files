"""Neutral, bounded extraction-adapter boundary.

Adapters only produce ordered structural observations.  Index identity,
revision identity, source-unit identity, final source anchors, and authority
remain owned by the core.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol

from .extraction_errors import BudgetExceededError, ExtractionError
from .extractors import (
    EXTRACTOR_VERSION,
    EXTRACTOR_VERSION_OVERRIDES,
    EXTRACTORS,
    ExtractionResult,
    extract,
)
from .formats import FORMAT_SPECS

REQUEST_SCHEMA_VERSION = "document-files.extraction-request.v1"
RESULT_SCHEMA_VERSION = "document-files.extraction-result.v1"
ENVELOPE_SCHEMA_VERSION = "document-files.extraction-envelope.v1"

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ISSUE_SEVERITIES = frozenset({"info", "warning", "error"})
_COMPLETENESS_VALUES = frozenset({"complete", "partial"})
_EXECUTION_MODES = frozenset({"in_process", "jsonl_subprocess"})
_INFORMATIONAL_ISSUES = frozenset({"unit_split"})
_DERIVATION_METHODS = frozenset({"native_text", "ocr"})
_PROHIBITED_CONTROL_FIELDS = frozenset(
    {
        "anchor",
        "anchors",
        "authority",
        "authority_level",
        "document_id",
        "file_path",
        "file_uri",
        "file_url",
        "filename",
        "original_path",
        "original_uri",
        "original_url",
        "path",
        "relative_path",
        "revision_id",
        "source_anchor",
        "source_anchors",
        "source_path",
        "source_unit_id",
        "source_uri",
        "source_url",
        "trust_lineage",
        "unit_id",
        "uri",
        "url",
    }
)

JSONScalar = str | int | float | bool | None
FrozenJSON = JSONScalar | tuple["FrozenJSON", ...] | Mapping[str, "FrozenJSON"]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _thaw_json(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _freeze_json(value: object, *, location: str = "$", depth: int = 0) -> FrozenJSON:
    if depth > 32:
        raise ExtractionError(
            "adapter JSON is nested too deeply",
            details={"location": location, "limit": 32},
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExtractionError(
                "adapter JSON contains a non-finite number",
                details={"location": location},
            )
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJSON] = {}
        if not all(isinstance(key, str) for key in value):
            raise ExtractionError(
                "adapter JSON object keys must be strings",
                details={"location": location},
            )
        for key in sorted(value):
            frozen[key] = _freeze_json(
                value[key],
                location=f"{location}.{key}",
                depth=depth + 1,
            )
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, location=f"{location}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    raise ExtractionError(
        "adapter value is not JSON-compatible",
        details={"location": location, "type": type(value).__name__},
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def adapter_config_hash(config: Mapping[str, object] | None = None) -> str:
    """Return the canonical digest used to distinguish adapter configurations."""

    frozen = _freeze_json(config or {}, location="$.config")
    return _sha256_json(frozen)


def _validate_identifier(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ExtractionError(
            f"{field_name} is not a valid adapter identifier",
            details={"field": field_name},
        )


def _validate_short_string(value: object, *, field_name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise ExtractionError(
            f"{field_name} must be a non-empty bounded string",
            details={"field": field_name, "limit": limit},
        )
    return value


@dataclass(frozen=True)
class AdapterCapabilities:
    """Operator-declared capabilities, not adapter-controlled output."""

    format_ids: tuple[str, ...]
    structural_unit_types: tuple[str, ...]
    execution_mode: Literal["in_process", "jsonl_subprocess"]
    preserves_reading_order: bool = True
    supports_geometry: bool = False
    supports_confidence: bool = False
    supports_ocr: bool = False
    may_emit_partial: bool = True
    protocol_version: str = RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in self.format_ids):
            raise ExtractionError("adapter format identifiers must be strings")
        if not all(isinstance(value, str) for value in self.structural_unit_types):
            raise ExtractionError("adapter structural unit types must be strings")
        format_ids = tuple(sorted(set(self.format_ids)))
        unit_types = tuple(sorted(set(self.structural_unit_types)))
        if not format_ids:
            raise ExtractionError(
                "adapter capabilities must declare at least one format"
            )
        for value in format_ids:
            _validate_identifier(value, field_name="format_id")
        if not unit_types:
            raise ExtractionError(
                "adapter capabilities must declare at least one structural unit type"
            )
        for value in unit_types:
            _validate_identifier(value, field_name="structural_unit_type")
        if (
            not isinstance(self.execution_mode, str)
            or self.execution_mode not in _EXECUTION_MODES
        ):
            raise ExtractionError(
                "adapter execution mode is invalid",
                details={"execution_mode": self.execution_mode},
            )
        boolean_fields = (
            "preserves_reading_order",
            "supports_geometry",
            "supports_confidence",
            "supports_ocr",
            "may_emit_partial",
        )
        if any(not isinstance(getattr(self, name), bool) for name in boolean_fields):
            raise ExtractionError("adapter capability flags must be booleans")
        if self.protocol_version != RESULT_SCHEMA_VERSION:
            raise ExtractionError(
                "adapter capability protocol version is unsupported",
                details={"protocol_version": self.protocol_version},
            )
        object.__setattr__(self, "format_ids", format_ids)
        object.__setattr__(self, "structural_unit_types", unit_types)

    def to_dict(self) -> dict:
        return {
            "format_ids": list(self.format_ids),
            "structural_unit_types": list(self.structural_unit_types),
            "execution_mode": self.execution_mode,
            "preserves_reading_order": self.preserves_reading_order,
            "supports_geometry": self.supports_geometry,
            "supports_confidence": self.supports_confidence,
            "supports_ocr": self.supports_ocr,
            "may_emit_partial": self.may_emit_partial,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True)
class AdapterDescriptor:
    """Stable identity of one implementation and configuration."""

    adapter_id: str
    adapter_version: str
    config_hash: str
    capabilities: AdapterCapabilities

    def __post_init__(self) -> None:
        _validate_identifier(self.adapter_id, field_name="adapter_id")
        _validate_short_string(
            self.adapter_version,
            field_name="adapter_version",
            limit=128,
        )
        if not _HASH_RE.fullmatch(self.config_hash):
            raise ExtractionError(
                "adapter config hash must be a lowercase SHA-256 digest"
            )

    @classmethod
    def from_config(
        cls,
        *,
        adapter_id: str,
        adapter_version: str,
        config: Mapping[str, object] | None,
        capabilities: AdapterCapabilities,
    ) -> AdapterDescriptor:
        return cls(
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            config_hash=adapter_config_hash(config),
            capabilities=capabilities,
        )

    def to_dict(self) -> dict:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "config_hash": self.config_hash,
            "capabilities": self.capabilities.to_dict(),
        }


@dataclass(frozen=True)
class AdapterBudgets:
    """Hard execution and result-size limits for a subprocess adapter."""

    timeout_seconds: float = 60.0
    max_input_bytes: int = 2 * 1024 * 1024 * 1024
    max_request_bytes: int = 1024 * 1024
    max_stdout_bytes: int = 64 * 1024 * 1024
    max_stderr_bytes: int = 128 * 1024
    max_units: int = 200_000
    max_issues: int = 200_000
    max_unit_content_chars: int = 5_000_000
    max_total_content_chars: int = 100_000_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
        ):
            raise ExtractionError(
                "adapter timeout budget must be numeric",
                details={"field": "timeout_seconds"},
            )
        if not 0.01 <= self.timeout_seconds <= 600.0:
            raise ExtractionError(
                "adapter budget is outside the safety bounds",
                details={
                    "field": "timeout_seconds",
                    "minimum": 0.01,
                    "maximum": 600.0,
                },
            )
        limits = {
            "max_input_bytes": (self.max_input_bytes, 1, 64 * 1024 * 1024 * 1024),
            "max_request_bytes": (self.max_request_bytes, 1, 16 * 1024 * 1024),
            "max_stdout_bytes": (self.max_stdout_bytes, 1, 512 * 1024 * 1024),
            "max_stderr_bytes": (self.max_stderr_bytes, 1, 16 * 1024 * 1024),
            "max_units": (self.max_units, 1, 1_000_000),
            "max_issues": (self.max_issues, 1, 1_000_000),
            "max_unit_content_chars": (
                self.max_unit_content_chars,
                1,
                50_000_000,
            ),
            "max_total_content_chars": (
                self.max_total_content_chars,
                1,
                500_000_000,
            ),
        }
        for field_name, (value, minimum, maximum) in limits.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ExtractionError(
                    "adapter size and count budgets must be integers",
                    details={"field": field_name},
                )
            if value < minimum or value > maximum:
                raise ExtractionError(
                    "adapter budget is outside the safety bounds",
                    details={
                        "field": field_name,
                        "minimum": minimum,
                        "maximum": maximum,
                    },
                )

    def to_request_dict(self) -> dict:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_input_bytes": self.max_input_bytes,
            "max_request_bytes": self.max_request_bytes,
            "max_stdout_bytes": self.max_stdout_bytes,
            "max_units": self.max_units,
            "max_issues": self.max_issues,
            "max_unit_content_chars": self.max_unit_content_chars,
            "max_total_content_chars": self.max_total_content_chars,
        }


@dataclass(frozen=True)
class ExtractionIssue:
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    details: Mapping[str, FrozenJSON] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.code, field_name="issue.code")
        _validate_short_string(self.message, field_name="issue.message", limit=20_000)
        if not isinstance(self.severity, str) or self.severity not in _ISSUE_SEVERITIES:
            raise ExtractionError(
                "adapter issue severity is invalid",
                details={"severity": self.severity},
            )
        if not isinstance(self.details, Mapping):
            raise ExtractionError("adapter issue details must be an object")
        frozen = _freeze_json(self.details, location="$.issue.details")
        if not isinstance(frozen, Mapping):
            raise ExtractionError("adapter issue details must be an object")
        object.__setattr__(self, "details", frozen)

    def to_dict(self) -> dict:
        result = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.details:
            result["details"] = _thaw_json(self.details)
        return result


@dataclass(frozen=True)
class ExtractedUnit:
    """One ordered structural observation without index-owned identity."""

    unit_type: str
    structure_path: Mapping[str, FrozenJSON]
    content: str
    derivation_method: str = "native_text"
    geometry: Mapping[str, FrozenJSON] = field(default_factory=dict)
    confidence: float | None = None
    quality_flags: tuple[str, ...] = ()
    issues: tuple[ExtractionIssue, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.unit_type, field_name="unit_type")
        _validate_identifier(self.derivation_method, field_name="derivation_method")
        if self.derivation_method not in _DERIVATION_METHODS:
            raise ExtractionError(
                "adapter unit derivation_method is unsupported",
                details={
                    "derivation_method": self.derivation_method,
                    "allowed": sorted(_DERIVATION_METHODS),
                },
            )
        if not isinstance(self.content, str):
            raise ExtractionError("adapter unit content must be a string")
        if not isinstance(self.structure_path, Mapping):
            raise ExtractionError("adapter unit structure_path must be an object")
        if not isinstance(self.geometry, Mapping):
            raise ExtractionError("adapter unit geometry must be an object")
        structure_path = _freeze_json(
            self.structure_path,
            location="$.unit.structure_path",
        )
        geometry = _freeze_json(self.geometry, location="$.unit.geometry")
        if not isinstance(structure_path, Mapping) or not isinstance(geometry, Mapping):
            raise ExtractionError("adapter unit structural fields must be objects")
        confidence = self.confidence
        if confidence is not None:
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(float(confidence))
                or not 0 <= float(confidence) <= 1
            ):
                raise ExtractionError("adapter unit confidence must be between 0 and 1")
            confidence = float(confidence)
        if not all(isinstance(flag, str) for flag in self.quality_flags):
            raise ExtractionError("adapter quality flags must be strings")
        quality_flags = tuple(sorted(set(self.quality_flags)))
        for flag in quality_flags:
            _validate_identifier(flag, field_name="quality_flag")
        issues = tuple(self.issues)
        if not all(isinstance(issue, ExtractionIssue) for issue in issues):
            raise ExtractionError("adapter unit issues must be ExtractionIssue values")
        object.__setattr__(self, "structure_path", structure_path)
        object.__setattr__(self, "geometry", geometry)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "quality_flags", quality_flags)
        object.__setattr__(self, "issues", issues)

    def to_dict(self) -> dict:
        return {
            "unit_type": self.unit_type,
            "structure_path": _thaw_json(self.structure_path),
            "content": self.content,
            "derivation_method": self.derivation_method,
            "geometry": _thaw_json(self.geometry),
            "confidence": self.confidence,
            "quality_flags": list(self.quality_flags),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class ExtractionEnvelope:
    """Immutable extraction result whose digest covers all adapter observations."""

    descriptor: AdapterDescriptor
    completeness: Literal["complete", "partial"]
    units: tuple[ExtractedUnit, ...]
    issues: tuple[ExtractionIssue, ...]
    manifest_hash: str
    schema_version: str = ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ENVELOPE_SCHEMA_VERSION:
            raise ExtractionError(
                "extraction envelope schema version is unsupported",
                details={"schema_version": self.schema_version},
            )
        if (
            not isinstance(self.completeness, str)
            or self.completeness not in _COMPLETENESS_VALUES
        ):
            raise ExtractionError(
                "extraction completeness is invalid",
                details={"completeness": self.completeness},
            )
        units = tuple(self.units)
        issues = tuple(self.issues)
        if not all(isinstance(unit, ExtractedUnit) for unit in units):
            raise ExtractionError(
                "extraction envelope units must be ExtractedUnit values"
            )
        if not all(isinstance(issue, ExtractionIssue) for issue in issues):
            raise ExtractionError(
                "extraction envelope issues must be ExtractionIssue values"
            )
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "issues", issues)
        expected_hash = _sha256_json(self._manifest_payload())
        if self.manifest_hash != expected_hash:
            raise ExtractionError(
                "extraction envelope manifest hash does not match its contents"
            )

    @classmethod
    def create(
        cls,
        *,
        descriptor: AdapterDescriptor,
        completeness: Literal["complete", "partial"],
        units: Sequence[ExtractedUnit],
        issues: Sequence[ExtractionIssue] = (),
    ) -> ExtractionEnvelope:
        units_tuple = tuple(units)
        issues_tuple = tuple(issues)
        provisional = {
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "descriptor": descriptor.to_dict(),
            "completeness": completeness,
            "units": [unit.to_dict() for unit in units_tuple],
            "issues": [issue.to_dict() for issue in issues_tuple],
        }
        return cls(
            descriptor=descriptor,
            completeness=completeness,
            units=units_tuple,
            issues=issues_tuple,
            manifest_hash=_sha256_json(provisional),
        )

    def _manifest_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "descriptor": self.descriptor.to_dict(),
            "completeness": self.completeness,
            "units": [unit.to_dict() for unit in self.units],
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def to_dict(self) -> dict:
        return {**self._manifest_payload(), "manifest_hash": self.manifest_hash}


class ExtractionAdapter(Protocol):
    descriptor: AdapterDescriptor

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope: ...


def _issue_from_mapping(
    raw: Mapping[str, object],
    *,
    location: str,
    strict: bool,
) -> ExtractionIssue:
    allowed = {"code", "message", "severity", "details"}
    required = {"code", "message"}
    keys = set(raw)
    if strict and (unknown := keys - allowed):
        raise ExtractionError(
            "adapter issue contains unknown fields",
            details={"location": location, "fields": sorted(unknown)},
        )
    if missing := required - keys:
        raise ExtractionError(
            "adapter issue is missing required fields",
            details={"location": location, "fields": sorted(missing)},
        )
    code = raw["code"]
    message = raw["message"]
    severity = raw.get("severity")
    if not isinstance(code, str) or not isinstance(message, str):
        raise ExtractionError(
            "adapter issue code and message must be strings",
            details={"location": location},
        )
    if severity is None:
        severity = "info" if code in _INFORMATIONAL_ISSUES else "warning"
    if not isinstance(severity, str):
        raise ExtractionError(
            "adapter issue severity must be a string",
            details={"location": location},
        )
    details = raw.get("details", {})
    if not isinstance(details, Mapping):
        raise ExtractionError(
            "adapter issue details must be an object",
            details={"location": location},
        )
    if not strict:
        details = {
            **details,
            **{
                key: value
                for key, value in raw.items()
                if key not in {"code", "message", "severity", "details"}
            },
        }
    return ExtractionIssue(
        code=code,
        message=message,
        severity=severity,
        details=details,
    )


def _built_in_capabilities(adapter_name: str) -> AdapterCapabilities:
    unit_types: set[str] = set()
    format_ids: set[str] = set()
    for extension, specification in FORMAT_SPECS.items():
        if specification.adapter == adapter_name:
            format_ids.add(extension)
            unit_types.update(specification.structural_units)
    if not format_ids:
        format_ids.add(adapter_name)
    if not unit_types:
        unit_types.add("structural_unit")
    if adapter_name == "html":
        unit_types.add("document_text")
    preserves_reading_order = adapter_name not in {"docx", "pdf", "pptx"}
    return AdapterCapabilities(
        format_ids=tuple(format_ids),
        structural_unit_types=tuple(unit_types),
        execution_mode="in_process",
        preserves_reading_order=preserves_reading_order,
        supports_geometry=False,
        supports_confidence=False,
        supports_ocr=False,
        may_emit_partial=True,
    )


def builtin_adapter_descriptor(
    adapter_name: str,
    *,
    config: Mapping[str, object] | None = None,
) -> AdapterDescriptor:
    if adapter_name not in EXTRACTORS:
        raise ExtractionError(
            "no built-in extractor is registered",
            details={"adapter_name": adapter_name},
        )
    if config:
        raise ExtractionError(
            "the current built-in adapters do not accept configuration",
            details={"adapter_name": adapter_name},
        )
    adapter_version = EXTRACTOR_VERSION_OVERRIDES.get(
        adapter_name,
        EXTRACTOR_VERSION,
    )
    return AdapterDescriptor.from_config(
        # Persisted adapter IDs retain their original namespace so an identity
        # rename does not invalidate every existing extraction projection.
        adapter_id=f"document-files.builtin.{adapter_name}",
        adapter_version=adapter_version,
        config={},
        capabilities=_built_in_capabilities(adapter_name),
    )


def _convert_builtin_result(
    result: ExtractionResult,
) -> tuple[tuple[ExtractedUnit, ...], tuple[ExtractionIssue, ...]]:
    units: list[ExtractedUnit] = []
    for unit_index, unit in enumerate(result.units):
        issues = tuple(
            _issue_from_mapping(
                issue,
                location=f"$.units[{unit_index}].issues[{issue_index}]",
                strict=False,
            )
            for issue_index, issue in enumerate(unit.issues)
        )
        units.append(
            ExtractedUnit(
                unit_type=unit.unit_type,
                structure_path=unit.structure_path,
                content=unit.content,
                issues=issues,
            )
        )
    issues = tuple(
        _issue_from_mapping(
            issue,
            location=f"$.issues[{issue_index}]",
            strict=False,
        )
        for issue_index, issue in enumerate(result.issues)
    )
    return tuple(units), issues


def _honest_completeness(
    declared: str,
    units: Sequence[ExtractedUnit],
    issues: Sequence[ExtractionIssue],
) -> Literal["complete", "partial"]:
    if declared == "partial" or not units:
        return "partial"
    all_issues = [*issues, *(issue for unit in units for issue in unit.issues)]
    if any(
        issue.severity in {"warning", "error"}
        and issue.code not in _INFORMATIONAL_ISSUES
        for issue in all_issues
    ):
        return "partial"
    return "complete"


def run_builtin_extraction(
    path: Path,
    adapter_name: str,
    *,
    config: Mapping[str, object] | None = None,
) -> ExtractionEnvelope:
    """Wrap the current local extractor in the neutral adapter envelope."""

    path = Path(path)
    if not path.is_file():
        raise ExtractionError("adapter input must be an existing regular file")
    descriptor = builtin_adapter_descriptor(adapter_name, config=config)
    units, issues = _convert_builtin_result(extract(path, adapter_name))
    if not descriptor.capabilities.preserves_reading_order:
        issues = (
            *issues,
            ExtractionIssue(
                code="reading_order_unverified",
                message=(
                    "This built-in adapter does not verify that extracted units preserve the "
                    "document's reading order."
                ),
                severity="warning",
                details={"adapter": adapter_name},
            ),
        )
    completeness = _honest_completeness("complete", units, issues)
    return ExtractionEnvelope.create(
        descriptor=descriptor,
        completeness=completeness,
        units=units,
        issues=issues,
    )


def _reject_adapter_control_fields(value: object, *, location: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.strip().lower().replace("-", "_")
            controls_anchor_or_authority = normalized.startswith(
                ("anchor_", "authority_")
            ) or normalized.endswith(("_anchor", "_anchors", "_authority"))
            if normalized in _PROHIBITED_CONTROL_FIELDS or controls_anchor_or_authority:
                raise ExtractionError(
                    "adapter output attempted to set a core-owned field",
                    details={"location": f"{location}.{key}", "field": normalized},
                )
            _reject_adapter_control_fields(item, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_adapter_control_fields(item, location=f"{location}[{index}]")


def _json_object_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExtractionError(
                "adapter output contains a duplicate JSON key",
                details={"field": key},
            )
        result[key] = value
    return result


def _parse_jsonl_result(stdout: bytes) -> Mapping[str, object]:
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError("adapter output is not valid UTF-8") from exc
    # JSONL records are separated by the protocol's physical LF byte.  Do not
    # use str.splitlines(): valid JSON strings can contain U+2028 or U+2029,
    # and treating those content characters as record boundaries corrupts an
    # otherwise valid one-line result.
    lines = [line for line in text.split("\n") if line.strip()]
    if len(lines) != 1:
        raise ExtractionError(
            "adapter must emit exactly one non-empty JSONL result line",
            details={"line_count": len(lines)},
        )
    try:
        result = json.loads(
            lines[0],
            object_pairs_hook=_json_object_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ExtractionError(
                    "adapter output contains a non-finite JSON number",
                    details={"value": value},
                )
            ),
        )
    except ExtractionError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ExtractionError("adapter output is not valid JSON") from exc
    if not isinstance(result, Mapping):
        raise ExtractionError("adapter result must be a JSON object")
    _freeze_json(result, location="$")
    _reject_adapter_control_fields(result)
    return result


def _strict_fields(
    value: Mapping[str, object],
    *,
    allowed: set[str],
    required: set[str],
    location: str,
) -> None:
    keys = set(value)
    if unknown := keys - allowed:
        raise ExtractionError(
            "adapter output contains unknown fields",
            details={"location": location, "fields": sorted(unknown)},
        )
    if missing := required - keys:
        raise ExtractionError(
            "adapter output is missing required fields",
            details={"location": location, "fields": sorted(missing)},
        )


def _unit_from_external(
    raw: Mapping[str, object],
    *,
    index: int,
    descriptor: AdapterDescriptor,
    budgets: AdapterBudgets,
) -> ExtractedUnit:
    location = f"$.units[{index}]"
    _strict_fields(
        raw,
        allowed={
            "unit_type",
            "structure_path",
            "content",
            "derivation_method",
            "geometry",
            "confidence",
            "quality_flags",
            "issues",
        },
        required={"unit_type", "structure_path", "content"},
        location=location,
    )
    unit_type = raw["unit_type"]
    structure_path = raw["structure_path"]
    content = raw["content"]
    if not isinstance(unit_type, str):
        raise ExtractionError(
            "adapter unit_type must be a string",
            details={"location": location},
        )
    if unit_type not in descriptor.capabilities.structural_unit_types:
        raise ExtractionError(
            "adapter emitted an undeclared structural unit type",
            details={"location": location, "unit_type": unit_type},
        )
    if not isinstance(structure_path, Mapping):
        raise ExtractionError(
            "adapter structure_path must be an object",
            details={"location": location},
        )
    if not isinstance(content, str):
        raise ExtractionError(
            "adapter content must be a string",
            details={"location": location},
        )
    if len(content) > budgets.max_unit_content_chars:
        raise BudgetExceededError(
            "adapter unit content exceeds its configured budget",
            details={
                "unit_index": index,
                "limit": budgets.max_unit_content_chars,
            },
        )
    derivation_method = raw.get("derivation_method", "native_text")
    if not isinstance(derivation_method, str):
        raise ExtractionError(
            "adapter derivation_method must be a string",
            details={"location": location},
        )
    geometry = raw.get("geometry", {})
    if not isinstance(geometry, Mapping):
        raise ExtractionError(
            "adapter geometry must be an object",
            details={"location": location},
        )
    if geometry and not descriptor.capabilities.supports_geometry:
        raise ExtractionError(
            "adapter emitted geometry without declaring that capability",
            details={"location": location},
        )
    confidence = raw.get("confidence")
    if confidence is not None and not descriptor.capabilities.supports_confidence:
        raise ExtractionError(
            "adapter emitted confidence without declaring that capability",
            details={"location": location},
        )
    quality_flags = raw.get("quality_flags", [])
    if not isinstance(quality_flags, list) or not all(
        isinstance(flag, str) for flag in quality_flags
    ):
        raise ExtractionError(
            "adapter quality_flags must be an array of strings",
            details={"location": location},
        )
    if derivation_method not in _DERIVATION_METHODS:
        raise ExtractionError(
            "adapter derivation_method is unsupported",
            details={
                "location": location,
                "derivation_method": derivation_method,
                "allowed": sorted(_DERIVATION_METHODS),
            },
        )
    marks_ocr = derivation_method == "ocr" or "ocr" in quality_flags
    if marks_ocr and not descriptor.capabilities.supports_ocr:
        raise ExtractionError(
            "adapter emitted OCR content without declaring that capability",
            details={"location": location},
        )
    if "ocr" in quality_flags and derivation_method != "ocr":
        raise ExtractionError(
            "adapter OCR quality flag requires derivation_method=ocr",
            details={"location": location},
        )
    raw_issues = raw.get("issues", [])
    if not isinstance(raw_issues, list):
        raise ExtractionError(
            "adapter unit issues must be an array",
            details={"location": location},
        )
    issues = tuple(
        _issue_from_mapping(
            issue,
            location=f"{location}.issues[{issue_index}]",
            strict=True,
        )
        if isinstance(issue, Mapping)
        else _raise_issue_object(f"{location}.issues[{issue_index}]")
        for issue_index, issue in enumerate(raw_issues)
    )
    return ExtractedUnit(
        unit_type=unit_type,
        structure_path=structure_path,
        content=content,
        derivation_method=derivation_method,
        geometry=geometry,
        confidence=confidence,
        quality_flags=tuple(quality_flags),
        issues=issues,
    )


def _raise_issue_object(location: str):
    raise ExtractionError(
        "adapter issue must be an object",
        details={"location": location},
    )


@dataclass
class _BoundedCapture:
    limit: int
    data: bytearray = field(default_factory=bytearray)
    exceeded: bool = False


def _kill_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _capture_stream(
    process: subprocess.Popen,
    stream,
    capture: _BoundedCapture,
) -> None:
    try:
        while chunk := stream.read(64 * 1024):
            remaining = capture.limit + 1 - len(capture.data)
            if remaining > 0:
                capture.data.extend(chunk[:remaining])
            if len(capture.data) > capture.limit:
                capture.exceeded = True
                _kill_process_group(process)
                break
    finally:
        stream.close()


def _bounded_subprocess(
    *,
    command: tuple[str, ...],
    request: bytes,
    budgets: AdapterBudgets,
    input_fd: int,
    cwd: Path,
    environment: Mapping[str, str],
) -> tuple[bytes, bytes]:
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            close_fds=True,
            pass_fds=(input_fd,),
            start_new_session=True,
        )
    except OSError as exc:
        raise ExtractionError(
            "could not start extraction adapter",
            details={"executable": command[0], "error_type": type(exc).__name__},
        ) from exc
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _kill_process_group(process)
        raise ExtractionError("could not establish extraction adapter pipes")

    stdout = _BoundedCapture(budgets.max_stdout_bytes)
    stderr = _BoundedCapture(budgets.max_stderr_bytes)
    readers = [
        threading.Thread(
            target=_capture_stream,
            args=(process, process.stdout, stdout),
            daemon=True,
        ),
        threading.Thread(
            target=_capture_stream,
            args=(process, process.stderr, stderr),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    try:
        try:
            process.stdin.write(request)
            process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            return_code = process.wait(timeout=budgets.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(process)
            process.wait()
            raise BudgetExceededError(
                "extraction adapter exceeded its timeout",
                details={"timeout_seconds": budgets.timeout_seconds},
            ) from exc
    finally:
        if process.poll() is None:
            _kill_process_group(process)
            process.wait()
        for reader in readers:
            reader.join(timeout=5)

    if stdout.exceeded:
        raise BudgetExceededError(
            "extraction adapter stdout exceeded its byte budget",
            details={"limit": budgets.max_stdout_bytes},
        )
    if stderr.exceeded:
        raise BudgetExceededError(
            "extraction adapter stderr exceeded its byte budget",
            details={"limit": budgets.max_stderr_bytes},
        )
    if return_code != 0:
        raise ExtractionError(
            "extraction adapter exited unsuccessfully",
            details={
                "return_code": return_code,
                "stderr_bytes": len(stderr.data),
                "stderr_sha256": hashlib.sha256(stderr.data).hexdigest(),
            },
        )
    return bytes(stdout.data), bytes(stderr.data)


def _sanitized_environment(extra: Mapping[str, str] | None) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    if extra:
        for key, value in extra.items():
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key)
                or not isinstance(value, str)
                or "\x00" in value
            ):
                raise ExtractionError("adapter environment contains an invalid entry")
            environment[key] = value
    return environment


class ExternalJSONLAdapter:
    """Run one externally implemented adapter through a strict JSONL boundary."""

    def __init__(
        self,
        descriptor: AdapterDescriptor,
        command: Sequence[str],
        budgets: AdapterBudgets | None = None,
        *,
        config: Mapping[str, object] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if descriptor.capabilities.execution_mode != "jsonl_subprocess":
            raise ExtractionError(
                "external adapter descriptor must declare jsonl_subprocess execution"
            )
        if not command or not all(
            isinstance(argument, str) and argument and "\x00" not in argument
            for argument in command
        ):
            raise ExtractionError(
                "external adapter command must contain bounded strings"
            )
        executable = command[0]
        executable_path = (
            Path(os.path.abspath(Path(executable).expanduser()))
            if Path(executable).is_absolute()
            else Path(shutil.which(executable) or "")
        )
        if not executable_path or not executable_path.is_file():
            raise ExtractionError(
                "external adapter executable was not found",
                details={"executable": executable},
            )
        frozen_config = _freeze_json(config or {}, location="$.config")
        if not isinstance(frozen_config, Mapping):
            raise ExtractionError("external adapter config must be an object")
        if adapter_config_hash(frozen_config) != descriptor.config_hash:
            raise ExtractionError(
                "external adapter config does not match the descriptor config hash"
            )
        self.descriptor = descriptor
        self.command = (str(executable_path), *tuple(command[1:]))
        self.budgets = budgets or AdapterBudgets()
        self.config = frozen_config
        self.environment = _sanitized_environment(environment)

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        path = Path(path)
        if not path.is_file():
            raise ExtractionError("adapter input must be an existing regular file")
        _validate_identifier(format_id, field_name="format_id")
        if format_id not in self.descriptor.capabilities.format_ids:
            raise ExtractionError(
                "external adapter does not declare support for this format",
                details={"format_id": format_id},
            )
        input_bytes = path.stat().st_size
        if input_bytes > self.budgets.max_input_bytes:
            raise BudgetExceededError(
                "external adapter input exceeds its byte budget",
                details={"count": input_bytes, "limit": self.budgets.max_input_bytes},
            )
        input_fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            request = {
                "schema_version": REQUEST_SCHEMA_VERSION,
                "operation": "extract",
                "adapter": {
                    "adapter_id": self.descriptor.adapter_id,
                    "adapter_version": self.descriptor.adapter_version,
                    "config_hash": self.descriptor.config_hash,
                },
                "input": {
                    "kind": "read_only_file_descriptor",
                    "file_descriptor": input_fd,
                    "path": f"/dev/fd/{input_fd}",
                    "format_id": format_id,
                },
                "config": _thaw_json(self.config),
                "budgets": self.budgets.to_request_dict(),
            }
            request_bytes = _canonical_json(request) + b"\n"
            if len(request_bytes) > self.budgets.max_request_bytes:
                raise BudgetExceededError(
                    "external adapter request exceeds its byte budget",
                    details={
                        "count": len(request_bytes),
                        "limit": self.budgets.max_request_bytes,
                    },
                )
            with tempfile.TemporaryDirectory(prefix="document-files-adapter-") as temporary:
                stdout, _stderr = _bounded_subprocess(
                    command=self.command,
                    request=request_bytes,
                    budgets=self.budgets,
                    input_fd=input_fd,
                    cwd=Path(temporary),
                    environment=self.environment,
                )
        finally:
            os.close(input_fd)
        return self._validate_result(_parse_jsonl_result(stdout))

    def _validate_result(self, raw: Mapping[str, object]) -> ExtractionEnvelope:
        _strict_fields(
            raw,
            allowed={"schema_version", "completeness", "units", "issues"},
            required={"schema_version", "completeness", "units"},
            location="$",
        )
        if raw["schema_version"] != RESULT_SCHEMA_VERSION:
            raise ExtractionError(
                "external adapter result schema version is unsupported",
                details={"schema_version": raw["schema_version"]},
            )
        declared_completeness = raw["completeness"]
        if (
            not isinstance(declared_completeness, str)
            or declared_completeness not in _COMPLETENESS_VALUES
        ):
            raise ExtractionError(
                "external adapter completeness must be complete or partial"
            )
        raw_units = raw["units"]
        raw_issues = raw.get("issues", [])
        if not isinstance(raw_units, list):
            raise ExtractionError("external adapter units must be an array")
        if not isinstance(raw_issues, list):
            raise ExtractionError("external adapter issues must be an array")
        if len(raw_units) > self.budgets.max_units:
            raise BudgetExceededError(
                "external adapter emitted too many units",
                details={"count": len(raw_units), "limit": self.budgets.max_units},
            )
        if len(raw_issues) > self.budgets.max_issues:
            raise BudgetExceededError(
                "external adapter emitted too many issues",
                details={"count": len(raw_issues), "limit": self.budgets.max_issues},
            )
        units = tuple(
            _unit_from_external(
                unit,
                index=index,
                descriptor=self.descriptor,
                budgets=self.budgets,
            )
            if isinstance(unit, Mapping)
            else _raise_unit_object(index)
            for index, unit in enumerate(raw_units)
        )
        issue_count = len(raw_issues) + sum(len(unit.issues) for unit in units)
        if issue_count > self.budgets.max_issues:
            raise BudgetExceededError(
                "external adapter emitted too many combined issues",
                details={"count": issue_count, "limit": self.budgets.max_issues},
            )
        total_content_chars = sum(len(unit.content) for unit in units)
        if total_content_chars > self.budgets.max_total_content_chars:
            raise BudgetExceededError(
                "external adapter content exceeds its total character budget",
                details={
                    "count": total_content_chars,
                    "limit": self.budgets.max_total_content_chars,
                },
            )
        issues = tuple(
            _issue_from_mapping(
                issue,
                location=f"$.issues[{index}]",
                strict=True,
            )
            if isinstance(issue, Mapping)
            else _raise_issue_object(f"$.issues[{index}]")
            for index, issue in enumerate(raw_issues)
        )
        completeness = _honest_completeness(
            declared_completeness,
            units,
            issues,
        )
        if (
            completeness == "partial"
            and not self.descriptor.capabilities.may_emit_partial
        ):
            raise ExtractionError(
                "external adapter emitted a partial result without declaring that capability"
            )
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness=completeness,
            units=units,
            issues=issues,
        )


def _raise_unit_object(index: int):
    raise ExtractionError(
        "external adapter unit must be an object",
        details={"location": f"$.units[{index}]"},
    )
