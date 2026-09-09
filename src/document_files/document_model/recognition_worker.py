"""Offline-only heavy recognition process. No document content or prompt logs."""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import io
import json
import logging
import math
import os
import sys
import time
from copy import deepcopy
from dataclasses import fields
from importlib.metadata import version
from pathlib import Path

from .docling_adapter import RecognitionConfig


def _offline_network_guard(event, _args):
    if event in {"socket.connect", "socket.getaddrinfo", "urllib.Request"}:
        raise PermissionError("recognition network access is disabled")


def _render_coordinate_evidence(page, bitmap):
    """Measure this bitmap's PDFium transform, not a recognition coordinate map."""
    evidence = {
        "version": "document-files.render-coordinates.v1",
        "scope": "full_render_to_source_page_only",
        "status": "failed",
        "basis": "pdfium_device_page_conversion",
        "pixelCoordinateOrigin": "TOPLEFT",
        "pageCoordinateSpace": "pdfium_original_page_canvas",
        "recognitionAlignmentVerified": False,
        "pageContentCompletenessVerified": False,
        "ocrTruthVerified": False,
        "samples": [],
    }
    try:
        converter = bitmap.get_posconv(page)
        width, height = bitmap.width, bitmap.height
        origin = converter.to_page(0, 0)
        x_end, y_end = converter.to_page(width, 0), converter.to_page(0, height)
        a, b = ((x_end[i] - origin[i]) / width for i in (0, 1))
        c, d = ((y_end[i] - origin[i]) / height for i in (0, 1))
        e, f = origin
        matrix = [a, b, c, d, e, f]
        determinant = a * d - b * c
        if not all(math.isfinite(v) for v in matrix) or determinant == 0:
            raise ValueError
        inverse = [
            d / determinant,
            -b / determinant,
            -c / determinant,
            a / determinant,
            (c * f - d * e) / determinant,
            (b * e - a * f) / determinant,
        ]
        if not all(math.isfinite(v) for v in inverse):
            raise ValueError
        evidence.update(
            renderArguments=list(converter.pos_args),
            pixelToPageAffine=matrix,
            pageToPixelAffine=inverse,
            affineConvention="x_out=a*x+c*y+e;y_out=b*x+d*y+f",
            # The native conversion has finite precision even for exact page
            # boxes. Bound the measured error in pixels, not guessed PDF units.
            pixelAffineTolerance=0.001,
            pixelRoundTripTolerance=0,
        )

        def pixel_error(actual, expected):
            dx, dy = actual[0] - expected[0], actual[1] - expected[1]
            return max(
                abs(inverse[0] * dx + inverse[2] * dy),
                abs(inverse[1] * dx + inverse[3] * dy),
            )

        # Include the full rectangle's boundary and interior integer pixels. The
        # page-to-device API rounds to integers; it must recover these same pixels.
        for x, y in dict.fromkeys(
            (x, y) for x in (0, width // 2, width) for y in (0, height // 2, height)
        ):
            point = converter.to_page(x, y)
            if not all(math.isfinite(v) for v in point):
                raise ValueError
            returned = converter.to_bitmap(*point)
            evidence["samples"].append(
                {"pixel": [x, y], "page": list(point), "returnedPixel": list(returned)}
            )
            if (
                tuple(returned) != (x, y)
                or pixel_error(point, (a * x + c * y + e, b * x + d * y + f))
                > evidence["pixelAffineTolerance"]
            ):
                raise ValueError
        corners = [
            s["page"]
            for s in evidence["samples"]
            if s["pixel"][0] in (0, width) and s["pixel"][1] in (0, height)
        ]
        bounds = [
            min(p[0] for p in corners),
            min(p[1] for p in corners),
            max(p[0] for p in corners),
            max(p[1] for p in corners),
        ]
        # This capture uses zero additional crop/rotation. Its converted corners
        # must span the effective source-page box, including any nonzero origin.
        effective = page.get_bbox()
        if any(
            pixel_error(bounds[start : start + 2], effective[start : start + 2])
            > evidence["pixelAffineTolerance"]
            for start in (0, 2)
        ):
            raise ValueError
        evidence.update(status="verified", mappedPageBounds=bounds)
    except Exception:
        evidence["failure"] = "render_coordinate_conversion_unverified"
    return evidence


def capture_full_page_render(document, index, source_hash, *, scale=3.0, max_pixels=16000000):
    """Capture the full displayed PDF page, not a claim that its ink was interpreted.

    The caller initializes form rendering before acquiring page handles. Pixels are
    transient; their exact RGB digest and rendering recipe remain source-addressed.
    """
    from pypdfium2 import PDFIUM_INFO

    from .recognition_sources import page_render_fingerprint

    record = {
        "version": "document-files.full-page-render.v1",
        "sourceSha256": source_hash,
        "page_no": index + 1,
        "status": "failed",
        "engine": "pypdfium2",
        "engineVersion": version("pypdfium2"),
        "pdfiumVersion": str(PDFIUM_INFO),
        "profile": {
            "scale": scale,
            "maxPixels": max_pixels,
            "additionalRotation": 0,
            "crop": [0, 0, 0, 0],
            "background": [255, 255, 255, 255],
            "drawAnnotations": True,
            "drawForms": True,
            "pixelMode": "RGB",
        },
        "scope": "full_displayed_page_media_crop_intersection",
        "visualContentCoverage": "not_assessed",
        "ocrTruthVerified": False,
        "coordinateAlignmentToRecognition": "not_verified",
        "issues": [],
    }
    page = bitmap = image = None
    try:
        if not math.isfinite(scale) or scale <= 0 or type(max_pixels) is not int or max_pixels <= 0:
            raise ValueError
        page = document[index]
        width, height = page.get_width(), page.get_height()
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            raise ValueError
        pixel_width, pixel_height = math.ceil(width * scale), math.ceil(height * scale)
        record.update(
            pageSizeCanvasUnits=[width, height],
            intrinsicRotation=page.get_rotation(),
            pageBoxes={
                "effective": list(page.get_bbox()),
                "mediaDeclared": page.get_mediabox(fallback_ok=False),
                "cropDeclared": page.get_cropbox(fallback_ok=False),
                "boxCoordinateOrigin": "BOTTOMLEFT",
                "declaredBoxesMayBeInherited": True,
            },
            plannedPixelSize=[pixel_width, pixel_height],
        )
        if pixel_width * pixel_height > max_pixels:
            record["issues"].append({"code": "recognition_page_render_pixel_budget_exceeded"})
        else:
            bitmap = page.render(
                scale=scale,
                rotation=0,
                crop=(0, 0, 0, 0),
                fill_color=(255, 255, 255, 255),
                draw_annots=True,
                may_draw_forms=True,
            )
            image = bitmap.to_pil().convert("RGB")
            if list(image.size) != [pixel_width, pixel_height]:
                raise ValueError
            record.update(
                status="captured",
                pixelSize=list(image.size),
                pixelSha256=hashlib.sha256(image.tobytes()).hexdigest(),
                processedPixelBounds=[0, 0, pixel_width, pixel_height],
                pixelCoordinateOrigin="TOPLEFT",
                displayCanvasToPixelScale=[pixel_width / width, pixel_height / height],
            )
            record["renderCoordinates"] = _render_coordinate_evidence(page, bitmap)
            if record["renderCoordinates"]["status"] != "verified":
                record["status"] = "failed"
                record["issues"].append({"code": "recognition_render_coordinates_unverified"})
    except Exception:
        record["issues"].append({"code": "recognition_page_render_failed"})
    finally:
        for resource in (image, bitmap, page):
            if resource is not None:
                resource.close()
    record["fingerprint"] = page_render_fingerprint(record)
    return record


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
        document.init_forms()
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
                page_render = capture_full_page_render(document, index, source_hash)
                if time.monotonic() - started >= config.timeout_seconds:
                    issues.append({"code": "recognition_timeout"})
                    break
                exported, status, *observations = convert_page(page_content)
                if page_render["status"] != "captured":
                    status = "partial"
                    issues.extend({**issue, "page": index + 1} for issue in page_render["issues"])

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
                    "pageRender": page_render,
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
                from .recognition_sources import raw_pass_fingerprint

                observations["rawCaptureVersion"] = "document-files.raw-ocr.v1"
                observations["coverage"] = (
                    "raw_word_detections_and_separate_post_merge_observations"
                )
                observations["rawOCRPasses"] = [
                    {**deepcopy(capture), "fingerprint": raw_pass_fingerprint(capture)}
                    for snapshot in source_snapshots.values()
                    for capture in snapshot.get("rawOCRPasses", [])
                ]
                observations["rawCapturePages"] = [
                    {
                        "page_no": page_no,
                        "captureAvailable": snapshot.get("rawCaptureVersion")
                        == "document-files.raw-ocr.v1",
                        "passFingerprints": [
                            raw_pass_fingerprint(c) for c in snapshot.get("rawOCRPasses", [])
                        ],
                    }
                    for page_no, snapshot in source_snapshots.items()
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
