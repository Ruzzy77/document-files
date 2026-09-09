"""Offline-only heavy recognition process. No document content or prompt logs."""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import io
import json
import logging
import os
import sys
import time
from dataclasses import fields
from importlib.metadata import version
from pathlib import Path

from .docling_adapter import RecognitionConfig


def _offline_network_guard(event, _args):
    if event in {"socket.connect", "socket.getaddrinfo", "urllib.Request"}:
        raise PermissionError("recognition network access is disabled")


def page_batches(
    content: bytes,
    config: RecognitionConfig,
    convert_page,
    *,
    on_page=None,
    completed_pages=(),
    before_page=None,
) -> dict:
    """Keep at most one page's framework result alive; completed pages are plain JSON."""
    import pypdfium2 as pdfium

    started = time.monotonic()
    source_hash = hashlib.sha256(content).hexdigest()
    if any(type(p) is not int or not 1 <= p <= config.max_pages for p in completed_pages):
        raise ValueError("invalid completed recognition pages")
    skipped = set(completed_pages)
    if len(skipped) != len(completed_pages):
        raise ValueError("duplicate completed recognition pages")
    completed, processed, retained, issues = sorted(skipped), sorted(skipped), [], []
    total_bytes = 0
    document = pdfium.PdfDocument(content)
    try:
        total_pages = len(document)
        if any(p > total_pages for p in skipped):
            raise ValueError("completed page outside source document")
        for index in range(min(total_pages, config.max_pages)):
            if index + 1 in skipped:
                continue
            if time.monotonic() - started >= config.timeout_seconds:
                issues.append({"code": "recognition_timeout"})
                break
            subset = pdfium.PdfDocument.new()
            try:
                subset.import_pages(document, pages=[index])
                stream = io.BytesIO()
                subset.save(stream)
                page_content = stream.getvalue()
            finally:
                subset.close()
            try:
                if before_page:
                    before_page(index + 1)
                exported, status, *observations = convert_page(page_content)

                # A single-page conversion numbers its page 1. Preserve its local
                # location and remap every page locator into the original PDF.
                def remap(value, index=index):
                    if isinstance(value, dict):
                        for key, item in list(value.items()):
                            if key == "page_no" and type(item) is int:
                                value["batch_page_no"] = item
                                value[key] = index + 1
                            elif key == "pages" and isinstance(item, dict):
                                value[key] = {str(index + 1): remap(v) for v in item.values()}
                            else:
                                value[key] = remap(item)
                    elif isinstance(value, list):
                        return [remap(item) for item in value]
                    return value

                page = {
                    "page": index + 1,
                    "status": status,
                    "sourceSha256": source_hash,
                    "document": remap(exported),
                }
                if observations:
                    page["sourceObservations"] = remap(observations[0])
                encoded_size = len(json.dumps(page, ensure_ascii=False, allow_nan=False).encode())
                if total_bytes + encoded_size > config.max_output_bytes - 4096:
                    issues.append({"code": "recognition_output_budget_exceeded"})
                    break
                total_bytes += encoded_size
                if on_page:
                    on_page(page)
                else:
                    retained.append(page)
                processed.append(index + 1)
                if status == "complete":
                    completed.append(index + 1)
                if status != "complete":
                    issues.append({"code": "recognition_page_partial", "page": index + 1})
                if observations and any(
                    issue.get("code")
                    in {"table_ocr_repair_budget_exceeded", "table_ocr_repair_timeout"}
                    for issue in observations[0].get("issues", [])
                ):
                    # Pause at the first unfinished repair page instead of spending
                    # the remainder of the attempt re-observing later pages.
                    break
                del exported, page
                gc.collect()
            except Exception:
                issues.append({"code": "recognition_page_failed", "page": index + 1})
                break
        if total_pages > config.max_pages:
            issues.append({"code": "recognition_page_budget_exceeded"})
        return {
            "status": "partial" if issues else "complete",
            "pageResults": retained,
            "issues": issues,
            "completedPages": completed,
            "processedPages": processed,
            "pageCount": total_pages,
            "sourceSha256": source_hash,
            "batchSize": 1,
        }
    finally:
        document.close()


