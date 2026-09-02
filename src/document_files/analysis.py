"""Transport-neutral document analysis jobs and replaceable backends.

The job contract describes document bytes, requested work, and budgets without
containing a local path or a transport mechanism.  A caller may pass the same
job and byte stream to an in-process backend, a subprocess wrapper, or a remote
service.  Analyzer implementations may stage bytes privately because the
format libraries require reopenable files, but that path never enters the
public contract or result.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal, Protocol

from .extraction_errors import BudgetExceededError, ExtractionError
from .extraction_protocol import (
    ENVELOPE_SCHEMA_VERSION,
    AdapterCapabilities,
    AdapterDescriptor,
    CoverageProfile,
    ExtractedUnit,
    ExtractionEnvelope,
    ExtractionIssue,
)
from .extraction_registry import AdapterRegistry, build_default_registry
from .formats import FORMAT_SPECS

ANALYSIS_JOB_SCHEMA_VERSION = "document-files.analysis-job.v1"
ANALYSIS_RESULT_SCHEMA_VERSION = "document-files.analysis-result.v1"
DEFAULT_COMPLETION_SECONDS = 580.0
MAX_ANALYSIS_INPUT_BYTES = 2 * 1024 * 1024 * 1024
MAX_CONTINUATION_PASSES = 1_000
COPY_CHUNK_BYTES = 1024 * 1024

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _strict_mapping(
    value: object,
    *,
    allowed: set[str],
    required: set[str],
    location: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExtractionError(
            "analysis contract value must be an object",
            details={"location": location},
        )
    keys = set(value)
    if unknown := keys - allowed:
        raise ExtractionError(
            "analysis contract contains unknown fields",
            details={"location": location, "fields": sorted(unknown)},
        )
    if missing := required - keys:
        raise ExtractionError(
            "analysis contract is missing required fields",
            details={"location": location, "fields": sorted(missing)},
        )
    return value


@dataclass(frozen=True)
class AnalysisInput:
    """Identity of bytes supplied separately by the selected transport."""

    format_id: str
    media_type: str
    byte_size: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.format_id, str):
            raise ExtractionError("analysis input format must be a string")
        if not isinstance(self.media_type, str):
            raise ExtractionError("analysis input media type must be a string")
        specification = FORMAT_SPECS.get(self.format_id)
        if specification is None:
            raise ExtractionError(
                "analysis input format is unsupported",
                details={"format_id": self.format_id},
            )
        if self.media_type != specification.media_type:
            raise ExtractionError(
                "analysis input media type does not match its format",
                details={
                    "format_id": self.format_id,
                    "media_type": self.media_type,
                    "expected_media_type": specification.media_type,
                },
            )
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or not 0 <= self.byte_size <= 64 * 1024 * 1024 * 1024
        ):
            raise ExtractionError("analysis input byte size is invalid")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ExtractionError("analysis input hash must be a lowercase SHA-256 digest")

    @classmethod
    def from_bytes(cls, content: bytes, *, format_id: str) -> AnalysisInput:
        if not isinstance(content, bytes):
            raise ExtractionError("analysis byte input must be bytes")
        if not isinstance(format_id, str):
            raise ExtractionError("analysis input format must be a string")
        specification = FORMAT_SPECS.get(format_id)
        if specification is None:
            raise ExtractionError(
                "analysis input format is unsupported",
                details={"format_id": format_id},
            )
        return cls(
            format_id=format_id,
            media_type=specification.media_type,
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    @classmethod
    def from_path(cls, path: str | Path, *, format_id: str) -> AnalysisInput:
        """Build an identity for a local convenience adapter, not the job contract."""

        if not isinstance(format_id, str):
            raise ExtractionError("analysis input format must be a string")
        specification = FORMAT_SPECS.get(format_id)
        if specification is None:
            raise ExtractionError(
                "analysis input format is unsupported",
                details={"format_id": format_id},
            )
        source = Path(path)
        try:
            size = source.stat().st_size
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise ExtractionError("analysis input file is unavailable") from exc
        return cls(format_id, specification.media_type, size, digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_id": self.format_id,
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AnalysisInput:
        value = _strict_mapping(
            raw,
            allowed={"format_id", "media_type", "byte_size", "sha256"},
            required={"format_id", "media_type", "byte_size", "sha256"},
            location="$.input",
        )
        return cls(
            format_id=value["format_id"],
            media_type=value["media_type"],
            byte_size=value["byte_size"],
            sha256=value["sha256"],
        )


@dataclass(frozen=True)
class AnalysisBudgets:
    """Caller-declared limits that apply equally to local and remote backends."""

    max_input_bytes: int = MAX_ANALYSIS_INPUT_BYTES
    completion_seconds: float = DEFAULT_COMPLETION_SECONDS

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_input_bytes, bool)
            or not isinstance(self.max_input_bytes, int)
            or not 1 <= self.max_input_bytes <= 64 * 1024 * 1024 * 1024
        ):
            raise ExtractionError("analysis input budget is invalid")
        if (
            isinstance(self.completion_seconds, bool)
            or not isinstance(self.completion_seconds, (int, float))
            or not math.isfinite(float(self.completion_seconds))
            or not 0.01 <= float(self.completion_seconds) <= 600.0
        ):
            raise ExtractionError("analysis completion budget is invalid")
        object.__setattr__(self, "completion_seconds", float(self.completion_seconds))

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "completion_seconds": self.completion_seconds,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AnalysisBudgets:
        value = _strict_mapping(
            raw,
            allowed={"max_input_bytes", "completion_seconds"},
            required={"max_input_bytes", "completion_seconds"},
            location="$.budgets",
        )
        return cls(
            max_input_bytes=value["max_input_bytes"],
            completion_seconds=value["completion_seconds"],
        )


@dataclass(frozen=True)
class AnalysisJob:
    """Transport-neutral request for one complete structural extraction."""

    job_id: str
    input: AnalysisInput
    budgets: AnalysisBudgets = AnalysisBudgets()
    operation: Literal["extract"] = "extract"
    schema_version: str = ANALYSIS_JOB_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ANALYSIS_JOB_SCHEMA_VERSION:
            raise ExtractionError(
                "analysis job schema version is unsupported",
                details={"schema_version": self.schema_version},
            )
        if not isinstance(self.job_id, str) or not _JOB_ID_RE.fullmatch(self.job_id):
            raise ExtractionError("analysis job identifier is invalid")
        if self.operation != "extract":
            raise ExtractionError(
                "analysis job operation is unsupported",
                details={"operation": self.operation},
            )
        if not isinstance(self.input, AnalysisInput):
            raise ExtractionError("analysis job input is invalid")
        if not isinstance(self.budgets, AnalysisBudgets):
            raise ExtractionError("analysis job budgets are invalid")
        if self.input.byte_size > self.budgets.max_input_bytes:
            raise BudgetExceededError(
                "analysis input exceeds its byte budget",
                details={
                    "count": self.input.byte_size,
                    "limit": self.budgets.max_input_bytes,
                },
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "operation": self.operation,
            "input": self.input.to_dict(),
            "budgets": self.budgets.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: object) -> AnalysisJob:
        value = _strict_mapping(
            raw,
            allowed={"schema_version", "job_id", "operation", "input", "budgets"},
            required={"schema_version", "job_id", "operation", "input", "budgets"},
            location="$",
        )
        return cls(
            schema_version=value["schema_version"],
            job_id=value["job_id"],
            operation=value["operation"],
            input=AnalysisInput.from_dict(value["input"]),
            budgets=AnalysisBudgets.from_dict(value["budgets"]),
        )


def _capabilities_from_dict(raw: object) -> AdapterCapabilities:
    value = _strict_mapping(
        raw,
        allowed={
            "format_ids",
            "structural_unit_types",
            "execution_mode",
            "preserves_reading_order",
            "supports_geometry",
            "supports_confidence",
            "supports_ocr",
            "may_emit_partial",
            "protocol_version",
        },
        required={
            "format_ids",
            "structural_unit_types",
            "execution_mode",
            "preserves_reading_order",
            "supports_geometry",
            "supports_confidence",
            "supports_ocr",
            "may_emit_partial",
            "protocol_version",
        },
        location="$.analyzer.capabilities",
    )
    format_ids = value["format_ids"]
    unit_types = value["structural_unit_types"]
    if not isinstance(format_ids, list) or not isinstance(unit_types, list):
        raise ExtractionError("analyzer capability lists are invalid")
    return AdapterCapabilities(
        format_ids=tuple(format_ids),
        structural_unit_types=tuple(unit_types),
        execution_mode=value["execution_mode"],
        preserves_reading_order=value["preserves_reading_order"],
        supports_geometry=value["supports_geometry"],
        supports_confidence=value["supports_confidence"],
        supports_ocr=value["supports_ocr"],
        may_emit_partial=value["may_emit_partial"],
        protocol_version=value["protocol_version"],
    )


def _descriptor_from_dict(raw: object) -> AdapterDescriptor:
    value = _strict_mapping(
        raw,
        allowed={"adapter_id", "adapter_version", "config_hash", "capabilities"},
        required={"adapter_id", "adapter_version", "config_hash", "capabilities"},
        location="$.analyzer",
    )
    return AdapterDescriptor(
        adapter_id=value["adapter_id"],
        adapter_version=value["adapter_version"],
        config_hash=value["config_hash"],
        capabilities=_capabilities_from_dict(value["capabilities"]),
    )


def _issue_from_dict(raw: object, *, location: str) -> ExtractionIssue:
    value = _strict_mapping(
        raw,
        allowed={
            "code",
            "message",
            "severity",
            "impact",
            "coverage_dimensions",
            "details",
        },
        required={"code", "message", "severity", "impact", "coverage_dimensions"},
        location=location,
    )
    dimensions = value["coverage_dimensions"]
    if not isinstance(dimensions, list):
        raise ExtractionError(
            "analysis issue coverage dimensions must be an array",
            details={"location": location},
        )
    return ExtractionIssue(
        code=value["code"],
        message=value["message"],
        severity=value["severity"],
        impact=value["impact"],
        coverage_dimensions=tuple(dimensions),
        details=value.get("details", {}),
    )


def _unit_from_dict(raw: object, *, index: int) -> ExtractedUnit:
    location = f"$.extraction.units[{index}]"
    value = _strict_mapping(
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
        required={
            "unit_type",
            "structure_path",
            "content",
            "derivation_method",
            "geometry",
            "confidence",
            "quality_flags",
            "issues",
        },
        location=location,
    )
    flags = value["quality_flags"]
    issues = value["issues"]
    if not isinstance(flags, list) or not isinstance(issues, list):
        raise ExtractionError(
            "analysis unit arrays are invalid",
            details={"location": location},
        )
    return ExtractedUnit(
        unit_type=value["unit_type"],
        structure_path=value["structure_path"],
        content=value["content"],
        derivation_method=value["derivation_method"],
        geometry=value["geometry"],
        confidence=value["confidence"],
        quality_flags=tuple(flags),
        issues=tuple(
            _issue_from_dict(issue, location=f"{location}.issues[{issue_index}]")
            for issue_index, issue in enumerate(issues)
        ),
    )


def _envelope_from_dict(raw: object) -> ExtractionEnvelope:
    value = _strict_mapping(
        raw,
        allowed={
            "schema_version",
            "descriptor",
            "completeness",
            "coverage",
            "units",
            "issues",
            "manifest_hash",
        },
        required={
            "schema_version",
            "descriptor",
            "completeness",
            "coverage",
            "units",
            "issues",
            "manifest_hash",
        },
        location="$.extraction",
    )
    if value["schema_version"] != ENVELOPE_SCHEMA_VERSION:
        raise ExtractionError(
            "analysis extraction envelope schema is unsupported",
            details={"schema_version": value["schema_version"]},
        )
    coverage = _strict_mapping(
        value["coverage"],
        allowed={"text_content", "structure", "visual_content", "reading_order"},
        required={"text_content", "structure", "visual_content", "reading_order"},
        location="$.extraction.coverage",
    )
    units = value["units"]
    issues = value["issues"]
    if not isinstance(units, list) or not isinstance(issues, list):
        raise ExtractionError("analysis extraction result arrays are invalid")
    return ExtractionEnvelope(
        schema_version=value["schema_version"],
        descriptor=_descriptor_from_dict(value["descriptor"]),
        completeness=value["completeness"],
        coverage=CoverageProfile(**coverage),
        units=tuple(_unit_from_dict(unit, index=index) for index, unit in enumerate(units)),
        issues=tuple(
            _issue_from_dict(issue, location=f"$.extraction.issues[{index}]")
            for index, issue in enumerate(issues)
        ),
        manifest_hash=value["manifest_hash"],
    )


@dataclass(frozen=True)
class AnalysisResult:
    """Serializable analyzer response bound to the exact supplied bytes."""

    job_id: str
    input: AnalysisInput
    analyzer: AdapterDescriptor
    extraction: ExtractionEnvelope
    schema_version: str = ANALYSIS_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ANALYSIS_RESULT_SCHEMA_VERSION:
            raise ExtractionError(
                "analysis result schema version is unsupported",
                details={"schema_version": self.schema_version},
            )
        if not isinstance(self.job_id, str) or not _JOB_ID_RE.fullmatch(self.job_id):
            raise ExtractionError("analysis result job identifier is invalid")
        if not isinstance(self.input, AnalysisInput):
            raise ExtractionError("analysis result input is invalid")
        if not isinstance(self.analyzer, AdapterDescriptor):
            raise ExtractionError("analysis result analyzer is invalid")
        if not isinstance(self.extraction, ExtractionEnvelope):
            raise ExtractionError("analysis result extraction is invalid")
        if self.analyzer != self.extraction.descriptor:
            raise ExtractionError("analysis result analyzer does not match its extraction")
        if self.input.format_id not in self.analyzer.capabilities.format_ids:
            raise ExtractionError(
                "analysis result analyzer does not support the input format",
                details={"format_id": self.input.format_id},
            )

    @classmethod
    def create(cls, job: AnalysisJob, extraction: ExtractionEnvelope) -> AnalysisResult:
        return cls(
            job_id=job.job_id,
            input=job.input,
            analyzer=extraction.descriptor,
            extraction=extraction,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "input": self.input.to_dict(),
            "analyzer": self.analyzer.to_dict(),
            "extraction": self.extraction.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        raw: object,
        *,
        expected_job: AnalysisJob | None = None,
    ) -> AnalysisResult:
        value = _strict_mapping(
            raw,
            allowed={"schema_version", "job_id", "input", "analyzer", "extraction"},
            required={"schema_version", "job_id", "input", "analyzer", "extraction"},
            location="$",
        )
        result = cls(
            schema_version=value["schema_version"],
            job_id=value["job_id"],
            input=AnalysisInput.from_dict(value["input"]),
            analyzer=_descriptor_from_dict(value["analyzer"]),
            extraction=_envelope_from_dict(value["extraction"]),
        )
        if expected_job is not None and (
            result.job_id != expected_job.job_id or result.input != expected_job.input
        ):
            raise ExtractionError("analysis result does not match the requested job")
        return result


class AnalyzerBackend(Protocol):
    """One replaceable local or remote implementation of the analysis contract."""

    def analyze(self, job: AnalysisJob, source: BinaryIO) -> AnalysisResult: ...


def runtime_root() -> Path:
    configured = os.environ.get("DOCUMENT_FILES_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Document Files"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "document-files"


def default_registry() -> AdapterRegistry:
    return build_default_registry(runtime_root())


def _pending_continuation(envelope: ExtractionEnvelope) -> bool:
    return any(
        issue.code in {"pdf_page_range_pending", "office_image_range_pending"}
        for issue in envelope.issues
    )


def extract_complete(
    path: Path,
    *,
    format_id: str,
    active_registry: AdapterRegistry | None = None,
    completion_seconds: float = DEFAULT_COMPLETION_SECONDS,
) -> ExtractionEnvelope:
    """Extract one privately staged document and finish resumable ranges."""

    active_registry = active_registry or default_registry()
    adapter = active_registry.resolve(format_id)
    started = time.monotonic()
    result = adapter.extract(path, format_id=format_id)
    passes = 0
    if time.monotonic() - started >= completion_seconds:
        raise BudgetExceededError(
            "document extraction exceeded its total runtime budget",
            details={"limit_seconds": completion_seconds, "format_id": format_id},
        )
    while _pending_continuation(result):
        resume = getattr(adapter, "resume", None)
        if not callable(resume):
            raise ExtractionError(
                "adapter reported pending coverage without a continuation operation",
                details={"format_id": format_id, "adapter_id": adapter.descriptor.adapter_id},
            )
        if passes >= MAX_CONTINUATION_PASSES:
            raise BudgetExceededError(
                "document continuation exceeded its pass budget",
                details={"limit": MAX_CONTINUATION_PASSES, "format_id": format_id},
            )
        if time.monotonic() - started >= completion_seconds:
            raise BudgetExceededError(
                "document continuation exceeded its total runtime budget",
                details={"limit_seconds": completion_seconds, "format_id": format_id},
            )
        previous_manifest = result.manifest_hash
        result = resume(path, format_id=format_id, previous=result)
        passes += 1
        if result.manifest_hash == previous_manifest:
            raise ExtractionError(
                "document continuation made no progress",
                details={"format_id": format_id, "pass": passes},
            )
    return result


@contextmanager
def _staged_stream(job: AnalysisJob, source: BinaryIO):
    """Materialize and verify a sequential stream without exposing its location."""

    with tempfile.TemporaryDirectory(prefix="document-files-analysis-") as folder:
        root = Path(folder)
        private_path = root / f"source.{job.input.format_id}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        destination = os.open(private_path, flags, 0o600)
        digest = hashlib.sha256()
        copied = 0
        try:
            while True:
                try:
                    chunk = source.read(COPY_CHUNK_BYTES)
                except (AttributeError, OSError) as exc:
                    raise ExtractionError("analysis input stream could not be read") from exc
                if chunk == b"":
                    break
                if not isinstance(chunk, bytes):
                    raise ExtractionError("analysis input stream must yield bytes")
                copied += len(chunk)
                if copied > job.budgets.max_input_bytes:
                    raise BudgetExceededError(
                        "analysis input exceeds its byte budget",
                        details={"count": copied, "limit": job.budgets.max_input_bytes},
                    )
                digest.update(chunk)
                offset = 0
                while offset < len(chunk):
                    written = os.write(destination, chunk[offset:])
                    if written <= 0:
                        raise ExtractionError("analysis input could not be staged")
                    offset += written
        except OSError as exc:
            raise ExtractionError("analysis input stream could not be staged") from exc
        finally:
            os.close(destination)
        if copied != job.input.byte_size or digest.hexdigest() != job.input.sha256:
            raise ExtractionError(
                "analysis input bytes do not match the job identity",
                details={
                    "expected_bytes": job.input.byte_size,
                    "received_bytes": copied,
                    "expected_sha256": job.input.sha256,
                    "received_sha256": digest.hexdigest(),
                },
            )
        yield private_path


class LocalAnalyzerBackend:
    """Run the common job contract with analyzers available on this host."""

    def __init__(self, active_registry: AdapterRegistry | None = None) -> None:
        self.registry = active_registry or default_registry()

    def analyze(self, job: AnalysisJob, source: BinaryIO) -> AnalysisResult:
        if not isinstance(job, AnalysisJob):
            raise ExtractionError("analysis backend requires an AnalysisJob")
        started = time.monotonic()
        with _staged_stream(job, source) as path:
            remaining_seconds = job.budgets.completion_seconds - (time.monotonic() - started)
            if remaining_seconds <= 0:
                raise BudgetExceededError(
                    "document analysis exceeded its total runtime budget",
                    details={"limit_seconds": job.budgets.completion_seconds},
                )
            extraction = extract_complete(
                path,
                format_id=job.input.format_id,
                active_registry=self.registry,
                completion_seconds=remaining_seconds,
            )
        return AnalysisResult.create(job, extraction)


def analyze_document(
    job: AnalysisJob,
    source: BinaryIO,
    *,
    backend: AnalyzerBackend | None = None,
) -> AnalysisResult:
    """Analyze an authorized byte stream through the selected backend."""

    selected = backend or LocalAnalyzerBackend()
    result = selected.analyze(job, source)
    if not isinstance(result, AnalysisResult):
        raise ExtractionError("analysis backend returned an invalid result")
    if result.job_id != job.job_id or result.input != job.input:
        raise ExtractionError("analysis backend result does not match the requested job")
    return result
