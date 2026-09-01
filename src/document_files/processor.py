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
import sys
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .extraction_errors import BudgetExceededError, DocumentExtractionError, ExtractionError
from .extraction_protocol import (
    REQUEST_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    AdapterDescriptor,
    ExtractionEnvelope,
)
from .extraction_registry import AdapterRegistry, build_default_registry
from .formats import FORMAT_SPECS

DESCRIPTOR_SCHEMA_VERSION = "document-files.descriptor.v1"
DEFAULT_COMPLETION_SECONDS = 580.0
MAX_CONTINUATION_PASSES = 1_000


def _runtime_root() -> Path:
    configured = os.environ.get("DOCUMENT_FILES_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Document Files"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "document-files"


def registry() -> AdapterRegistry:
    return build_default_registry(_runtime_root())


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
        "route": route,
    }
    descriptor = AdapterDescriptor.from_config(
        adapter_id=f"document-files.process.{format_id}",
        adapter_version=f"1.0.0+route.{route_digest[:12]}",
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
    """Extract one document and finish every safely resumable bounded range."""

    active_registry = active_registry or registry()
    adapter = active_registry.resolve(format_id)
    result = adapter.extract(path, format_id=format_id)
    started = time.monotonic()
    passes = 0
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
) -> tuple[Path, str]:
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
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        probe = os.open(path, flags)
        os.close(probe)
    except OSError as exc:
        raise ExtractionError("processor input descriptor is not readable") from exc

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
    return Path(path), format_id


def process_request() -> None:
    request = _read_request()
    active_registry = registry()
    path, format_id = _request_input(request, active_registry)
    result = extract_complete(
        path,
        format_id=format_id,
        active_registry=active_registry,
    )
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "completeness": result.completeness,
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
