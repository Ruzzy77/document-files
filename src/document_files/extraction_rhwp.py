"""Bounded page-text extraction through the pinned rhwp command-line backend."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from .extraction_errors import BudgetExceededError, ExtractionError
from .extraction_protocol import (
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExtractedUnit,
    ExtractionEnvelope,
    ExtractionIssue,
    _bounded_subprocess,
)

RHWP_VERSION = "0.8.6"
_SOURCE = Path(__file__)


def _platform_key() -> str | None:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-aarch64"
    if system == "darwin" and machine in {"x86_64", "amd64"}:
        return "macos-x86_64"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if system == "linux" and machine in {"arm64", "aarch64"}:
        return "linux-aarch64"
    return None


def _global_cache_root() -> Path:
    system = platform.system().casefold()
    if system == "darwin":
        return Path.home() / "Library" / "Caches" / "Document Files"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "document-files"


def _rhwp_name() -> str:
    return "rhwp.exe" if platform.system().casefold() == "windows" else "rhwp"


def _candidate_executables(runtime_root: Path, explicit: Path | None) -> tuple[Path, ...]:
    key = _platform_key()
    candidates: list[Path] = []
    configured = os.environ.get("DOCUMENT_FILES_RHWP")
    if explicit is not None:
        candidates.append(explicit)
    elif configured:
        candidates.append(Path(configured).expanduser())
    if key is not None:
        relative = Path("rhwp") / f"v{RHWP_VERSION}" / key / "bin" / _rhwp_name()
        candidates.extend((runtime_root / relative, _global_cache_root() / relative))
    return tuple(candidates)


class RhwpPageTextAdapter:
    """Read binary HWP page text without exposing the staged source path."""

    def __init__(
        self,
        runtime_root: Path | None,
        *,
        executable: Path | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root or _global_cache_root()).expanduser().resolve()
        self._explicit_executable = executable
        try:
            source_hash = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()
        except OSError as exc:
            raise ExtractionError("packaged rhwp adapter source is unavailable") from exc
        self.config = {
            "backend": "rhwp",
            "backend_version": RHWP_VERSION,
            "command": "export-text --json",
            "input_boundary": "read_only_file_descriptor",
            "output_schema_version": "1.0",
        }
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id="document-files.hwp5.rhwp-page-text",
            adapter_version=f"1.0.0+source.{source_hash[:12]}",
            config=self.config,
            capabilities=AdapterCapabilities(
                format_ids=("hwp",),
                structural_unit_types=("page_text",),
                execution_mode="jsonl_subprocess",
                preserves_reading_order=False,
                supports_geometry=False,
                supports_confidence=False,
                supports_ocr=False,
                may_emit_partial=True,
            ),
        )
        self.budgets = AdapterBudgets(
            timeout_seconds=120,
            max_input_bytes=1024 * 1024 * 1024,
            max_stdout_bytes=64 * 1024 * 1024,
            max_stderr_bytes=1024 * 1024,
            max_units=100_000,
            max_unit_content_chars=5_000_000,
            max_total_content_chars=50_000_000,
        )

    def _resolve_executable(self) -> Path:
        for candidate in _candidate_executables(
            self.runtime_root,
            self._explicit_executable,
        ):
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_file() or not os.access(resolved, os.X_OK):
                continue
            try:
                version = subprocess.run(
                    [str(resolved), "--version"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=False,
                    timeout=5,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                    },
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if version.returncode != 0 or len(version.stdout) > 256:
                continue
            reported = version.stdout.decode("utf-8", errors="replace").strip()
            normalized = reported.removeprefix("rhwp ").removeprefix("v")
            if normalized == RHWP_VERSION:
                return resolved
        raise ExtractionError(
            "the pinned rhwp backend is unavailable",
            details={"expected_version": RHWP_VERSION},
        )

    def _run(self, executable: Path, input_fd: int) -> dict:
        if os.name != "posix":
            raise ExtractionError("rhwp file-descriptor extraction requires POSIX")
        with tempfile.TemporaryDirectory(prefix="document-files-rhwp-") as temporary:
            stdout, _stderr = _bounded_subprocess(
                command=(
                    str(executable),
                    "export-text",
                    f"/dev/fd/{input_fd}",
                    "--json",
                ),
                request=b"",
                budgets=self.budgets,
                input_fd=input_fd,
                cwd=Path(temporary),
                environment={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                },
            )
        try:
            payload = json.loads(stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExtractionError("rhwp output is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ExtractionError("rhwp output must be a JSON object")
        return payload

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        if format_id != "hwp":
            raise ExtractionError(
                "rhwp adapter does not declare support for this format",
                details={"format_id": format_id},
            )
        path = Path(path)
        if not path.is_file():
            raise ExtractionError("rhwp input must be an existing regular file")
        input_bytes = path.stat().st_size
        if input_bytes > self.budgets.max_input_bytes:
            raise BudgetExceededError(
                "rhwp input exceeds its byte budget",
                details={"count": input_bytes, "limit": self.budgets.max_input_bytes},
            )
        executable = self._resolve_executable()
        input_fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            # /dev/fd inputs may duplicate an already inspected open-file offset.
            os.lseek(input_fd, 0, os.SEEK_SET)
            payload = self._run(executable, input_fd)
        finally:
            os.close(input_fd)

        if payload.get("schemaVersion") != "1.0":
            raise ExtractionError(
                "rhwp output schema version is unsupported",
                details={"schema_version": payload.get("schemaVersion")},
            )
        page_count = payload.get("pageCount")
        pages = payload.get("pages")
        if (
            not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or not 0 <= page_count <= self.budgets.max_units
            or not isinstance(pages, list)
            or len(pages) > self.budgets.max_units
        ):
            raise ExtractionError("rhwp output contains invalid page counts")

        units: list[ExtractedUnit] = []
        blank_pages = 0
        total_chars = 0
        seen_pages: set[int] = set()
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                raise ExtractionError("rhwp output contains an invalid page")
            backend_page = page.get("page")
            content = page.get("text")
            if (
                not isinstance(backend_page, int)
                or isinstance(backend_page, bool)
                or backend_page < 0
                or backend_page in seen_pages
                or not isinstance(content, str)
            ):
                raise ExtractionError(
                    "rhwp output contains invalid page text",
                    details={"page_index": index},
                )
            seen_pages.add(backend_page)
            content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
            if not content:
                blank_pages += 1
                continue
            if len(content) > self.budgets.max_unit_content_chars:
                raise BudgetExceededError(
                    "rhwp page text exceeds its character budget",
                    details={"page": backend_page + 1},
                )
            total_chars += len(content)
            if total_chars > self.budgets.max_total_content_chars:
                raise BudgetExceededError("rhwp text exceeds its aggregate character budget")
            units.append(
                ExtractedUnit(
                    unit_type="page_text",
                    structure_path={
                        "page": backend_page + 1,
                        "backend_page": backend_page,
                    },
                    content=content,
                    derivation_method="native_text",
                    quality_flags=("binary_hwp", "structure_partial"),
                )
            )

        issues = [
            ExtractionIssue(
                code="hwp_page_text_partial",
                message=(
                    "Page text is searchable, but HWP tables, headings, fields, and "
                    "embedded-object relationships are not reconstructed."
                ),
                severity="warning",
                details={
                    "backend": "rhwp",
                    "backend_version": RHWP_VERSION,
                    "declared_pages": page_count,
                    "returned_pages": len(pages),
                    "text_pages": len(units),
                },
            )
        ]
        if blank_pages:
            issues.append(
                ExtractionIssue(
                    code="hwp_page_without_text",
                    message="One or more HWP pages did not contain extractable text.",
                    severity="warning",
                    details={"count": blank_pages},
                )
            )
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness="partial",
            units=units,
            issues=issues,
        )