def export_source_observations(pages) -> dict:
    """Copy typed post-OCR-merge cells before one-page framework memory is released.

    These are not every raw OCR detection: Docling may already filter overlaps
    against PDF text. Never infer OCR provenance from membership in page.cells.
    """
    cells, unavailable = [], []
    for page in pages:
        parsed = getattr(page, "parsed_page", None)
        if parsed is None:
            unavailable.append(page.page_no)
            continue
        for ordinal, cell in enumerate(parsed.textline_cells):
            from_ocr = getattr(cell, "from_ocr", None)
            if type(from_ocr) is not bool:
                unavailable.append(page.page_no)
                continue
            cells.append(
                {
                    "page_no": page.page_no,
                    "ordinal": ordinal,
                    "backendCellIndex": cell.index,
                    "sourceKind": "ocr" if from_ocr else "pdf_text",
                    "fromOcr": from_ocr,
                    "text": cell.text,
                    "raw": cell.orig,
                    "confidence": cell.confidence,
                    "bbox": cell.to_bounding_box().model_dump(mode="json"),
                    "rectangle": cell.rect.model_dump(mode="json"),
                }
            )
    return {
        "version": "document-files.recognition-source-observations.v1",
        "stage": "docling_post_ocr_merge",
        "coverage": "post_merge_cells_only_not_all_raw_ocr_detections",
        "unavailablePages": sorted(set(unavailable)),
        "cells": cells,
    }


def conversion_page_status(status, observations):
    retry_codes = {"table_ocr_repair_budget_exceeded", "table_ocr_repair_timeout"}
    incomplete = any(i.get("code") in retry_codes for i in observations.get("issues", []))
    return "complete" if status == "success" and not incomplete else "partial"


