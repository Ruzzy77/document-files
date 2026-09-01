"""Bounded, read-only pypdf repair for explicitly selected empty PDF pages."""

from __future__ import annotations

import json
import os
import resource
import stat
import sys
import threading
import time

from pypdf import PdfReader, filters
from pypdf.errors import LimitReachedError, PyPdfError
from pypdf.generic import ArrayObject

_READ_ERRORS = (
    ArithmeticError,
    LookupError,
    MemoryError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    PyPdfError,
)


def main():
    request = json.loads(sys.stdin.buffer.readline(1_048_577))
    source, config, budgets = request["input"], request["config"], request["budgets"]
    fd = source["file_descriptor"]
    if (
        request["schema_version"] != "document-files.extraction-request.v2"
        or request["operation"] != "extract"
        or source["kind"] != "read_only_file_descriptor"
        or source["format_id"] != "pdf"
        or source["path"] != f"/dev/fd/{fd}"
        or not stat.S_ISREG(os.fstat(fd).st_mode)
        or os.fstat(fd).st_size > budgets["max_input_bytes"]
    ):
        raise ValueError("invalid read-only PDF request")
    pages = config["page_numbers"]
    if (
        not isinstance(pages, list)
        or not 1 <= len(pages) <= 2_000
        or any(type(page) is not int or not 1 <= page <= 1_000_000 for page in pages)
        or pages != sorted(set(pages))
    ):
        raise ValueError("invalid PDF page selection")
    # macOS does not enforce RLIMIT_AS reliably. Bound decoded streams and stop
    # this disposable worker if sampled resident memory exceeds one GiB.
    stream_limit = 32 * 1024 * 1024
    filters.ZLIB_MAX_OUTPUT_LENGTH = min(
        getattr(filters, "ZLIB_MAX_OUTPUT_LENGTH", stream_limit), stream_limit
    )

    def watch_memory():
        multiplier = 1 if sys.platform == "darwin" else 1024
        while True:
            if (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * multiplier
                > 1024**3
            ):
                os._exit(3)
            time.sleep(0.02)

    threading.Thread(target=watch_memory, daemon=True).start()
    deadline = time.monotonic() + budgets["timeout_seconds"]
    units, issues, characters = [], [], 0
    with os.fdopen(os.dup(fd), "rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("encrypted PDF")
        total = len(reader.pages)
        for page in pages:
            if page > total:
                raise ValueError("selected page is outside the PDF")
            if time.monotonic() >= deadline:
                issues.append(
                    {
                        "code": "pdf_page_fallback_limit",
                        "severity": "warning",
                        "message": "The selected pages exceeded the fallback time budget.",
                        "details": {"next_page": page},
                    }
                )
                break
            try:
                source_page = reader.pages[page - 1]
                contents = source_page.get("/Contents")
                if contents is not None:
                    contents = contents.get_object()
                    streams = (
                        contents if isinstance(contents, ArrayObject) else [contents]
                    )
                    decoded_bytes = 0
                    for content in streams:
                        decoded_bytes += len(content.get_object().get_data())
                        if decoded_bytes > stream_limit:
                            raise LimitReachedError(
                                "decoded page content exceeds its byte budget"
                            )
                text = (source_page.extract_text() or "").strip()
                text = text.encode("utf-8", errors="replace").decode("utf-8")
                if not text:
                    continue
                if (
                    len(text) > budgets["max_unit_content_chars"]
                    or characters + len(text) > budgets["max_total_content_chars"]
                    or len(units) >= budgets["max_units"]
                ):
                    issues.append(
                        {
                            "code": "pdf_page_fallback_limit",
                            "severity": "warning",
                            "message": "One fallback page exceeds the result budget.",
                            "details": {"page": page},
                        }
                    )
                    continue
                units.append(
                    {
                        "unit_type": "page",
                        "structure_path": {"page": page},
                        "content": text,
                        "derivation_method": "native_text",
                        "quality_flags": ["pypdf_fallback", "reading_order_unverified"],
                        "issues": [],
                    }
                )
                characters += len(text)
            except LimitReachedError:
                issues.append(
                    {
                        "code": "pdf_page_fallback_limit",
                        "severity": "warning",
                        "message": "The page exceeded the bounded native reader's decoding limit.",
                        "details": {"page": page, "stage": "page_decoding"},
                    }
                )
            except _READ_ERRORS as exc:
                issues.append(
                    {
                        "code": "pdf_page_fallback_failed",
                        "severity": "warning",
                        "message": "The secondary native reader could not read one page.",
                        "details": {"page": page, "error_type": type(exc).__name__},
                    }
                )
    print(
        json.dumps(
            {
                "schema_version": "document-files.extraction-result.v2",
                "completeness": "partial" if issues or not units else "complete",
                "units": units,
                "issues": issues,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except _READ_ERRORS:
        print("Bounded PDF page fallback failed.", file=sys.stderr)
        raise SystemExit(2) from None
