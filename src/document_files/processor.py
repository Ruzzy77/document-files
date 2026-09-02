"""Document extraction process used by Corpus and the public CLI.

The process owns all format-specific parsing.  Callers pass a read-only file
descriptor and receive ordered structural observations without index identity,
anchors, or authority fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from .analysis import default_registry, extract_complete
from .extraction_errors import BudgetExceededError, DocumentExtractionError, ExtractionError
from .extraction_protocol import (
    REQUEST_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    AdapterDescriptor,
)
from .extraction_registry import AdapterRegistry
from .formats import FORMAT_SPECS

DESCRIPTOR_SCHEMA_VERSION = "document-files.descriptor.v1"
MAX_PROCESS_INPUT_BYTES = 2 * 1024 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
PROCESSOR_IMPLEMENTATION_SHA256 = hashlib.sha256(
    Path(__file__).read_bytes()
    + b"\0"
    + Path(__file__).with_name("analysis.py").read_bytes()
).hexdigest()


def registry() -> AdapterRegistry:
    return default_registry()


def _public_route(
    format_id: str, active_registry: AdapterRegistry
) -> tuple[AdapterDescriptor, dict[str, Any]]:
    internal = active_registry.resolve(format_id)
    route = internal.descriptor.to_dict()
    route_digest = hashlib.sha256(
        json.dumps(route, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    config = {
        "processor_schema_version": RESULT_SCHEMA_VERSION,
        "processor_implementation_sha256": PROCESSOR_IMPLEMENTATION_SHA256,
        "route": route,
    }
    descriptor = AdapterDescriptor.from_config(
        adapter_id=f"document-files.process.{format_id}",
        adapter_version=(
            f"1.0.0+process.{PROCESSOR_IMPLEMENTATION_SHA256[:12]}"
            f".route.{route_digest[:12]}"
        ),
        config=config,
        capabilities=replace(
            internal.descriptor.capabilities,
            execution_mode="jsonl_subprocess",
        ),
    )
    return descriptor, config


def describe_format(format_id: str) -> dict[str, Any]:
    if format_id not in FORMAT_SPECS:
        raise ExtractionError(
            "unsupported document format",
            details={"format_id": format_id, "supported": sorted(FORMAT_SPECS)},
        )
    active = registry()
    descriptor, config = _public_route(format_id, active)
    return {
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "format_id": format_id,
        "media_type": FORMAT_SPECS[format_id].media_type,
        "descriptor": descriptor.to_dict(),
        "config": config,
    }


def describe_all() -> dict[str, Any]:
    active = registry()
    formats: dict[str, Any] = {}
    for format_id, specification in sorted(FORMAT_SPECS.items()):
        descriptor, config = _public_route(format_id, active)
        formats[format_id] = {
            "media_type": specification.media_type,
            "descriptor": descriptor.to_dict(),
            "config": config,
        }
    return {
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "formats": formats,
    }


def _read_request() -> Mapping[str, Any]:
    raw = sys.stdin.buffer.readline()
    if not raw or sys.stdin.buffer.read(1):
        raise ExtractionError("processor requires exactly one JSONL request")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtractionError("processor request is not valid UTF-8 JSON") from exc
    if not isinstance(request, Mapping):
        raise ExtractionError("processor request must be a JSON object")
    return request


def _request_input(
    request: Mapping[str, Any], active_registry: AdapterRegistry
) -> tuple[int, str]:
    if request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise ExtractionError(
            "processor request schema is unsupported",
            details={"schema_version": request.get("schema_version")},
        )
    if request.get("operation") != "extract":
        raise ExtractionError(
            "processor operation is unsupported",
            details={"operation": request.get("operation")},
        )
    source = request.get("input")
    if not isinstance(source, Mapping):
        raise ExtractionError("processor input must be an object")
    descriptor = request.get("adapter")
    if not isinstance(descriptor, Mapping):
        raise ExtractionError("processor adapter identity must be an object")
    file_descriptor = source.get("file_descriptor")
    path = source.get("path")
    format_id = source.get("format_id")
    if (
        source.get("kind") != "read_only_file_descriptor"
        or isinstance(file_descriptor, bool)
        or not isinstance(file_descriptor, int)
        or file_descriptor < 3
        or path != f"/dev/fd/{file_descriptor}"
        or not isinstance(format_id, str)
    ):
        raise ExtractionError("processor input descriptor is invalid")
    try:
        source_stat = os.fstat(file_descriptor)
    except OSError as exc:
        raise ExtractionError("processor input descriptor is not readable") from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise ExtractionError("processor input descriptor must be a regular file")
    if source_stat.st_size > MAX_PROCESS_INPUT_BYTES:
        raise BudgetExceededError(
            "processor input exceeds its byte budget",
            details={"count": source_stat.st_size, "limit": MAX_PROCESS_INPUT_BYTES},
        )

    current, current_config = _public_route(format_id, active_registry)
    expected = {
        "adapter_id": current.adapter_id,
        "adapter_version": current.adapter_version,
        "config_hash": current.config_hash,
    }
    if dict(descriptor) != expected or request.get("config") != current_config:
        raise ExtractionError(
            "processor adapter identity is stale or does not match the requested format",
            details={"format_id": format_id},
        )
    return file_descriptor, format_id


@contextmanager
def _materialized_input(file_descriptor: int, format_id: str):
    """Give format libraries an independently reopenable private file.

    Opening ``/dev/fd/N`` duplicates one open-file description on macOS, so
    multiple parsers can otherwise inherit each other's seek position. The
    processor therefore copies the inherited descriptor once into its own
    owner-only temporary directory and removes it when extraction ends.
    """

    try:
        before = os.fstat(file_descriptor)
        os.lseek(file_descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise ExtractionError("processor input descriptor is not seekable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ExtractionError("processor input descriptor must be a regular file")
    if before.st_size > MAX_PROCESS_INPUT_BYTES:
        raise BudgetExceededError(
            "processor input exceeds its byte budget",
            details={"count": before.st_size, "limit": MAX_PROCESS_INPUT_BYTES},
        )

    with tempfile.TemporaryDirectory(prefix="document-files-process-") as folder:
        private_path = Path(folder) / f"source.{format_id}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        destination = os.open(private_path, flags, 0o600)
        copied = 0
        try:
            while True:
                try:
                    chunk = os.read(file_descriptor, COPY_CHUNK_BYTES)
                except OSError as exc:
                    raise ExtractionError("processor could not read its input descriptor") from exc
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MAX_PROCESS_INPUT_BYTES:
                    raise BudgetExceededError(
                        "processor input exceeds its byte budget",
                        details={"count": copied, "limit": MAX_PROCESS_INPUT_BYTES},
                    )
                offset = 0
                while offset < len(chunk):
                    try:
                        written = os.write(destination, chunk[offset:])
                    except OSError as exc:
                        raise ExtractionError(
                            "processor could not materialize its private input"
                        ) from exc
                    if written <= 0:
                        raise ExtractionError(
                            "processor could not materialize its private input"
                        )
                    offset += written
        finally:
            os.close(destination)

        try:
            after = os.fstat(file_descriptor)
        except OSError as exc:
            raise ExtractionError("processor input descriptor became unavailable") from exc
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if copied != before.st_size or identity_after != identity_before:
            raise ExtractionError(
                "processor input changed while it was being materialized",
                details={"expected_bytes": before.st_size, "copied_bytes": copied},
            )
        yield private_path


def process_request() -> None:
    request = _read_request()
    active_registry = registry()
    file_descriptor, format_id = _request_input(request, active_registry)
    with _materialized_input(file_descriptor, format_id) as path:
        result = extract_complete(
            path,
            format_id=format_id,
            active_registry=active_registry,
        )
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "completeness": result.completeness,
        "coverage": result.coverage.to_dict(),
        "units": [unit.to_dict() for unit in result.units],
        "issues": [issue.to_dict() for issue in result.issues],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="document-files process")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--format", choices=tuple(sorted(FORMAT_SPECS)))
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        if args.describe:
            result = describe_format(args.format) if args.format else describe_all()
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            return
        if args.format:
            raise ExtractionError("--format requires --describe")
        process_request()
    except DocumentExtractionError as exc:
        print(
            json.dumps(
                {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
