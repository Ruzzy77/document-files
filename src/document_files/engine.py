"""Artifact operations shared by the Document Files CLI and MCP server."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import tempfile
from collections import Counter
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import BadZipFile, ZipFile

try:
    import olefile
except ModuleNotFoundError:  # Optional in a reduced OpenAI host runtime.
    from ._vendor import olefile

try:
    from hwpx import (
        HwpxDocument,
        TextExtractor,
        validate_editor_open_safety,
    )
    from hwpx.body_patch import apply_body_ops
    from hwpx.experimental import render_layout_preview
    from hwpx.table_patch import apply_table_ops, table_summary
    from hwpx_automation.office.authoring import (
        create_document_from_plan,
        inspect_document_authoring_quality,
        validate_document_plan,
    )
except ModuleNotFoundError:  # HWPX authoring is optional in an OpenAI host runtime.
    HwpxDocument = None  # type: ignore[assignment,misc]
    TextExtractor = None  # type: ignore[assignment,misc]
    validate_editor_open_safety = None  # type: ignore[assignment]
    apply_body_ops = None  # type: ignore[assignment]
    render_layout_preview = None  # type: ignore[assignment]
    apply_table_ops = None  # type: ignore[assignment]
    table_summary = None  # type: ignore[assignment]
    create_document_from_plan = None  # type: ignore[assignment]
    inspect_document_authoring_quality = None  # type: ignore[assignment]
    validate_document_plan = None  # type: ignore[assignment]

from .analysis import (
    ANALYSIS_JOB_SCHEMA_VERSION,
    ANALYSIS_RESULT_SCHEMA_VERSION,
    AnalysisBudgets,
    AnalysisInput,
    AnalysisJob,
    AnalysisResult,
    AnalyzerBackend,
    analyze_document,
)
from .extraction_errors import DocumentExtractionError
from .formats import FORMAT_SPECS
from .read_projection import analysis_record, project_read_text, project_tables_and_fields
from .rhwp_backend import RHWP_VERSION, RhwpBackend, RhwpBackendError, backend_status
from .structured_extraction import (
    DEFAULT_PUBLIC_STRUCTURED_UNITS,
    MAX_PUBLIC_STRUCTURED_UNITS,
    STRUCTURED_EXTRACTION_SCHEMA_VERSION,
    project_structured_extraction,
)

EDIT_PLAN_SCHEMA_VERSION = "document-files.edit.v1"
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_EDIT_OPERATIONS = 200
MAX_OPERATION_TEXT_CHARS = 200_000
MAX_TABLE_CELLS_RETURNED = 2_000
HWP_SIGNATURE = b"HWP Document File"
try:
    PLUGIN_VERSION = version("document-files")
except PackageNotFoundError:
    PLUGIN_VERSION = "1.5.0"
PYTHON_HWPX_VERSION = "6.3.0"
PYTHON_HWPX_AUTOMATION_VERSION = "7.0.3"


class DocumentFilesError(Exception):
    """Structured error returned by every public operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.suggestion = suggestion

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
            "suggestion": self.suggestion,
        }


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _translate_backend_error(exc: RhwpBackendError) -> DocumentFilesError:
    return DocumentFilesError(
        exc.code,
        str(exc),
        details=exc.details,
        suggestion=exc.suggestion,
    )


def _python_hwpx_status() -> dict[str, Any]:
    core_version = _package_version("python-hwpx")
    automation_version = _package_version("python-hwpx-automation")
    available = (
        core_version == PYTHON_HWPX_VERSION
        and automation_version == PYTHON_HWPX_AUTOMATION_VERSION
        and HwpxDocument is not None
        and validate_document_plan is not None
    )
    return {
        "available": available,
        "expectedVersion": PYTHON_HWPX_VERSION,
        "version": core_version,
        "expectedAutomationVersion": PYTHON_HWPX_AUTOMATION_VERSION,
        "automationVersion": automation_version,
        "reason": None if available else "version-mismatch",
    }


def _require_python_hwpx_backend() -> dict[str, Any]:
    status = _python_hwpx_status()
    if not status["available"]:
        raise DocumentFilesError(
            "backend-version-mismatch",
            "The resolved python-hwpx stack does not match the pinned versions.",
            details=status,
            suggestion="Reinstall the Document Files plugin runtime from its current lockfile.",
        )
    return status


def _rhwp_backend() -> RhwpBackend:
    try:
        backend = RhwpBackend()
        actual_version = backend.version()
    except RhwpBackendError as exc:
        raise _translate_backend_error(exc) from exc
    if actual_version != RHWP_VERSION:
        raise DocumentFilesError(
            "backend-version-mismatch",
            "The resolved rhwp backend does not match the pinned version.",
            details={"expectedVersion": RHWP_VERSION, "actualVersion": actual_version},
            suggestion="Provision the pinned backend or update DOCUMENT_FILES_RHWP.",
        )
    return backend


def _absolute(path: str | Path, *, strict: bool) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        return candidate.resolve(strict=strict)
    except OSError as exc:
        raise DocumentFilesError(
            "path-unavailable",
            "The requested local path is unavailable.",
            details={"path": str(candidate), "errorType": type(exc).__name__},
        ) from exc


def _source_file(path: str | Path, *, suffixes: set[str]) -> Path:
    source = _absolute(path, strict=True)
    if not source.is_file():
        raise DocumentFilesError(
            "input-not-file",
            "The input must be a regular local file.",
            details={"path": str(source)},
        )
    suffix = source.suffix.casefold()
    if suffix not in suffixes:
        raise DocumentFilesError(
            "unsupported-format",
            f"Expected one of: {', '.join(sorted(suffixes))}.",
            details={"path": str(source), "suffix": suffix},
        )
    size = source.stat().st_size
    if size > MAX_FILE_BYTES:
        raise DocumentFilesError(
            "input-too-large",
            "The input exceeds the configured file-size limit.",
            details={"size": size, "limit": MAX_FILE_BYTES},
        )
    return source


