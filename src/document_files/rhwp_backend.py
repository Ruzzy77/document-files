"""Bounded adapter for the pinned rhwp command-line backend."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

RHWP_VERSION = "0.8.2"
MAX_BACKEND_OUTPUT_BYTES = 32 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 120


class RhwpBackendError(RuntimeError):
    """A typed backend failure that can be translated at the public boundary."""

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


def _platform_key() -> str | None:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-aarch64"
    if system == "darwin" and machine in {"x86_64", "amd64"}:
        return "macos-x86_64"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if system == "windows" and machine in {"x86_64", "amd64"}:
        return "windows-x86_64"
    return None


def _cache_executable() -> Path | None:
    key = _platform_key()
    if key is None:
        return None
    name = "rhwp.exe" if key == "windows-x86_64" else "rhwp"
    system = platform.system().casefold()
    if system == "darwin":
        cache_root = Path.home() / "Library" / "Caches" / "Document Files"
    elif system == "windows":
        cache_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        cache_root = cache_root / "Document Files" / "Cache"
    else:
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        cache_root = cache_root / "document-files"
    return (
        cache_root
        / "rhwp"
        / f"v{RHWP_VERSION}"
        / key
        / "bin"
        / name
    )


def resolve_rhwp() -> Path | None:
    """Resolve an explicitly configured, PATH, or provisioned rhwp executable."""

    candidates: list[Path] = []
    configured = os.environ.get("DOCUMENT_FILES_RHWP")
    if configured:
        candidates.append(Path(configured).expanduser())
    discovered = shutil.which("rhwp")
    if discovered:
        candidates.append(Path(discovered))
    cached = _cache_executable()
    if cached is not None:
        candidates.append(cached)

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return None


class RhwpBackend:
    """Run the rhwp backend with bounded stdout, stderr, and execution time."""

    def __init__(self, executable: str | Path | None = None) -> None:
        resolved = Path(executable).resolve(strict=True) if executable else resolve_rhwp()
        if resolved is None or not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise RhwpBackendError(
                "backend-unavailable",
                "The pinned rhwp backend is not installed or executable.",
                details={"expectedVersion": RHWP_VERSION},
                suggestion="Run scripts/provision_rhwp.py once, then retry.",
            )
        self.executable = resolved

    def _run(
        self,
        args: list[str],
        *,
        allowed_exit_codes: set[int] | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[str, str, int]:
        allowed = allowed_exit_codes or {0}
        with tempfile.TemporaryFile() as stdout_stream, tempfile.TemporaryFile() as stderr_stream:
            try:
                completed = subprocess.run(  # noqa: S603
                    [str(self.executable), *args],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    check=False,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise RhwpBackendError(
                    "backend-timeout",
                    "The rhwp backend exceeded the operation time limit.",
                    details={"command": args[0], "timeoutSeconds": timeout},
                ) from exc
            stdout_size = stdout_stream.tell()
            stderr_size = stderr_stream.tell()
            if max(stdout_size, stderr_size) > MAX_BACKEND_OUTPUT_BYTES:
                raise RhwpBackendError(
                    "backend-output-too-large",
                    "The rhwp backend returned more diagnostic data than allowed.",
                    details={
                        "command": args[0],
                        "stdoutBytes": stdout_size,
                        "stderrBytes": stderr_size,
                        "limit": MAX_BACKEND_OUTPUT_BYTES,
                    },
                )
            stdout_stream.seek(0)
            stderr_stream.seek(0)
            stdout = stdout_stream.read().decode("utf-8", errors="replace")
            stderr = stderr_stream.read().decode("utf-8", errors="replace")

        if completed.returncode not in allowed:
            raise RhwpBackendError(
                "backend-command-failed",
                "The rhwp backend could not complete the document operation.",
                details={
                    "command": args[0],
                    "exitCode": completed.returncode,
                    "stderr": stderr[-8_000:],
                },
            )
        return stdout, stderr, completed.returncode

    def run_json(
        self,
        args: list[str],
        *,
        allowed_exit_codes: set[int] | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[dict[str, Any], str, int]:
        stdout, stderr, return_code = self._run(
            args,
            allowed_exit_codes=allowed_exit_codes,
            timeout=timeout,
        )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RhwpBackendError(
                "backend-response-invalid",
                "The rhwp backend returned an invalid JSON response.",
                details={"command": args[0], "stdout": stdout[-4_000:]},
            ) from exc
        if not isinstance(payload, dict):
            raise RhwpBackendError(
                "backend-response-invalid",
                "The rhwp backend JSON response must be an object.",
                details={"command": args[0]},
            )
        return payload, stderr, return_code

    def version(self) -> str:
        stdout, _, _ = self._run(["--version"])
        return stdout.strip().removeprefix("rhwp v")

    def capabilities(self) -> dict[str, Any]:
        payload, _, _ = self.run_json(["capabilities"])
        return payload

    def info(self, source: Path) -> dict[str, Any]:
        payload, _, _ = self.run_json(["info", str(source), "--json"])
        return payload

    def text_pages(self, source: Path) -> dict[str, Any]:
        payload, _, _ = self.run_json(["export-text", str(source), "--json"])
        return payload

    def tables(self, source: Path) -> dict[str, Any]:
        payload, _, _ = self.run_json(["export-tables", str(source), "--json"])
        return payload

    def fields(self, source: Path) -> dict[str, Any]:
        payload, _, _ = self.run_json(["fields", str(source), "--json"])
        return payload

    def markdown(self, source: Path) -> str:
        with tempfile.TemporaryDirectory(prefix="document-files-markdown-") as folder:
            destination = Path(folder)
            self._run(["export-markdown", str(source), "-o", str(destination)])
            files = sorted(destination.glob("*.md"))
            if not files:
                raise RhwpBackendError(
                    "backend-output-missing",
                    "The rhwp backend did not create a Markdown output.",
                    details={"command": "export-markdown"},
                )
            return "\n\n".join(path.read_text(encoding="utf-8") for path in files)

    def export_hwpx(self, source: Path, output: Path) -> dict[str, Any]:
        _, stderr, _ = self._run(["export-hwpx", str(source), str(output)])
        diff, diff_stderr, diff_code = self.run_json(
            ["ir-diff", str(source), str(output), "--json"],
            allowed_exit_codes={0, 3},
        )
        return {
            "conversionDiagnostics": stderr.strip(),
            "irDiff": diff,
            "irDiffExitCode": diff_code,
            "irDiffDiagnostics": diff_stderr.strip(),
        }

    def export_svg(
        self,
        source: Path,
        output_dir: Path,
        *,
        page: int | None = None,
    ) -> dict[str, Any]:
        args = ["export-svg", str(source), "-o", str(output_dir), "--json"]
        if page is not None:
            args.extend(["--page", str(page)])
        payload, stderr, _ = self.run_json(args)
        payload["diagnostics"] = stderr.strip()
        return payload

    def export_pdf(
        self,
        source: Path,
        output: Path,
        *,
        page: int | None = None,
    ) -> dict[str, Any]:
        args = ["export-pdf", str(source), "-o", str(output)]
        if page is not None:
            args.extend(["--page", str(page)])
        stdout, stderr, _ = self._run(args)
        return {"diagnostics": "\n".join(value for value in (stdout, stderr) if value).strip()}


def backend_status() -> dict[str, Any]:
    """Describe rhwp availability without raising when it is not provisioned."""

    executable = resolve_rhwp()
    if executable is None:
        return {
            "available": False,
            "expectedVersion": RHWP_VERSION,
            "executable": None,
            "reason": "not-installed",
        }
    try:
        backend = RhwpBackend(executable)
        raw_capabilities = backend.capabilities()
        version = backend.version()
    except RhwpBackendError as exc:
        return {
            "available": False,
            "expectedVersion": RHWP_VERSION,
            "executable": str(executable),
            "reason": exc.code,
            "details": exc.details,
        }
    relevant_commands = {
        "info",
        "export-text",
        "export-markdown",
        "export-tables",
        "fields",
        "export-svg",
        "export-pdf",
        "export-hwpx",
        "ir-diff",
    }
    commands = [
        {
            key: value
            for key, value in command.items()
            if key in {"name", "available", "category", "flags", "json", "requiresFeature"}
        }
        for command in raw_capabilities.get("commands", [])
        if command.get("name") in relevant_commands
    ]
    return {
        "available": version == RHWP_VERSION,
        "expectedVersion": RHWP_VERSION,
        "version": version,
        "executable": str(executable),
        "capabilities": {
            "schemaVersion": raw_capabilities.get("schemaVersion"),
            "formats": raw_capabilities.get("formats"),
            "commands": commands,
            "exitCodes": raw_capabilities.get("exitCodes"),
        },
        "reason": None if version == RHWP_VERSION else "version-mismatch",
    }