def recognize(
    content: bytes,
    config: RecognitionConfig,
    *,
    on_page=None,
    completed_pages=(),
    restored_pages=None,
) -> dict:
    config.validate()
    # Validate local model presence before importing a framework that could try to fetch it.
    artifacts = Path(config.artifacts_path)
    layout = artifacts / "docling-project--docling-layout-heron"
    table = (
        artifacts
        / "docling-project--docling-models"
        / "model_artifacts"
        / "tableformer"
        / "accurate"
    )
    if not (layout / "config.json").is_file() or not (table / "tm_config.json").is_file():
        return {"status": "unavailable", "issues": [{"code": "recognition_models_unavailable"}]}
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TESSDATA_PREFIX": config.tessdata_path,
        }
    )
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import (
        LayoutObjectDetectionOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    from .docling_pipeline import pipeline_class

    options = PdfPipelineOptions(
        artifacts_path=artifacts,
        accelerator_options=AcceleratorOptions(
            device=AcceleratorDevice.CPU, num_threads=config.threads
        ),
        enable_remote_services=False,
        allow_external_plugins=False,
        do_ocr=True,
        ocr_options=TesseractCliOcrOptions(
            lang=["kor", "eng"],
            tesseract_cmd=config.tesseract_cmd,
            path=config.tessdata_path,
        ),
        do_table_structure=True,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.ACCURATE, do_cell_matching=True
        ),
        layout_options=LayoutObjectDetectionOptions.from_preset("layout_heron_default"),
        do_picture_description=False,
        do_picture_classification=False,
        do_code_enrichment=False,
        do_formula_enrichment=False,
        generate_parsed_pages=True,
        generate_page_images=False,
        generate_picture_images=False,
        document_timeout=float(config.timeout_seconds),
    )
    source_snapshots = {}
    active_restore = {}
    worker_pipeline = pipeline_class(config, source_snapshots, active_restore)

    def before_page(page_number):
        active_restore.clear()
        active_restore.update((restored_pages or {}).get(page_number, {}))

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=worker_pipeline,
                pipeline_options=options,
            )
        }
    )

    def convert_page(page_content):
        source_snapshots.clear()
        converted = converter.convert(
            DocumentStream(name="page.pdf", stream=io.BytesIO(page_content)),
            raises_on_error=False,
            max_num_pages=1,
        )
        try:
            status = getattr(converted.status, "value", str(converted.status))
            observations = export_source_observations(converted.pages)
            if source_snapshots:
                observations["cells"] = [
                    {**cell, "page_no": page_no}
                    for page_no, snapshot in source_snapshots.items()
                    for cell in [*snapshot["original"], *snapshot["supplemental"]]
                ]
                observations["tableRepairs"] = [
                    repair
                    for snapshot in source_snapshots.values()
                    for repair in snapshot["repairs"]
                ]
                observations["issues"] = [
                    issue for snapshot in source_snapshots.values() for issue in snapshot["issues"]
                ]
                observations["stage"] = "original_ocr_and_separate_table_repair"
                observations["originalOCRFingerprint"] = next(iter(source_snapshots.values()))[
                    "originalOCRFingerprint"
                ]
            return (
                converted.document.export_to_dict(),
                conversion_page_status(status, observations),
                observations,
            )
        finally:
            del converted

    result = page_batches(
        content,
        config,
        convert_page,
        on_page=on_page,
        completed_pages=completed_pages,
        before_page=before_page,
    )
    result["provenance"] = {
        "engine": "docling",
        "version": version("docling"),
        "pipeline": "StandardPdfPipeline",
        "device": "cpu",
        "layout": "Heron",
        "table": "TableFormer accurate",
        "ocr": "Tesseract CLI",
        "languages": ["kor", "eng"],
        "remoteServices": False,
        "modelDownloads": False,
        "batchSize": 1,
        "pageMapping": "single_page_pdf_to_original_page",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--completed-pages", default="[]")
    parser.add_argument("--partial-pages-file")
    args = parser.parse_args()
    config = None
    protocol_output = sys.stdout.buffer

    def emit_page(page):
        record = {"type": "page", "pageResult": page}
        protocol_output.write(
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode() + b"\n"
        )
        protocol_output.flush()

    # Both Python logs and dependency stdout/stderr are suppressed in this worker only.
    logging.disable(logging.CRITICAL)
    sys.addaudithook(_offline_network_guard)
    try:
        raw_config = json.loads(args.config)
        if set(raw_config) - {f.name for f in fields(RecognitionConfig)}:
            raise ValueError
        completed_pages = json.loads(args.completed_pages)
        if not isinstance(completed_pages, list):
            raise ValueError
        config = RecognitionConfig(**raw_config)
        config.validate()
        content = sys.stdin.buffer.read(128 * 1024 * 1024 + 1)
        if len(content) > 128 * 1024 * 1024:
            raise ValueError
        restored_pages = {}
        if args.partial_pages_file:
            raw = Path(args.partial_pages_file).read_bytes()
            if len(raw) > config.max_output_bytes:
                raise ValueError
            for page in json.loads(raw):
                if (
                    page.get("sourceSha256") != hashlib.sha256(content).hexdigest()
                    or page.get("status") != "partial"
                ):
                    raise ValueError
                restored_pages[page["page"]] = page.get("sourceObservations", {})
        with (
            open(os.devnull, "w") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            result = recognize(
                content,
                config,
                on_page=emit_page,
                completed_pages=completed_pages,
                restored_pages=restored_pages,
            )
    except ImportError:
        result = {
            "status": "unavailable",
            "issues": [{"code": "recognition_dependencies_unavailable"}],
        }
    except Exception:
        result = {"status": "partial", "issues": [{"code": "recognition_failed"}]}
    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if config is not None and len(serialized) > config.max_output_bytes:
        serialized = (
            b'{"status":"partial","issues":[{"code":"recognition_output_budget_exceeded"}]}'
        )
    protocol_output.write(serialized + b"\n")
    protocol_output.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