def _output_file(
    path: str | Path,
    *,
    suffix: str,
    source: Path | None = None,
    overwrite: bool,
    create_parent: bool,
) -> Path:
    output = _absolute(path, strict=False)
    if output.suffix.casefold() != suffix:
        raise DocumentFilesError(
            "output-format-mismatch",
            f"The output path must end with {suffix}.",
            details={"path": str(output)},
        )
    if source is not None and output == source.resolve():
        raise DocumentFilesError(
            "in-place-edit-refused",
            "Document Files writes edited documents to a separate output file.",
            details={"path": str(source)},
            suggestion="Choose a different output path.",
        )
    if output.exists() and not overwrite:
        raise DocumentFilesError(
            "output-exists",
            "The output file already exists.",
            details={"path": str(output)},
            suggestion="Choose a new path or explicitly allow overwrite.",
        )
    if create_parent:
        output.parent.mkdir(parents=True, exist_ok=True)
    elif not output.parent.exists():
        raise DocumentFilesError(
            "output-parent-missing",
            "The output folder does not exist.",
            details={"path": str(output.parent)},
        )
    return output


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    stat_result = path.stat()
    return {
        "path": str(path),
        "size": stat_result.st_size,
        "mtimeNs": stat_result.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def _directory_record(path: Path, *, suffix: str) -> dict[str, Any]:
    files = sorted(item for item in path.iterdir() if item.is_file() and item.suffix == suffix)
    return {
        "path": str(path),
        "fileCount": len(files),
        "files": [_file_record(item) for item in files],
        "totalSize": sum(item.stat().st_size for item in files),
    }


def _ensure_source_unchanged(source: Path, before: dict[str, Any]) -> None:
    after = _file_record(source)
    if after != before:
        raise DocumentFilesError(
            "source-changed",
            "The source file changed while the operation was running.",
            details={"before": before, "after": after},
        )


def capabilities() -> dict[str, Any]:
    """Return the exact local capability and backend availability contract."""

    rhwp = backend_status()
    rhwp_available = bool(rhwp.get("available"))
    python_hwpx = _python_hwpx_status()
    python_hwpx_available = bool(python_hwpx.get("available"))
    return {
        "schemaVersion": "document-files.capabilities.v1",
        "pluginVersion": PLUGIN_VERSION,
        "headless": True,
        "nativeAppAutomation": False,
        "runtimeNetworkUsed": False,
        "nativeRenderChecked": False,
        "backends": {
            "pythonHwpx": python_hwpx,
            "rhwp": rhwp,
        },
        "extraction": {
            "schemaVersion": "document-files.extraction-result.v2",
            "structuredSchemaVersion": STRUCTURED_EXTRACTION_SCHEMA_VERSION,
            "analysisJobSchemaVersion": ANALYSIS_JOB_SCHEMA_VERSION,
            "analysisResultSchemaVersion": ANALYSIS_RESULT_SCHEMA_VERSION,
            "maxInputBytes": MAX_FILE_BYTES,
            "maxStructuredUnitsPerPage": MAX_PUBLIC_STRUCTURED_UNITS,
            "coverageReported": True,
            "singlePassBoundedAnalysis": True,
            "structuredPagination": True,
            "sourceDeclaredSemanticsOnly": True,
            "pathIndependentInput": True,
            "replaceableBackend": True,
            "formats": [
                "docx",
                "htm",
                "html",
                "hwp",
                "hwpx",
                "markdown",
                "md",
                "pdf",
                "pptx",
                "txt",
                "xlsx",
            ],
        },
        "artifactFormats": {
            "hwp": {
                "inspectMetadata": True,
                "inspectContent": True,
                "extractText": True,
                "extractMarkdown": True,
                "extractStructure": True,
                "readBackend": "source-parser-first",
                "protectedContent": False,
                "distributionRecoveryRequiresRhwp": True,
                "convertToHwpx": rhwp_available and python_hwpx_available,
                "renderSvg": rhwp_available,
                "renderPdf": rhwp_available,
                "edit": False,
            },
            "hwpx": {
                "inspect": True,
                "extractText": True,
                "extractMarkdown": True,
                "extractStructure": True,
                "readBackend": "source-parser-first",
                "create": python_hwpx_available,
                "editCopy": python_hwpx_available,
                "verify": python_hwpx_available,
                "renderHtml": python_hwpx_available,
                "renderSvg": rhwp_available,
                "renderPdf": rhwp_available,
                "convertToHwp": False,
            },
        },
        "outputPolicy": {
            "sourceReadOnly": True,
            "separateOutput": True,
            "atomicFilePublish": True,
            "dryRunDefaultForEdits": False,
            "preflightAndReopenInWrite": True,
            "dependencyMismatchFailsClosed": True,
        },
    }


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _hwp_header(source: Path, *, file_record: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        compound = olefile.OleFileIO(
            str(source),
            raise_defects=olefile.DEFECT_INCORRECT,
        )
    except OSError as exc:
        raise DocumentFilesError(
            "hwp-open-failed",
            "The binary HWP compound file could not be opened.",
            details={"errorType": type(exc).__name__},
        ) from exc

    with compound:
        if not compound.exists("FileHeader"):
            raise DocumentFilesError(
                "invalid-hwp",
                "The HWP FileHeader stream is missing.",
            )
        header = compound.openstream("FileHeader").read(40)
        if len(header) < 40 or not header.startswith(HWP_SIGNATURE):
            raise DocumentFilesError(
                "invalid-hwp",
                "The HWP file signature is invalid.",
            )
        version_bytes = header[32:36]
        version = ".".join(str(value) for value in reversed(version_bytes))
        flags = struct.unpack_from("<I", header, 36)[0]
        sections = [
            parts[1]
            for parts in compound.listdir(streams=True, storages=False)
            if len(parts) == 2 and parts[0] == "BodyText" and parts[1].startswith("Section")
        ]

    return {
        "format": "hwp5",
        "file": file_record if file_record is not None else _file_record(source),
        "version": version,
        "compressed": bool(flags & 0x01),
        "encrypted": bool(flags & 0x02),
        "distributionDocument": bool(flags & 0x04),
        "sectionCount": len(sections),
    }


def _validate_text_limit(max_chars: int) -> None:
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or not 0 <= max_chars <= MAX_TEXT_CHARS
    ):
        raise DocumentFilesError(
            "invalid-limit",
            "max_chars is outside the supported range.",
            details={"minimum": 0, "maximum": MAX_TEXT_CHARS},
        )


@contextmanager
def _read_document(path: str | Path, *, allow_protected_metadata: bool = False):
    """Bind every local read view to one budgeted analysis of the same bytes."""

    source = _source_file(path, suffixes={f".{format_id}" for format_id in FORMAT_SPECS})
    before = _file_record(source)
    format_id = source.suffix.casefold().removeprefix(".")
    metadata: dict[str, Any] = {}
    try:
        # The HWPX content router also recognizes binary HWP under a .hwpx name.
        with source.open("rb") as stream:
            binary_hwp = stream.read(8) == bytes.fromhex("d0cf11e0a1b11ae1")
        if format_id == "hwp" or (format_id == "hwpx" and binary_hwp):
            metadata = _hwp_header(source, file_record=before)
            if metadata["encrypted"]:
                if allow_protected_metadata:
                    yield source, before, None, metadata
                    return
                raise DocumentFilesError(
                    "protected-document",
                    "The HWP body is encrypted and was not opened.",
                    details={"path": str(source), "encrypted": True},
                    suggestion="Provide an authorized unprotected HWP or HWPX copy.",
                )
        job = AnalysisJob(
            job_id=f"local:{format_id}:{before['sha256'][:24]}",
            input=AnalysisInput(
                format_id=format_id,
                media_type=FORMAT_SPECS[format_id].media_type,
                byte_size=before["size"],
                sha256=before["sha256"],
            ),
            budgets=AnalysisBudgets(max_input_bytes=MAX_FILE_BYTES),
        )
        try:
            with source.open("rb") as stream:
                analysis = analyze_document(job, stream)
        except DocumentExtractionError as exc:
            raise DocumentFilesError(exc.code, str(exc), details=exc.details) from exc
        yield source, before, analysis, metadata
    finally:
        _ensure_source_unchanged(source, before)


def _read_summary(analysis: AnalysisResult) -> dict[str, Any]:
    envelope = analysis.extraction
    return {
        "sourceFormat": analysis.input.format_id,
        "manifestHash": envelope.manifest_hash,
        "completeness": envelope.completeness,
        "coverage": envelope.completeness,
        "coverageProfile": envelope.coverage.to_dict(),
        "unitCount": len(envelope.units),
        "unitTypes": dict(sorted(Counter(unit.unit_type for unit in envelope.units).items())),
        "issues": [issue.to_dict() for issue in envelope.issues],
        "engine": {
            "name": envelope.descriptor.adapter_id,
            "version": envelope.descriptor.adapter_version,
        },
        "analysis": analysis_record(analysis),
        "warnings": sorted(
            {
                issue.code
                for issue in (
                    *envelope.issues,
                    *(issue for unit in envelope.units for issue in unit.issues),
                )
                if issue.severity in {"warning", "error"}
            }
        ),
        "nativeRenderChecked": False,
    }


def inspect_file(
    path: str | Path,
    *,
    include_text: bool = True,
    include_cells: bool = True,
    max_chars: int = 20_000,
) -> dict[str, Any]:
    """Inspect source-declared content without requiring an authoring backend."""

    _validate_text_limit(max_chars)
    with _read_document(path, allow_protected_metadata=True) as (
        source,
        before,
        analysis,
        metadata,
    ):
        base = {
            **metadata,
            "schemaVersion": "document-files.inspect.v1",
            "ok": True,
            "source": before,
            "file": before,
            "sourceUnchanged": True,
            "format": metadata.get("format", source.suffix.casefold().removeprefix(".")),
            "nativeRenderChecked": False,
        }
        if analysis is None:
            return {
                **base,
                "sourceFormat": source.suffix.casefold().removeprefix("."),
                "text": "",
                "textChars": 0,
                "textIncluded": include_text,
                "textTruncated": False,
                "tableMap": {"tables": []},
                "tablesTruncated": False,
                "formFields": [],
                "completeness": "partial",
                "coverage": "partial",
                "coverageProfile": {
                    "text_content": "unverified",
                    "structure": "unverified",
                    "visual_content": "unverified",
                    "reading_order": "unverified",
                },
                "contentAccess": {
                    "ok": False,
                    "error": {
                        "code": "protected-document",
                        "message": "The HWP body is encrypted and was not opened.",
                    },
                },
                "engine": {"name": "olefile", "version": olefile.__version__},
                "supportedOperations": ["inspect-metadata"],
                "warnings": ["protected-document"],
            }
        text, truncated = (
            project_read_text(analysis, output_format="text", max_chars=max_chars)
            if include_text
            else ("", False)
        )
        table_map, fields, tables_truncated = project_tables_and_fields(
            analysis,
            source=source,
            include_cells=include_cells,
            max_cells=MAX_TABLE_CELLS_RETURNED,
            max_chars=max_chars,
        )
        summary = _read_summary(analysis)
        return {
            **base,
            **summary,
            "text": text,
            "textChars": len(text),
            "textIncluded": include_text,
            "textTruncated": truncated,
            "textRepresentation": "source-unit-order",
            "tableMap": table_map,
            "tablesTruncated": tables_truncated,
            "formFields": fields,
            "contentAccess": {"ok": True},
            "warnings": sorted(
                set(summary["warnings"] + (["content-truncated"] if truncated else []))
            ),
        }


def _require_unprotected_hwp(source: Path) -> None:
    if source.suffix.casefold() != ".hwp":
        return
    metadata = _hwp_header(source)
    if metadata["encrypted"]:
        raise DocumentFilesError(
            "protected-document",
            "The HWP body is encrypted and cannot be processed without bypassing protection.",
            details={"path": str(source), "encrypted": True},
            suggestion="Provide an authorized unprotected HWP or HWPX copy.",
        )


def extract_structure(
    path: str | Path,
    *,
    unit_offset: int = 0,
    max_units: int = DEFAULT_PUBLIC_STRUCTURED_UNITS,
    include_text: bool = True,
) -> dict[str, Any]:
    """Extract a bounded page of source-addressed structure and explicit values."""

    _validate_structured_page(unit_offset=unit_offset, max_units=max_units)
    with _read_document(path) as (_source, before, analysis, _metadata):
        projected = _project_structure(
            analysis,
            unit_offset=unit_offset,
            max_units=max_units,
            include_text=include_text,
        )
        return {**projected, "source": before, "sourceUnchanged": True}


def _validate_structured_page(*, unit_offset: int, max_units: int) -> None:
    if isinstance(unit_offset, bool) or not isinstance(unit_offset, int) or unit_offset < 0:
        raise DocumentFilesError(
            "invalid-unit-offset",
            "unit_offset must be a non-negative integer.",
            details={"minimum": 0},
        )
    if (
        isinstance(max_units, bool)
        or not isinstance(max_units, int)
        or not 1 <= max_units <= MAX_PUBLIC_STRUCTURED_UNITS
    ):
        raise DocumentFilesError(
            "invalid-unit-limit",
            "max_units is outside the supported range.",
            details={"minimum": 1, "maximum": MAX_PUBLIC_STRUCTURED_UNITS},
        )


def extract_structure_from_stream(
    job: AnalysisJob,
    source: BinaryIO,
    *,
    backend: AnalyzerBackend | None = None,
    unit_offset: int = 0,
    max_units: int = DEFAULT_PUBLIC_STRUCTURED_UNITS,
    include_text: bool = True,
) -> dict[str, Any]:
    """Extract structure from authorized bytes without a public local-path dependency."""

    _validate_structured_page(unit_offset=unit_offset, max_units=max_units)
    analysis = analyze_document(job, source, backend=backend)
    return _project_structure(
        analysis, unit_offset=unit_offset, max_units=max_units, include_text=include_text
    )


def _project_structure(
    analysis: AnalysisResult, *, unit_offset: int, max_units: int, include_text: bool
) -> dict[str, Any]:
    projected = project_structured_extraction(
        analysis.extraction,
        source_format=analysis.input.format_id,
        unit_offset=unit_offset,
        max_units=max_units,
        include_text=include_text,
    )
    return {
        "ok": True,
        **projected,
        "analysis": analysis_record(analysis),
        "nativeRenderChecked": False,
    }


def extract_file(
    path: str | Path,
    *,
    output_format: str = "text",
    max_chars: int = 200_000,
) -> dict[str, Any]:
    """Extract bounded text or Markdown without writing a work file."""

    if output_format not in {"text", "markdown"}:
        raise DocumentFilesError(
            "unsupported-extraction-format",
            "output_format must be text or markdown.",
            details={"outputFormat": output_format},
        )
    _validate_text_limit(max_chars)
    with _read_document(path) as (_source, before, analysis, _metadata):
        content, truncated = project_read_text(
            analysis, output_format=output_format, max_chars=max_chars
        )
        summary = _read_summary(analysis)
        return {
            "schemaVersion": "document-files.extract.v1",
            "ok": True,
            "source": before,
            "sourceUnchanged": True,
            **summary,
            "format": output_format,
            "representation": (
                "source-structure-markdown" if output_format == "markdown" else "source-unit-order"
            ),
            "layoutPreserved": False,
            "content": content,
            "contentChars": len(content),
            "contentSha256": _sha256_bytes(content.encode("utf-8")),
            "truncated": truncated,
            "warnings": sorted(
                set(summary["warnings"] + (["content-truncated"] if truncated else []))
            ),
        }


def _convert_to_hwpx(
    source: Path,
    output_path: str | Path,
    *,
    allow_lossy: bool,
    overwrite: bool,
) -> dict[str, Any]:
    _require_python_hwpx_backend()
    if source.suffix.casefold() != ".hwp":
        raise DocumentFilesError(
            "conversion-not-needed",
            "HWPX output conversion currently requires a binary HWP input.",
            details={"path": str(source)},
        )
    _require_unprotected_hwp(source)
    output = _output_file(
        output_path,
        suffix=".hwpx",
        source=source,
        overwrite=overwrite,
        create_parent=True,
    )
    source_before = _file_record(source)
    backend = _rhwp_backend()
    with tempfile.TemporaryDirectory(
        prefix=".document-files-convert-",
        dir=output.parent,
    ) as folder:
        staged = Path(folder) / "converted.hwpx"
        try:
            backend_report = backend.export_hwpx(source, staged)
            source_info = backend.info(source)
            output_info = backend.info(staged)
        except RhwpBackendError as exc:
            raise _translate_backend_error(exc) from exc
        safety = validate_editor_open_safety(staged)
        if not safety.ok:
            raise DocumentFilesError(
                "conversion-verification-failed",
                "The converted HWPX did not pass package and reopen checks.",
                details=safety.to_dict(),
            )

        losses: list[dict[str, Any]] = []
        ir_diff = backend_report["irDiff"]
        if not ir_diff.get("identical", False):
            losses.append(
                {
                    "kind": "intermediate-representation-difference",
                    "diffCount": ir_diff.get("diffCount"),
                    "categories": ir_diff.get("categories"),
                }
            )
        page_count_preserved = source_info.get("pageCount") == output_info.get("pageCount")
        if not page_count_preserved:
            losses.append(
                {
                    "kind": "page-count-change",
                    "before": source_info.get("pageCount"),
                    "after": output_info.get("pageCount"),
                }
            )
        if losses and not allow_lossy:
            raise DocumentFilesError(
                "conversion-loss-detected",
                "The HWP to HWPX conversion produced measurable differences.",
                details={"losses": losses, "irDiff": ir_diff},
                suggestion=(
                    "Review the differences, then retry with allow_lossy=true if acceptable."
                ),
            )
        _ensure_source_unchanged(source, source_before)
        os.replace(staged, output)
        os.chmod(output, 0o600)

    return {
        "schemaVersion": "document-files.convert.v1",
        "ok": True,
        "source": source_before,
        "sourceUnchanged": True,
        "output": _file_record(output),
        "sourceFormat": "hwp5",
        "targetFormat": "hwpx",
        "engine": {"name": "rhwp", "version": backend.version()},
        "validation": {
            "ok": safety.ok and (not losses or allow_lossy),
            "packageAndReopen": safety.to_dict(),
            "irIdentical": ir_diff.get("identical"),
            "pageCountPreserved": page_count_preserved,
            "sourceInfo": source_info,
            "outputInfo": output_info,
        },
        "losses": losses,
        "warnings": ["lossy-conversion-accepted"] if losses else [],
        "nativeRenderChecked": False,
    }


def _render_pdf(
    source: Path,
    output_path: str | Path,
    *,
    page: int | None,
    overwrite: bool,
) -> dict[str, Any]:
    output = _output_file(
        output_path,
        suffix=".pdf",
        source=source,
        overwrite=overwrite,
        create_parent=True,
    )
    source_before = _file_record(source)
    backend = _rhwp_backend()
    with tempfile.TemporaryDirectory(
        prefix=".document-files-render-",
        dir=output.parent,
    ) as folder:
        staged = Path(folder) / "rendered.pdf"
        try:
            backend_report = backend.export_pdf(source, staged, page=page)
            source_info = backend.info(source)
        except RhwpBackendError as exc:
            raise _translate_backend_error(exc) from exc
        if not staged.is_file() or not staged.read_bytes().startswith(b"%PDF-"):
            raise DocumentFilesError(
                "render-output-invalid",
                "The background PDF renderer did not create a valid PDF header.",
            )
        _ensure_source_unchanged(source, source_before)
        os.replace(staged, output)
        os.chmod(output, 0o600)
    return {
        "schemaVersion": "document-files.render.v1",
        "ok": True,
        "source": source_before,
        "sourceUnchanged": True,
        "output": _file_record(output),
        "format": "pdf",
        "page": page,
        "pageCount": 1 if page is not None else source_info.get("pageCount"),
        "engine": {"name": "rhwp", "version": backend.version()},
        "diagnostics": backend_report.get("diagnostics", ""),
        "warnings": ["background-render-not-native-hancom"],
        "nativeRenderChecked": False,
    }


def _render_svg(
    source: Path,
    output_path: str | Path,
    *,
    page: int | None,
    overwrite: bool,
) -> dict[str, Any]:
    output_dir = _absolute(output_path, strict=False)
    if output_dir.exists():
        code = "directory-overwrite-refused" if overwrite else "output-exists"
        raise DocumentFilesError(
            code,
            "SVG pages must be written to a new output directory.",
            details={"path": str(output_dir)},
            suggestion="Choose a new output directory.",
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    source_before = _file_record(source)
    backend = _rhwp_backend()
    with tempfile.TemporaryDirectory(
        prefix=".document-files-svg-",
        dir=output_dir.parent,
    ) as folder:
        staged = Path(folder)
        try:
            manifest = backend.export_svg(source, staged, page=page)
        except RhwpBackendError as exc:
            raise _translate_backend_error(exc) from exc
        svg_files = sorted(staged.glob("*.svg"))
        if not svg_files or any(b"<svg" not in item.read_bytes()[:4_096] for item in svg_files):
            raise DocumentFilesError(
                "render-output-invalid",
                "The background SVG renderer did not create valid SVG pages.",
            )
        _ensure_source_unchanged(source, source_before)
        os.replace(staged, output_dir)

    output_record = _directory_record(output_dir, suffix=".svg")
    pages: list[dict[str, Any]] = []
    for raw in manifest.get("pages", []):
        page_record = dict(raw)
        page_record["path"] = str(output_dir / Path(str(raw.get("path", ""))).name)
        pages.append(page_record)
    return {
        "schemaVersion": "document-files.render.v1",
        "ok": True,
        "source": source_before,
        "sourceUnchanged": True,
        "output": output_record,
        "format": "svg",
        "page": page,
        "pageCount": manifest.get("pageCount"),
        "renderedCount": manifest.get("renderedCount"),
        "pages": pages,
        "engine": {"name": "rhwp", "version": backend.version()},
        "diagnostics": manifest.get("diagnostics", ""),
        "warnings": ["background-render-not-native-hancom"],
        "nativeRenderChecked": False,
    }


def render_file(
    path: str | Path,
    output_path: str | Path,
    *,
    output_format: str = "auto",
    page: int | None = None,
    mode: str = "pages",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render HWP/HWPX in the background as HTML, SVG pages, or PDF."""

    source = _source_file(path, suffixes={".hwp", ".hwpx"})
    _require_unprotected_hwp(source)
    if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 0):
        raise DocumentFilesError(
            "invalid-page",
            "page must be a non-negative integer.",
            details={"page": page},
        )
    selected = output_format
    if selected == "auto":
        selected = Path(output_path).suffix.casefold().removeprefix(".")
    if selected == "html":
        if source.suffix.casefold() != ".hwpx":
            raise DocumentFilesError(
                "unsupported-render-format",
                "HTML approximation is available only for HWPX input.",
            )
        if page is not None:
            raise DocumentFilesError(
                "unsupported-render-option",
                "HTML approximation does not support selecting one page.",
            )
        return render_hwpx_preview(source, output_path, mode=mode, overwrite=overwrite)
    if selected == "pdf":
        return _render_pdf(source, output_path, page=page, overwrite=overwrite)
    if selected == "svg":
        return _render_svg(source, output_path, page=page, overwrite=overwrite)
    raise DocumentFilesError(
        "unsupported-render-format",
        "output_format must be auto, html, svg, or pdf.",
        details={"outputFormat": output_format},
    )


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    target_format: str = "auto",
    allow_lossy: bool = False,
    page: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert HWP/HWPX to HWPX, text, Markdown, SVG, or PDF."""

    source = _source_file(input_path, suffixes={".hwp", ".hwpx"})
    selected = target_format
    if selected == "auto":
        selected = Path(output_path).suffix.casefold().removeprefix(".")
    if selected == "hwpx":
        if page is not None:
            raise DocumentFilesError(
                "unsupported-conversion-option",
                "Selecting one page is supported only for SVG or PDF conversion.",
            )
        return _convert_to_hwpx(
            source,
            output_path,
            allow_lossy=allow_lossy,
            overwrite=overwrite,
        )
    if selected in {"pdf", "svg"}:
        result = render_file(
            source,
            output_path,
            output_format=selected,
            page=page,
            overwrite=overwrite,
        )
        result["schemaVersion"] = "document-files.convert.v1"
        result["targetFormat"] = selected
        return result
    if selected not in {"txt", "text", "md", "markdown"}:
        raise DocumentFilesError(
            "unsupported-conversion-format",
            "target_format must be auto, hwpx, text, markdown, svg, or pdf.",
            details={"targetFormat": target_format},
        )
    normalized = "markdown" if selected in {"md", "markdown"} else "text"
    if page is not None:
        raise DocumentFilesError(
            "unsupported-conversion-option",
            "Selecting one page is supported only for SVG or PDF conversion.",
        )
    suffix = ".md" if normalized == "markdown" else ".txt"
    output = _output_file(
        output_path,
        suffix=suffix,
        source=source,
        overwrite=overwrite,
        create_parent=True,
    )
    extraction = extract_file(source, output_format=normalized, max_chars=MAX_TEXT_CHARS)
    if extraction["truncated"] and not allow_lossy:
        raise DocumentFilesError(
            "conversion-loss-detected",
            "The extracted document exceeds the bounded conversion size.",
            details={"limitChars": MAX_TEXT_CHARS},
            suggestion="Use extraction in bounded segments or retry with allow_lossy=true.",
        )
    _atomic_write(output, extraction["content"].encode("utf-8"))
    return {
        "schemaVersion": "document-files.convert.v1",
        "ok": True,
        "source": extraction["source"],
        "sourceUnchanged": True,
        "output": _file_record(output),
        "sourceFormat": source.suffix.casefold().removeprefix("."),
        "targetFormat": normalized,
        "engine": extraction["engine"],
        "validation": {"ok": not extraction["truncated"] or allow_lossy},
        "losses": (
            [{"kind": "content-truncated", "limitChars": MAX_TEXT_CHARS}]
            if extraction["truncated"]
            else []
        ),
        "warnings": ["lossy-conversion-accepted"] if extraction["truncated"] else [],
        "nativeRenderChecked": False,
    }


def _operation_text(value: object, *, field: str, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise DocumentFilesError(
            "invalid-edit-plan",
            f"{field} must be a string.",
        )
    if not allow_empty and not value:
        raise DocumentFilesError(
            "invalid-edit-plan",
            f"{field} must not be empty.",
        )
    if len(value) > MAX_OPERATION_TEXT_CHARS:
        raise DocumentFilesError(
            "invalid-edit-plan",
            f"{field} exceeds the configured character limit.",
        )
    return value


def _plan_value(mapping: dict[str, Any], snake: str, camel: str, default: Any = None) -> Any:
    if snake in mapping:
        return mapping[snake]
    return mapping.get(camel, default)


def _normalize_edit_plan(
    plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(plan, dict) or plan.get("schemaVersion") != EDIT_PLAN_SCHEMA_VERSION:
        raise DocumentFilesError(
            "invalid-edit-plan",
            f"schemaVersion must be {EDIT_PLAN_SCHEMA_VERSION!r}.",
        )

    raw_replacements = plan.get("textReplacements", [])
    raw_cells = plan.get("tableCells", [])
    if not isinstance(raw_replacements, list) or not isinstance(raw_cells, list):
        raise DocumentFilesError(
            "invalid-edit-plan",
            "textReplacements and tableCells must be arrays.",
        )
    if not raw_replacements and not raw_cells:
        raise DocumentFilesError(
            "empty-edit-plan",
            "The edit plan contains no operations.",
        )
    if len(raw_replacements) + len(raw_cells) > MAX_EDIT_OPERATIONS:
        raise DocumentFilesError(
            "too-many-operations",
            "The edit plan exceeds the configured operation limit.",
            details={"limit": MAX_EDIT_OPERATIONS},
        )

    body_ops: list[dict[str, Any]] = []
    for index, item in enumerate(raw_replacements):
        if not isinstance(item, dict):
            raise DocumentFilesError(
                "invalid-edit-plan",
                f"textReplacements[{index}] must be an object.",
            )
        find = _operation_text(item.get("find"), field="find", allow_empty=False)
        replacement = _operation_text(item.get("replace", ""), field="replace")
        expected_count = _plan_value(item, "expected_count", "expectedCount", 1)
        if isinstance(expected_count, bool) or not isinstance(expected_count, int):
            raise DocumentFilesError(
                "invalid-edit-plan",
                f"textReplacements[{index}].expectedCount must be an integer.",
            )
        if not 1 <= expected_count <= MAX_EDIT_OPERATIONS:
            raise DocumentFilesError(
                "invalid-edit-plan",
                f"textReplacements[{index}].expectedCount is outside the supported range.",
            )
        body_ops.append(
            {
                "op": "replace_text",
                "find": find,
                "replace": replacement,
                "count": expected_count,
                "section_path": _plan_value(
                    item,
                    "section_path",
                    "sectionPath",
                    "Contents/section0.xml",
                ),
            }
        )

    table_ops: list[dict[str, Any]] = []
    table_expectations: list[dict[str, Any]] = []
    for index, item in enumerate(raw_cells):
        if not isinstance(item, dict):
            raise DocumentFilesError(
                "invalid-edit-plan",
                f"tableCells[{index}] must be an object.",
            )
        address: dict[str, int] = {}
        for field in ("tableIndex", "row", "col"):
            value = item.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DocumentFilesError(
                    "invalid-edit-plan",
                    f"tableCells[{index}].{field} must be a non-negative integer.",
                )
            address[field] = value
        text = _operation_text(item.get("text", ""), field="text")
        section_path = _plan_value(
            item,
            "section_path",
            "sectionPath",
            "Contents/section0.xml",
        )
        operation: dict[str, Any] = {
            "op": "fill_cell",
            "section_path": section_path,
            "table_index": address["tableIndex"],
            "row": address["row"],
            "col": address["col"],
            "text": text,
        }
        max_lines = _plan_value(item, "max_lines", "maxLines")
        if max_lines is not None:
            if isinstance(max_lines, bool) or not isinstance(max_lines, int) or max_lines < 1:
                raise DocumentFilesError(
                    "invalid-edit-plan",
                    f"tableCells[{index}].maxLines must be a positive integer.",
                )
            operation["max_lines"] = max_lines
        table_ops.append(operation)
        expected_old = _plan_value(item, "expected_old_text", "expectedOldText")
        if expected_old is not None:
            table_expectations.append(
                {
                    "sectionPath": section_path,
                    "tableIndex": address["tableIndex"],
                    "row": address["row"],
                    "col": address["col"],
                    "expectedOldText": _operation_text(
                        expected_old,
                        field="expectedOldText",
                    ),
                }
            )

    return body_ops, table_ops, table_expectations


def _check_table_expectations(
    source_bytes: bytes,
    expectations: list[dict[str, Any]],
) -> tuple[dict[tuple, str], dict[str, Any] | None]:
    """Check whole addressed cells before mutation, not the editor's first-line report."""

    if not expectations:
        return {}, None
    analysis_input = AnalysisInput.from_bytes(source_bytes, format_id="hwpx")
    job = AnalysisJob(
        job_id=f"edit-preflight:{analysis_input.sha256[:24]}",
        input=analysis_input,
        budgets=AnalysisBudgets(max_input_bytes=MAX_FILE_BYTES),
    )
    try:
        analysis = analyze_document(job, io.BytesIO(source_bytes))
        envelope = analysis.extraction
        profile = envelope.coverage.to_dict()
        if profile["text_content"] != "complete" or profile["structure"] != "complete":
            raise DocumentFilesError(
                "edit-precondition-unverified",
                "Whole-cell preconditions require complete text and structure observations.",
                details={"coverageProfile": profile},
                suggestion="Use a source whose target cell contents can be read completely.",
            )
        # These internal bounds cover this already-bounded envelope, not a
        # truncated public inspection page. Selector XML uses the same bytes.
        table_map, _fields, truncated = project_tables_and_fields(
            analysis,
            source=io.BytesIO(source_bytes),
            include_cells=True,
            max_cells=len(envelope.units),
            max_chars=sum(len(unit.content) + 2 for unit in envelope.units),
        )
    except DocumentExtractionError as exc:
        raise DocumentFilesError(exc.code, str(exc), details=exc.details) from exc
    observed: dict[tuple, str] = {}
    mismatches = []
    for expectation in expectations:
        section, table_index, row, col = (
            expectation[key] for key in ("sectionPath", "tableIndex", "row", "col")
        )
        tables = [
            table
            for table in table_map["tables"]
            if table.get("sectionPath") == section and table.get("tableIndex") == table_index
        ]
        cells = []
        if len(tables) == 1:
            for cell in tables[0]["cells"]:
                geometry = [cell.get(key) for key in ("row", "col", "rowSpan", "colSpan")]
                if not all(
                    isinstance(value, int) and not isinstance(value, bool) for value in geometry
                ):
                    continue
                r, c, height, width = geometry
                if height > 0 and width > 0 and r <= row < r + height and c <= col < c + width:
                    cells.append(cell)
        if truncated or len(cells) != 1 or cells[0].get("textTruncated"):
            mismatches.append(
                {
                    **expectation,
                    "actualOldText": None,
                    "reason": "missing-ambiguous-or-incomplete-cell",
                }
            )
            continue
        actual = cells[0]["text"]
        if actual != expectation["expectedOldText"]:
            mismatches.append({**expectation, "actualOldText": actual[:MAX_OPERATION_TEXT_CHARS]})
        else:
            observed[(section, table_index, row, col)] = actual
    if mismatches:
        raise DocumentFilesError(
            "stale-edit-plan",
            "One or more whole table cells could not be matched to the expected value.",
            details={"mismatches": mismatches},
            suggestion="Inspect the current document and rebuild the edit plan.",
        )
    return observed, {
        "scope": "whole-cell",
        "checked": len(expectations),
        "sourceSha256": analysis_input.sha256,
        "manifestHash": envelope.manifest_hash,
    }


def _preflight_table_operations(
    source_bytes: bytes,
    operations: list[dict[str, Any]],
    observed: dict[tuple, str],
) -> list[dict[str, Any]]:
    """Refuse conflicting cells and text that the pinned editor would silently omit."""

    # These selectors must track the pinned byte-preserving editor, not a
    # different high-level table traversal. This never runs for read-only calls.
    from hwpx.patch import _text_edit_for_paragraph
    from hwpx.table_patch import _all_paragraph_spans, _iter_table_spans, _sections, build_grid

    sections = _sections(source_bytes)
    spans_by_section = {name: _iter_table_spans(content) for name, content in sections.items()}
    grids: dict[tuple, tuple] = {}
    occupied: set[tuple] = set()
    effective = []
    for operation in operations:
        section, table_index, row, col = (
            operation[key] for key in ("section_path", "table_index", "row", "col")
        )
        address = {"sectionPath": section, "tableIndex": table_index, "row": row, "col": col}
        spans = spans_by_section.get(section, [])
        if table_index >= len(spans):
            raise DocumentFilesError(
                "table-edit-refused", "The target table is unavailable.", details=address
            )
        start, end = spans[table_index]
        key = (section, table_index)
        if key not in grids:
            table = sections[section][start:end]
            grid, report = build_grid(table)
            if not report.ok:
                raise DocumentFilesError(
                    "table-edit-refused",
                    "The target table does not have an unambiguous cell grid.",
                    details={**address, "grid": report.to_dict()},
                )
            grids[key] = (table, grid)
        table, grid = grids[key]
        cell = grid.get((row, col))
        if cell is None:
            raise DocumentFilesError(
                "table-edit-refused", "The target cell is unavailable.", details=address
            )
        physical = (section, start + cell.start, start + cell.end)
        if physical in occupied:
            raise DocumentFilesError(
                "conflicting-cell-edits",
                "Multiple operations target the same physical cell, including merged coordinates.",
                details=address,
            )
        occupied.add(physical)
        if (
            operation.get("max_lines") is None
            and observed.get((section, table_index, row, col)) == operation["text"]
        ):
            continue
        cell_bytes = table[cell.start : cell.end]
        if _iter_table_spans(cell_bytes):
            raise DocumentFilesError(
                "unsupported-nested-cell-edit",
                "Editing a cell that contains a nested table could erase the nested table text.",
                details=address,
                suggestion="Edit an ordinary cell inside the nested table instead.",
            )
        paragraphs = _all_paragraph_spans(cell_bytes)
        lines = operation["text"].split("\n")
        if len(lines) > len(paragraphs):
            raise DocumentFilesError(
                "table-text-capacity-exceeded",
                "The replacement has more lines than the existing cell paragraphs can hold.",
                details={
                    **address,
                    "requestedLines": len(lines),
                    "availableParagraphs": len(paragraphs),
                },
                suggestion=(
                    "Use text that fits the existing paragraphs "
                    "or explicitly restructure the document."
                ),
            )
        for line, (begin, finish) in zip(lines, paragraphs, strict=False):
            if line and _text_edit_for_paragraph(cell_bytes[begin:finish], line) is None:
                raise DocumentFilesError(
                    "table-text-capacity-exceeded",
                    "An existing cell paragraph cannot receive the requested line.",
                    details=address,
                )
        effective.append(operation)
    return effective


def _zip_payload_changes(before: bytes, after: bytes) -> dict[str, list[str]]:
    try:
        with ZipFile(io.BytesIO(before)) as left, ZipFile(io.BytesIO(after)) as right:
            left_names = set(left.namelist())
            right_names = set(right.namelist())
            changed = [
                name
                for name in sorted(left_names & right_names)
                if _sha256_bytes(left.read(name)) != _sha256_bytes(right.read(name))
            ]
    except BadZipFile as exc:
        raise DocumentFilesError(
            "invalid-hwpx",
            "The HWPX package is not a readable ZIP archive.",
        ) from exc
    return {
        "changed": changed,
        "added": sorted(right_names - left_names),
        "removed": sorted(left_names - right_names),
    }


def edit_hwpx(
    input_path: str | Path,
    *,
    plan: dict[str, Any],
    output_path: str | Path | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Apply bounded edits to an HWPX copy and return a measured change report."""

    _require_python_hwpx_backend()
    source = _source_file(input_path, suffixes={".hwpx"})
    output: Path | None = None
    if output_path is not None:
        output = _output_file(
            output_path,
            suffix=".hwpx",
            source=source,
            overwrite=overwrite,
            create_parent=not dry_run,
        )
    elif not dry_run:
        raise DocumentFilesError(
            "output-required",
            "An output path is required when dry_run is false.",
        )

    source_before = _file_record(source)
    source_bytes = source.read_bytes()
    body_ops, table_ops, table_expectations = _normalize_edit_plan(plan)

    table_report: dict[str, Any] | None = None
    intermediate = source_bytes
    if table_ops:
        observed, preconditions = _check_table_expectations(source_bytes, table_expectations)
        effective_ops = _preflight_table_operations(source_bytes, table_ops, observed)
        table_result = apply_table_ops(
            source_bytes,
            effective_ops,
            dry_run=True,
        )
        table_report = table_result.to_dict()
        if not table_result.ok:
            raise DocumentFilesError(
                "table-edit-refused",
                "The table edit plan could not be applied safely.",
                details=table_report,
            )
        if preconditions is not None:
            table_report["preconditions"] = preconditions
            table_report["unchangedCellCount"] = len(table_ops) - len(effective_ops)
        intermediate = table_result.data

    text_report: dict[str, Any] | None = None
    final_bytes = intermediate
    if body_ops:
        text_result = apply_body_ops(
            intermediate,
            body_ops,
            dry_run=True,
        )
        text_report = text_result.to_dict()
        if not text_result.ok:
            raise DocumentFilesError(
                "text-edit-refused",
                "The text edit plan could not be applied safely.",
                details=text_report,
            )
        final_bytes = text_result.data

    safety = validate_editor_open_safety(final_bytes)
    if not safety.ok:
        raise DocumentFilesError(
            "output-verification-failed",
            "The edited HWPX did not pass package and reopen checks.",
            details=safety.to_dict(),
        )

    changes = _zip_payload_changes(source_bytes, final_bytes)
    source_after = _file_record(source)
    if source_after != source_before:
        raise DocumentFilesError(
            "source-changed",
            "The source file changed while the edit was running.",
            details={"before": source_before, "after": source_after},
        )

    output_record: dict[str, Any] | None = None
    if not dry_run:
        assert output is not None
        _atomic_write(output, final_bytes)
        output_safety = validate_editor_open_safety(output)
        if not output_safety.ok:
            output.unlink(missing_ok=True)
            raise DocumentFilesError(
                "written-output-verification-failed",
                "The written HWPX did not pass package and reopen checks.",
                details=output_safety.to_dict(),
            )
        output_record = _file_record(output)

    return {
        "schemaVersion": EDIT_PLAN_SCHEMA_VERSION,
        "ok": True,
        "dryRun": dry_run,
        "source": source_before,
        "sourceUnchanged": True,
        "plannedOutput": str(output) if output is not None else None,
        "output": output_record,
        "changes": changes,
        "tableEdits": table_report,
        "textEdits": text_report,
        "verification": safety.to_dict(),
        "engine": {
            "name": "python-hwpx",
            "version": _package_version("python-hwpx"),
        },
        "nativeRenderChecked": False,
    }


def create_hwpx(
    output_path: str | Path,
    *,
    plan: dict[str, Any],
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a new HWPX from the python-hwpx document-plan schema."""

    _require_python_hwpx_backend()
    output = _output_file(
        output_path,
        suffix=".hwpx",
        overwrite=overwrite,
        create_parent=True,
    )
    validation = validate_document_plan(plan)
    if not validation.ok:
        raise DocumentFilesError(
            "invalid-document-plan",
            "The HWPX document plan is invalid.",
            details=validation.to_dict(),
        )

    temporary = output.parent / f".{output.name}.{os.getpid()}.hwpx"
    if temporary.exists():
        temporary.unlink()
    document = create_document_from_plan(plan)
    try:
        save_report = document.save_to_path(temporary, return_report=True)
    finally:
        document.close()
    try:
        os.chmod(temporary, 0o600)
        safety = validate_editor_open_safety(temporary)
        if not safety.ok:
            raise DocumentFilesError(
                "creation-verification-failed",
                "The created HWPX did not pass package and reopen checks.",
                details=safety.to_dict(),
            )
        quality = inspect_document_authoring_quality(temporary, plan=plan)
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "schemaVersion": "document-files.create.v1",
        "ok": True,
        "output": _file_record(output),
        "save": save_report.to_dict(),
        "planValidation": validation.to_dict(),
        "quality": quality,
        "verification": safety.to_dict(),
        "engine": {
            "name": "python-hwpx-automation",
            "version": _package_version("python-hwpx-automation"),
            "coreVersion": _package_version("python-hwpx"),
        },
        "nativeRenderChecked": False,
    }


def _document_text(source: Path) -> str:
    _require_python_hwpx_backend()
    with TextExtractor(source) as extractor:
        return extractor.extract_text(
            include_nested=True,
            object_behavior="placeholder",
            object_placeholder="[{type}]",
        )


def verify_hwpx(
    path: str | Path,
    *,
    reference_path: str | Path | None = None,
    expected_text: list[str] | None = None,
    forbidden_text: list[str] | None = None,
) -> dict[str, Any]:
    """Verify package integrity, requested text, and optional template structure."""

    _require_python_hwpx_backend()
    source = _source_file(path, suffixes={".hwpx"})
    source_before = _file_record(source)
    safety = validate_editor_open_safety(source)
    text = _document_text(source)
    expected = expected_text or []
    forbidden = forbidden_text or []
    missing = [value for value in expected if value not in text]
    present_forbidden = [value for value in forbidden if value in text]

    comparison: dict[str, Any] | None = None
    if reference_path is not None:
        reference = _source_file(reference_path, suffixes={".hwpx"})
        before = reference.read_bytes()
        after = source.read_bytes()
        reference_tables = table_summary(reference)
        output_tables = table_summary(source)

        def geometry(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    "sectionPath": table["sectionPath"],
                    "tableIndex": table["tableIndex"],
                    "rows": table["rows"],
                    "cols": table["cols"],
                    "merges": table["merges"],
                }
                for table in tables
            ]

        reference_geometry = geometry(reference_tables)
        output_geometry = geometry(output_tables)
        comparison = {
            "reference": _file_record(reference),
            "packageParts": _zip_payload_changes(before, after),
            "tableGeometryPreserved": reference_geometry == output_geometry,
            "referenceTableGeometry": reference_geometry,
            "outputTableGeometry": output_geometry,
        }

    _ensure_source_unchanged(source, source_before)
    ok = safety.ok and not missing and not present_forbidden
    return {
        "schemaVersion": "document-files.verify.v1",
        "ok": ok,
        "file": source_before,
        "sourceUnchanged": True,
        "verification": safety.to_dict(),
        "textChecks": {
            "expected": expected,
            "missing": missing,
            "forbidden": forbidden,
            "forbiddenPresent": present_forbidden,
        },
        "comparison": comparison,
        "engine": {"name": "python-hwpx", "version": _package_version("python-hwpx")},
        "nativeRenderChecked": False,
        "visualStatus": "preview-or-native-review-not-performed",
    }


def render_hwpx_preview(
    path: str | Path,
    output_path: str | Path,
    *,
    mode: str = "pages",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a background HTML preview and return page metadata."""

    _require_python_hwpx_backend()
    source = _source_file(path, suffixes={".hwpx"})
    source_before = _file_record(source)
    output = _output_file(
        output_path,
        suffix=".html",
        source=source,
        overwrite=overwrite,
        create_parent=True,
    )
    try:
        preview = render_layout_preview(source, mode=mode)
    except Exception as exc:
        raise DocumentFilesError(
            "preview-failed",
            "The background HWPX preview could not be rendered.",
            details={"errorType": type(exc).__name__, "message": str(exc)},
        ) from exc
    _atomic_write(output, preview.html.encode("utf-8"))
    _ensure_source_unchanged(source, source_before)
    return {
        "schemaVersion": "document-files.render.v1",
        "ok": True,
        "source": source_before,
        "sourceUnchanged": True,
        "output": _file_record(output),
        "mode": preview.mode,
        "pageCount": len(preview.pages),
        "pages": [page.as_dict() for page in preview.pages],
        "warnings": list(preview.warnings),
        "engine": {"name": "python-hwpx", "version": _package_version("python-hwpx")},
        "nativeRenderChecked": False,
        "previewKind": "layout-aware-html-approximation",
    }
