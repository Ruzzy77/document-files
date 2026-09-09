"""Version-pinned internal Docling seam; framework types never leave the worker."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import subprocess
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from .recognition_coordinates import (
    bind_ocr_frame,
    capture_framework_crops,
    framework_frame,
    image_identity,
)
from .table_ocr_repair import (
    bounded_tsv,
    box_overlap,
    cell_ocr_units,
    geometric_structure_order,
    parse_tsv,
    remove_grid,
    ruling_line_evidence,
)


def pipeline_class(config, snapshots, restored=None):
    restored = restored if restored is not None else {}
    import pandas as pd
    from docling.models.stages.ocr.tesseract_ocr_cli_model import (
        TesseractOcrCliModel,
        _parse_orientation,
    )
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    from docling_core.types.doc import DocItemLabel
    from docling_core.types.doc.page import BoundingRectangle, TextCell

    def record(cell, *, stage, **extra):
        return {
            "text": cell.text,
            "raw": cell.orig,
            "fromOcr": cell.from_ocr,
            "sourceKind": "ocr" if cell.from_ocr else "pdf_text",
            "backendCellIndex": cell.index,
            "confidence": cell.confidence,
            "bbox": cell.to_bounding_box().model_dump(mode="json"),
            "rectangle": cell.rect.model_dump(mode="json"),
            "stage": stage,
            **extra,
        }

    def coords(cell, page):
        b = cell.to_bounding_box().to_top_left_origin(page_height=page.size.height)
        return (b.l, b.t, b.r, b.b)

    class ExactTesseract(TesseractOcrCliModel):
        repair_calls = 0
        repair_pixels = 0
        repair_tables = 0
        repair_elapsed = 0.0
        orientation = None

        def _release_coordinate_image(self):
            image = getattr(self, "raw_coordinate_image", None)
            if image is not None:
                image.close()
            self.raw_coordinate_image = None
            self.raw_pixel_frame = None

        def _perform_osd(self, filename):
            self.orientation = None
            result = super()._perform_osd(filename)
            self.orientation = _parse_orientation(result)
            return result

        def get_ocr_rects(self, page):
            rectangles = super().get_ocr_rects(page)
            self.raw_rectangles = [r.model_dump(mode="json") for r in rectangles if r.area() > 0]
            return rectangles

        def post_process_cells(self, all_ocr_cells, page, conv_res):
            self.raw_transform_seen = True
            # The pinned upstream seam exposes transformed cells BEFORE overlap filtering.
            detections = [
                d
                for p in self.raw_passes
                if p["sourcePass"] == "page_ocr"
                for d in p.get("detections", [])
                if d.get("accepted")
            ]
            if len(detections) == len(all_ocr_cells) and all(
                d["text"] == c.orig == c.text and abs(d["confidence"] / 100 - c.confidence) < 1e-9
                for d, c in zip(detections, all_ocr_cells, strict=True)
            ):
                for detection, cell in zip(detections, all_ocr_cells, strict=True):
                    detection["pageBBox"] = cell.to_bounding_box().model_dump(mode="json")
                    detection["mappingBasis"] = "pinned_upstream_pre_merge_order_text_confidence"
            else:
                self.raw_capture_issues.append({"code": "recognition_raw_transform_unresolved"})
            return super().post_process_cells(all_ocr_cells, page, conv_res)

        def _run_tesseract(self, ifilename, osd):
            # Upstream uses `osd` here only for automatic language selection.
            # Fixed kor/eng never uses it; inherited __call__ still rotates images
            # and maps TSV rectangles back with tesseract_box_to_bounding_rectangle.
            # CSV preserves precise numeric lexemes instead of pandas inference.
            cmd = [
                self._safe_tesseract_cmd,
                "-l",
                "+".join(self.options.lang),
                "--tessdata-dir",
                self._safe_tessdata_path,
                "--psm",
                str(self.options.psm if self.options.psm is not None else 3),
                self._sanitize_filename(ifilename),
                "stdout",
                "tsv",
            ]
            if hasattr(self, "call_psm"):
                cmd[cmd.index("--psm") + 1] = str(self.call_psm)
            timeout = getattr(self, "call_timeout", config.timeout_seconds)
            if not hasattr(self, "raw_passes"):
                self.raw_passes, self.raw_capture_issues = [], []
            from PIL import Image

            with Image.open(ifilename) as source_image:
                pixels = source_image.tobytes()
                image_identity = {
                    "sha256": hashlib.sha256(pixels).hexdigest(),
                    "size": list(source_image.size),
                    "mode": source_image.mode,
                }
            repair_transform = getattr(self, "raw_repair_transform", None)
            original_index = sum(p["sourcePass"] == "page_ocr" for p in self.raw_passes)
            capture = {
                "passId": f"pass-{len(self.raw_passes)}",
                "page_no": getattr(self, "raw_page_no", 1),
                "sourcePass": "table_repair" if repair_transform else "page_ocr",
                "image": image_identity,
                "transform": deepcopy(repair_transform)
                if repair_transform
                else {
                    "scale": self.scale,
                    # Pinned upstream uses doc_orientation=0 when OSD fails. This
                    # is an applied transform, not a verified source orientation.
                    "orientation": _parse_orientation(osd) if osd is not None else 0,
                    "orientationObservation": self.orientation,
                    "orientationBasis": "upstream_osd_result"
                    if osd is not None
                    else "upstream_no_rotation_fallback",
                    "crop": self.raw_rectangles[original_index]
                    if original_index < len(getattr(self, "raw_rectangles", []))
                    else None,
                },
                "status": "failed",
                "detections": [],
            }
            if repair_transform:
                frame = getattr(self, "raw_repair_frame", None)
                if frame is not None and frame.get("inputImage") == image_identity:
                    capture["pixelFrame"] = deepcopy(frame)
            else:
                frame = bind_ocr_frame(
                    getattr(self, "raw_pixel_frame", None),
                    getattr(self, "raw_coordinate_image", None),
                    image_identity,
                    capture["transform"]["orientation"],
                )
                if frame is not None:
                    capture["pixelFrame"] = frame
            self.raw_passes.append(capture)
            try:
                raw = bounded_tsv(
                    cmd, timeout=timeout, max_bytes=min(config.max_output_bytes, 16 * 1024 * 1024)
                )
                # Keep the lexical TSV too: invalid geometry must not silently disappear.
                capture["tsv"] = raw.decode("utf-8")
                capture["tsvSha256"] = hashlib.sha256(raw).hexdigest()
                parsed = parse_tsv(raw)
                accepted = iter(parsed)
                next_accepted = next(accepted, None)
                for ordinal, row in enumerate(
                    csv.DictReader(io.StringIO(capture["tsv"]), delimiter="\t")
                ):
                    if not row.get("text", "").strip():
                        continue
                    detection = {
                        "ordinal": ordinal,
                        "text": row["text"],
                        "raw": row,
                        "accepted": False,
                    }
                    capture["detections"].append(detection)
                    if next_accepted is not None and row["text"] == next_accepted["text"]:
                        try:
                            matches = (
                                all(
                                    int(row[k]) == next_accepted[k]
                                    for k in ("left", "top", "width", "height")
                                )
                                and float(row["conf"]) == next_accepted["conf"]
                            )
                        except (ValueError, TypeError, KeyError):
                            matches = False
                        if matches:
                            detection.update(
                                accepted=True,
                                confidence=next_accepted["conf"],
                                imageBBox={
                                    k: next_accepted[k] for k in ("left", "top", "width", "height")
                                },
                            )
                            if repair_transform:
                                x, y = repair_transform["pixelOrigin"]
                                sx, sy = repair_transform["scale"]
                                left, top = (
                                    (x + next_accepted["left"]) / sx,
                                    (y + next_accepted["top"]) / sy,
                                )
                                detection["pageBBox"] = {
                                    "l": left,
                                    "t": top,
                                    "r": left + next_accepted["width"] / sx,
                                    "b": top + next_accepted["height"] / sy,
                                    "coord_origin": "TOPLEFT",
                                }
                                detection["mappingBasis"] = "explicit_table_unit_pixel_transform"
                            next_accepted = next(accepted, None)
                capture["status"] = "complete"
            except Exception:
                self.raw_capture_issues.append({"code": "recognition_raw_capture_failed"})
                raise
            return pd.DataFrame(
                parsed,
                columns=[
                    "left",
                    "top",
                    "width",
                    "height",
                    "conf",
                    "text",
                ],
            )

        def __call__(self, conv_res, page_batch):
            for source_page in page_batch:
                with capture_framework_crops(source_page, self):
                    yield from self._captured_page(conv_res, source_page)

        def _captured_page(self, conv_res, source_page):
            self.orientation = None
            self.raw_passes, self.raw_capture_issues = [], []
            self.raw_page_no = source_page.page_no
            self.raw_transform_seen = False
            self.raw_rectangles = []
            for page in super().__call__(conv_res, [source_page]):
                original = list(page.cells)
                if not self.raw_transform_seen or len(self.raw_rectangles) != len(self.raw_passes):
                    self.raw_capture_issues.append(
                        {"code": "recognition_raw_page_capture_incomplete"}
                    )
                snapshot = {
                    "original": [record(c, stage="original_ocr") for c in original],
                    "supplemental": [],
                    "repairs": [],
                    "issues": self.raw_capture_issues,
                    "rawOCRPasses": self.raw_passes,
                    "rawCaptureVersion": "document-files.raw-ocr.v1",
                }
                snapshot["originalOCRFingerprint"] = hashlib.sha256(
                    json.dumps(
                        [
                            {k: c[k] for k in ("text", "raw", "fromOcr", "bbox")}
                            for c in snapshot["original"]
                        ],
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
                snapshots[page.page_no] = snapshot
                if config.table_ocr_repair not in {"ruled_tables_v1", "ruled_cells_v2"}:
                    yield page
                    continue
                try:
                    self.repair(page, original, snapshot)
                except subprocess.TimeoutExpired:
                    snapshot["issues"].append({"code": "table_ocr_repair_timeout"})
                except Exception:
                    snapshot["issues"].append({"code": "table_ocr_repair_failed"})
                self.apply_structure_view(page, snapshot)
                yield page

        def apply_structure_view(self, page, snapshot):
            if config.table_ocr_repair != "ruled_cells_v2" or page.parsed_page is None:
                return
            # Source arrays remain immutable text/geometry observations. Only the
            # downstream structure view selects and orders records.
            suppressed = {
                c["backendCellIndex"]
                for c in snapshot["original"]
                if c.get("structureView", {}).get("selection") == "ruling_line_excluded"
            }
            cells = [c for c in page.parsed_page.textline_cells if c.index not in suppressed]
            records = [record(c, stage="structure_view") for c in cells]
            order = geometric_structure_order(records, snapshot["repairs"])
            # Docling layout postprocessing sorts cells by index again. Give only
            # the derived view new order indices; never reindex source records.
            if len({c.index for c in cells}) != len(cells):
                snapshot["issues"].append({"code": "structure_view_source_index_collision"})
                return
            view, source_to_view = [], {}
            for view_index, source_index in enumerate(order):
                source = cells[source_index]
                derived = source.model_copy(deep=True)
                derived.index = view_index
                source_to_view[source.index] = view_index
                view.append(derived)
            page.parsed_page.textline_cells = view
            for cell in snapshot["original"] + snapshot["supplemental"]:
                selection = cell.setdefault(
                    "structureView",
                    {
                        "selection": "source_candidate_preserved",
                        "ordering": "closed_grid_cell_then_geometric_text_line_v1",
                    },
                )
                if cell["backendCellIndex"] in source_to_view:
                    selection["structureInputIndex"] = source_to_view[cell["backendCellIndex"]]
                    selection["sourceIndexPreserved"] = True

        def repair(self, page, original, snapshot):
            if page.predictions.layout is None or page.parsed_page is None:
                return
            cells = list(original)
            for cluster in page.predictions.layout.clusters:
                if cluster.label != DocItemLabel.TABLE:
                    continue
                bbox = cluster.bbox.to_top_left_origin(page_height=page.size.height)
                target = (bbox.l, bbox.t, bbox.r, bbox.b)
                if any(
                    not c.from_ocr and c.text.strip() and box_overlap(coords(c, page), target) > 0.2
                    for c in original
                ):
                    snapshot["repairs"].append(
                        {"status": "native_text_table_skipped", "clusterId": cluster.id}
                    )
                    continue
                if self.orientation != 0:
                    snapshot["issues"].append(
                        {"code": "table_ocr_repair_orientation_unresolved", "clusterId": cluster.id}
                    )
                    continue
                if restored.get("originalOCRFingerprint") == snapshot.get("originalOCRFingerprint"):
                    reused = False
                    for repair_index, prior in enumerate(restored.get("tableRepairs", [])):
                        if (
                            prior.get("assessmentComplete")
                            and prior.get("policy", "ruled_tables_v1") == config.table_ocr_repair
                            and prior.get("sourceBBox") == bbox.model_dump(mode="json")
                        ):
                            snapshot["issues"].append(
                                {"code": "recognition_raw_reused_repair_unverified"}
                            )
                            new_index = len(snapshot["repairs"])
                            snapshot["repairs"].append(
                                {**deepcopy(prior), "reusedFromCheckpoint": True}
                            )
                            for saved in restored.get("cells", []):
                                if (
                                    saved.get("stage")
                                    not in {"table_line_removed_ocr", "table_cell_ocr"}
                                    or saved.get("repairIndex") != repair_index
                                ):
                                    continue
                                item = {**deepcopy(saved), "repairIndex": new_index}
                                snapshot["supplemental"].append(item)
                                if item.get("candidateStatus") == "new_observation":
                                    from docling_core.types.doc import BoundingBox

                                    cells.append(
                                        TextCell(
                                            index=len(cells),
                                            text=item["text"],
                                            orig=item["raw"],
                                            from_ocr=True,
                                            confidence=item["confidence"],
                                            rect=BoundingRectangle.from_bounding_box(
                                                BoundingBox.model_validate(item["bbox"])
                                            ),
                                        )
                                    )
                                elif item.get("candidateStatus") == "unresolved_conflict":
                                    snapshot["issues"].append(
                                        {
                                            "code": "table_ocr_repair_text_conflict",
                                            "clusterId": cluster.id,
                                        }
                                    )
                            for saved in restored.get("cells", []):
                                if saved.get("stage") == "original_ocr" and saved.get(
                                    "structureView"
                                ):
                                    for observed in snapshot["original"]:
                                        if all(
                                            observed.get(k) == saved.get(k)
                                            for k in ("backendCellIndex", "raw", "bbox")
                                        ):
                                            observed["structureView"] = deepcopy(
                                                saved["structureView"]
                                            )
                            page.parsed_page.textline_cells = cells
                            reused = True
                            break
                    if reused:
                        continue
                if (
                    self.repair_tables >= config.repair_max_tables
                    or self.repair_calls >= config.repair_max_calls
                    or self.repair_elapsed >= config.repair_max_seconds
                ):
                    snapshot["issues"].append({"code": "table_ocr_repair_budget_exceeded"})
                    break
                self.repair_tables += 1
                start = time.monotonic()
                try:
                    scale = (
                        max(self.scale, 300 / 72)
                        if config.table_ocr_repair == "ruled_cells_v2"
                        else self.scale
                    )
                    scale_x = scale_y = scale
                    canvas = None
                    if config.table_ocr_repair == "ruled_cells_v2":
                        # The pinned renderer rounds its canvas dimensions and crops
                        # using actual per-axis scales, not the requested DPI alone.
                        # Bound the full render too: its backend already renders a
                        # page internally even when called with a cropbox.
                        canvas_pixels = math.ceil(page.size.width * scale) * math.ceil(
                            page.size.height * scale
                        )
                        if canvas_pixels > config.repair_max_pixels:
                            snapshot["issues"].append({"code": "table_ocr_repair_region_too_large"})
                            continue
                        canvas = page.get_image(scale=scale)
                        if (
                            canvas is None
                            or canvas.width * canvas.height > config.repair_max_pixels
                        ):
                            snapshot["issues"].append(
                                {"code": "table_ocr_repair_geometry_unresolved"}
                            )
                            continue
                        scale_x = canvas.width / page.size.width
                        scale_y = canvas.height / page.size.height
                    x0, y0 = (
                        max(0, math.floor(bbox.l * scale_x + 1e-9)),
                        max(0, math.floor(bbox.t * scale_y + 1e-9)),
                    )
                    x1, y1 = (
                        min(round(page.size.width * scale_x), math.ceil(bbox.r * scale_x - 1e-9)),
                        min(round(page.size.height * scale_y), math.ceil(bbox.b * scale_y - 1e-9)),
                    )
                    pixels = max(0, x1 - x0) * max(0, y1 - y0)
                    remaining_pixels = config.repair_max_pixels - self.repair_pixels
                    if pixels <= 0 or pixels > config.repair_max_pixels:
                        snapshot["issues"].append({"code": "table_ocr_repair_region_too_large"})
                        continue
                    if pixels > remaining_pixels:
                        snapshot["issues"].append({"code": "table_ocr_repair_budget_exceeded"})
                        break
                    self.repair_pixels += pixels
                    from docling_core.types.doc import BoundingBox, CoordOrigin

                    # Check the pixel budget before requesting a render. The aligned
                    # crop carries exact pixel-origin mapping, not another whole page.
                    crop = (
                        canvas.crop((x0, y0, x1, y1))
                        if canvas is not None
                        else page.get_image(
                            scale=scale,
                            cropbox=BoundingBox(
                                l=x0 / scale,
                                t=y0 / scale,
                                r=x1 / scale,
                                b=y1 / scale,
                                coord_origin=CoordOrigin.TOPLEFT,
                            ),
                        )
                    )
                    if crop is None or crop.width != x1 - x0 or crop.height != y1 - y0:
                        snapshot["issues"].append({"code": "table_ocr_repair_geometry_unresolved"})
                        continue
                    derived, info = remove_grid(crop, max_pixels=remaining_pixels)
                    if config.table_ocr_repair == "ruled_cells_v2":
                        for source in snapshot["original"]:
                            if not source.get("fromOcr"):
                                continue
                            source_box = source["bbox"]
                            if source_box.get("coord_origin") != "TOPLEFT":
                                continue
                            evidence = ruling_line_evidence(
                                crop,
                                info,
                                (
                                    source_box["l"] * scale_x - x0,
                                    source_box["t"] * scale_y - y0,
                                    source_box["r"] * scale_x - x0,
                                    source_box["b"] * scale_y - y0,
                                ),
                            )
                            if evidence:
                                source["structureView"] = {
                                    "selection": "ruling_line_excluded",
                                    "repairIndex": len(snapshot["repairs"]),
                                    "rulingLineEvidence": evidence,
                                }

                    info.update(
                        clusterId=cluster.id,
                        policy=config.table_ocr_repair,
                        page_no=page.page_no,
                        sourceBBox=bbox.model_dump(mode="json"),
                        transform={
                            "cropPixelOrigin": [x0, y0],
                            "scale": scale,
                            "scaleX": scale_x,
                            "scaleY": scale_y,
                            "canvasPixels": list(canvas.size) if canvas is not None else None,
                            "rotation": 0,
                            "origin": "TOPLEFT",
                        },
                    )
                    snapshot["repairs"].append(info)
                    if derived is None:
                        info["assessmentComplete"] = True
                        continue
                    info["sourcePixelsSha256"] = hashlib.sha256(crop.tobytes()).hexdigest()
                    info["derivedPixelsSha256"] = hashlib.sha256(derived.tobytes()).hexdigest()
                    if config.table_ocr_repair == "ruled_cells_v2":
                        units, unit_geometry = cell_ocr_units(crop, info)
                        info["cellGeometry"] = unit_geometry
                        if unit_geometry["status"] != "verified_rectangular_grid":
                            snapshot["issues"].append(
                                {"code": "table_ocr_cell_geometry_unresolved"}
                            )
                    else:
                        units = [
                            (
                                derived,
                                {
                                    "status": "ready",
                                    "psm": 3,
                                    "pixelOffset": [0, 0],
                                    "fingerprint": info["derivedPixelsSha256"],
                                },
                            )
                        ]
                    info["units"] = []
                    prior_pair = next(
                        (
                            (i, p)
                            for i, p in enumerate(restored.get("tableRepairs", []))
                            if restored.get("originalOCRFingerprint")
                            == snapshot.get("originalOCRFingerprint")
                            and p.get("policy", "ruled_tables_v1") == config.table_ocr_repair
                            and p.get("sourceBBox") == info["sourceBBox"]
                            and p.get("sourcePixelsSha256") == info["sourcePixelsSha256"]
                        ),
                        None,
                    )
                    incomplete = False
                    for unit_index, (unit_image, unit) in enumerate(units):
                        info["units"].append(unit)
                        prior_index, prior = prior_pair if prior_pair else (None, {})
                        saved_unit = next(
                            (
                                u
                                for u in prior.get("units", [])
                                if u.get("fingerprint") == unit["fingerprint"] and u.get("complete")
                            ),
                            None,
                        )
                        if saved_unit:
                            snapshot["issues"].append(
                                {"code": "recognition_raw_reused_repair_unverified"}
                            )
                            unit.update(complete=True, reusedFromCheckpoint=True)
                            for saved in restored.get("cells", []):
                                if (
                                    saved.get("repairIndex") != prior_index
                                    or saved.get("cellUnitIndex") != unit_index
                                    or saved.get("stage")
                                    not in {"table_cell_ocr", "table_line_removed_ocr"}
                                ):
                                    continue
                                item = {
                                    **deepcopy(saved),
                                    "repairIndex": len(snapshot["repairs"]) - 1,
                                }
                                snapshot["supplemental"].append(item)
                                if item.get("candidateStatus") == "new_observation":
                                    cells.append(
                                        TextCell(
                                            index=len(cells),
                                            text=item["text"],
                                            orig=item["raw"],
                                            from_ocr=True,
                                            confidence=item["confidence"],
                                            rect=BoundingRectangle.from_bounding_box(
                                                BoundingBox.model_validate(item["bbox"])
                                            ),
                                        )
                                    )
                                elif item.get("candidateStatus") == "unresolved_conflict":
                                    snapshot["issues"].append(
                                        {
                                            "code": "table_ocr_repair_text_conflict",
                                            "clusterId": cluster.id,
                                        }
                                    )
                            continue
                        if unit_image is None:
                            unit["complete"] = True
                            if unit["status"] != "no_ink_observed":
                                snapshot["issues"].append(
                                    {"code": "table_ocr_cell_boundary_unresolved"}
                                )
                            continue
                        remaining = (
                            config.repair_max_seconds
                            - self.repair_elapsed
                            - (time.monotonic() - start)
                        )
                        if remaining <= 0 or self.repair_calls >= config.repair_max_calls:
                            snapshot["issues"].append({"code": "table_ocr_repair_budget_exceeded"})
                            incomplete = True
                            break
                        self.repair_calls += 1
                        self.call_timeout, self.call_psm = remaining, unit["psm"]
                        self.raw_repair_frame = None
                        if canvas is not None:
                            try:
                                self.raw_repair_frame = framework_frame(
                                    page._backend._result, canvas
                                )
                                self.raw_repair_frame.update(
                                    status="input_pixels_matched",
                                    inputImage=image_identity(unit_image),
                                    processing="ruled_cell_crop_and_white_padding",
                                    tableCropPixelBounds=[x0, y0, x1, y1],
                                    tableCropImage=image_identity(crop),
                                    cellPixelBox=unit.get("cellPixelBox"),
                                    unitFingerprint=unit["fingerprint"],
                                    unitPixelOffset=list(unit["pixelOffset"]),
                                    unitPixelOrigin=[
                                        x0 + unit["pixelOffset"][0],
                                        y0 + unit["pixelOffset"][1],
                                    ],
                                    appliedClockwiseRotation=0,
                                )
                            except Exception:
                                self.raw_repair_frame = None
                        self.raw_repair_transform = {
                            "pixelOrigin": [
                                x0 + unit["pixelOffset"][0],
                                y0 + unit["pixelOffset"][1],
                            ],
                            "scale": [scale_x, scale_y],
                            "repairIndex": len(snapshot["repairs"]) - 1,
                            "cellUnitIndex": unit_index,
                        }
                        try:
                            with tempfile.TemporaryDirectory(
                                prefix="document-files-table-ocr-"
                            ) as directory:
                                path = Path(directory) / "derived.png"
                                unit_image.save(path)
                                result = self._run_tesseract(str(path), None)
                        finally:
                            del (
                                self.call_timeout,
                                self.call_psm,
                                self.raw_repair_transform,
                                self.raw_repair_frame,
                            )
                        offset = unit["pixelOffset"]
                        for ordinal, row in result.iterrows():
                            left, top = (
                                (x0 + offset[0] + row["left"]) / scale_x,
                                (y0 + offset[1] + row["top"]) / scale_y,
                            )
                            right, bottom = (
                                left + row["width"] / scale_x,
                                top + row["height"] / scale_y,
                            )
                            if (
                                not bbox.l - 1 <= left < right <= bbox.r + 1
                                or not bbox.t - 1 <= top < bottom <= bbox.b + 1
                            ):
                                snapshot["issues"].append(
                                    {"code": "table_ocr_repair_token_geometry_unresolved"}
                                )
                                continue
                            if "cellPixelBox" in unit:
                                uleft, utop, uright, ubottom = unit["cellPixelBox"]
                                if not (
                                    (x0 + uleft) / scale_x
                                    <= left
                                    < right
                                    <= (x0 + uright) / scale_x
                                    and (y0 + utop) / scale_y
                                    <= top
                                    < bottom
                                    <= (y0 + ubottom) / scale_y
                                ):
                                    snapshot["issues"].append(
                                        {"code": "table_ocr_cell_token_geometry_unresolved"}
                                    )
                                    continue
                            from docling_core.types.doc import BoundingBox, CoordOrigin

                            cell = TextCell(
                                index=len(cells),
                                text=row["text"],
                                orig=row["text"],
                                from_ocr=True,
                                confidence=row["conf"] / 100,
                                rect=BoundingRectangle.from_bounding_box(
                                    BoundingBox(
                                        l=left,
                                        t=top,
                                        r=right,
                                        b=bottom,
                                        coord_origin=CoordOrigin.TOPLEFT,
                                    )
                                ),
                            )
                            overlaps = [
                                c
                                for c in original
                                if box_overlap(coords(c, page), (left, top, right, bottom)) >= 0.2
                            ]
                            exact = any(
                                c.orig == cell.orig and c.text == cell.text for c in overlaps
                            )
                            status = (
                                "equivalent"
                                if exact
                                else "unresolved_conflict"
                                if overlaps
                                else "new_observation"
                            )
                            observation = record(
                                cell,
                                stage="table_cell_ocr"
                                if config.table_ocr_repair == "ruled_cells_v2"
                                else "table_line_removed_ocr",
                                cellUnitIndex=unit_index,
                                candidateStatus=status,
                                repairIndex=len(snapshot["repairs"]) - 1,
                                originalCellIndices=[c.index for c in overlaps],
                                ordinal=int(ordinal),
                            )
                            snapshot["supplemental"].append(observation)
                            # Do not replace or duplicate existing text. Conflicts survive as
                            # observations; only previously uncovered tokens reach structure.
                            if not overlaps:
                                cells.append(cell)
                            elif not exact:
                                snapshot["issues"].append(
                                    {
                                        "code": "table_ocr_repair_text_conflict",
                                        "clusterId": cluster.id,
                                    }
                                )
                        unit["complete"] = True
                        unit["tokenCount"] = len(result)
                        if len(result) == 0:
                            unit["status"] = "ink_present_no_tokens"
                            snapshot["issues"].append({"code": "table_ocr_ink_without_tokens"})
                    if incomplete:
                        page.parsed_page.textline_cells = cells
                        break
                    info["ocrComplete"] = True
                    info["assessmentComplete"] = True
                    page.parsed_page.textline_cells = cells
                finally:
                    self.repair_elapsed += time.monotonic() - start

    class SourcePreservingPdfPipeline(StandardPdfPipeline):
        def _make_ocr_model(self, artifacts_path):
            return ExactTesseract(
                enabled=self.pipeline_options.do_ocr,
                artifacts_path=artifacts_path,
                options=self.pipeline_options.ocr_options,
                accelerator_options=self.pipeline_options.accelerator_options,
            )

    SourcePreservingPdfPipeline._product_ocr_type = ExactTesseract
    return SourcePreservingPdfPipeline
