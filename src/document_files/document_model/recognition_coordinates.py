"""Bounded coordinate evidence; never an OCR accuracy or content-coverage proof."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from contextlib import contextmanager
from copy import deepcopy

VERSION = "document-files.recognition-coordinates.v1"


def fingerprint(record):
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in record.items() if k != "fingerprint"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def image_identity(image):
    return {
        "sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        "size": list(image.size),
        "mode": image.mode,
    }


def framework_frame(result, image, cropbox=None):
    """Called at the pinned renderer's actual full-image/crop seam, not at OCR output."""
    dim = result._page_decoder.get_page_dimension()
    width, height = result.page_width, result.page_height
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError("invalid framework page size")
    bounds = [0, 0, image.width, image.height]
    requested = None
    if cropbox is not None:
        box = cropbox.to_top_left_origin(page_height=height)
        requested = [box.l, box.t, box.r, box.b]
        bounds = [
            max(0, round(box.l * image.width / width)),
            max(0, round(box.t * image.height / height)),
            min(image.width, round(box.r * image.width / width)),
            min(image.height, round(box.b * image.height / height)),
        ]
    if (
        not 0 <= bounds[0] < bounds[2] <= image.width
        or not 0 <= bounds[1] < bounds[3] <= image.height
    ):
        raise ValueError("invalid crop bounds")
    return {
        "version": VERSION,
        "backend": "ThreadedDoclingParsePageBackend",
        "documentKey": result.doc_key,
        "localPageNumber": result.page_number,
        "boundaryType": str(result._boundary_type),
        "pageSize": [width, height],
        "normalizedMediaBox": list(dim.get_media_bbox()),
        "normalizedCropBox": list(dim.get_crop_bbox()),
        "normalizedAngle": dim.get_angle(),
        "canvas": image_identity(image),
        "requestedCropTopLeft": requested,
        "cropPixelBounds": bounds,
        "rounding": "python_round_then_clamp",
        "pixelCoordinateOrigin": "TOPLEFT",
        "ocrTruthVerified": False,
    }


@contextmanager
def capture_framework_crops(page, observer):
    """Wrap only this page result; restore even when OCR is cancelled or raises."""
    result = getattr(getattr(page, "_backend", None), "_result", None)
    if result is None or not hasattr(result, "_crop_image"):
        yield
        return
    previous = result.__dict__.get("_crop_image")
    original = result._crop_image

    def capture(image, cropbox):
        cropped = original(image, cropbox)
        observer._release_coordinate_image()
        try:
            observer.raw_pixel_frame = framework_frame(result, image, cropbox)
            observer.raw_coordinate_image = cropped.convert("RGB")
        except Exception:
            observer.raw_pixel_frame = None
        return cropped

    result._crop_image = capture
    try:
        yield
    finally:
        if previous is None:
            del result._crop_image
        else:
            result._crop_image = previous
        observer._release_coordinate_image()


def bind_ocr_frame(frame, unrotated_image, identity, angle):
    if frame is None or unrotated_image is None or angle not in (0, 90, 180, 270):
        return None
    expected = unrotated_image.rotate(-angle, expand=True) if angle else unrotated_image
    try:
        if image_identity(expected) != identity:
            return None
        result = deepcopy(frame)
        result.update(
            inputImage=deepcopy(identity),
            appliedClockwiseRotation=angle,
            cropImage=image_identity(unrotated_image),
            status="input_pixels_matched",
        )
        return result
    finally:
        if expected is not unrotated_image:
            expected.close()


def subset_mapping(original, subset):
    record = {
        "version": VERSION,
        "scope": "serialized_single_page_pdf_remapping",
        "sourceSha256": original["sourceSha256"],
        "originalPageNumber": original["page_no"],
        "originalRenderFingerprint": original["fingerprint"],
        "subsetSha256": subset["sourceSha256"],
        "subsetPageNumber": subset["page_no"],
        "subsetRender": deepcopy(subset),
        "status": "unverified",
        "ocrTruthVerified": False,
        "contentCoverageVerified": False,
    }
    keys = (
        "profile",
        "pixelSize",
        "pixelSha256",
        "intrinsicRotation",
        "pageBoxes",
        "pageSizeCanvasUnits",
        "renderCoordinates",
    )
    if (
        original.get("status") == subset.get("status") == "captured"
        and subset.get("page_no") == 1
        and all(original.get(k) == subset.get(k) for k in keys)
    ):
        record["status"] = "verified"
    record["fingerprint"] = fingerprint(record)
    return record


def _close(actual, expected):
    return len(actual) == len(expected) and all(
        type(a) in (float, int) and math.isfinite(a) and math.isclose(a, b, rel_tol=0, abs_tol=1e-5)
        for a, b in zip(actual, expected, strict=True)
    )


def _framework_dimensions_close(actual, expected, pixel_scale):
    # PDFium page boxes use binary32; the parser retains decimal page sizes.
    # Accept only the same binary32 representation, bounded to at most 0.001 pixel.
    # This is not a general relative tolerance or evidence of matching pixels.
    if _close(actual, expected):
        return True
    try:
        return len(actual) == len(expected) and all(
            type(a) in (int, float)
            and type(b) in (int, float)
            and math.isfinite(a)
            and math.isfinite(b)
            and (
                math.isclose(a, b, rel_tol=0, abs_tol=1e-5)
                or (
                    abs(a - b) * pixel_scale <= 0.001
                    and struct.pack("!f", a) == struct.pack("!f", b)
                )
            )
            for a, b in zip(actual, expected, strict=True)
        )
    except (OverflowError, struct.error):
        return False


def _compose(outer, inner):
    a, b, c, d, e, f = outer
    g, h, i, j, k, offset_y = inner
    return [
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * offset_y + e,
        b * k + d * offset_y + f,
    ]


def _frame_to_page(frame, mapping):
    """Verify the active parser's normalized boundary against the measured PDF page.

    Nonzero media origins and non-intersected out-of-media crop boxes remain
    unverified; a dimensional coincidence must not establish their translation.
    """
    page = mapping["subsetRender"]
    fw, fh = frame["pageSize"]
    if not all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (fw, fh)):
        raise ValueError("invalid framework page dimensions")
    if (
        frame["version"] != VERSION
        or frame["backend"] != "ThreadedDoclingParsePageBackend"
        or frame["documentKey"] != "key=" + mapping["subsetSha256"]
        or type(frame["localPageNumber"]) is not int
        or frame["localPageNumber"] != 1
        or frame["normalizedAngle"] != 0
        or frame["boundaryType"] != "crop_box"
        or frame["pixelCoordinateOrigin"] != "TOPLEFT"
    ):
        raise ValueError
    media = page["pageBoxes"]["mediaDeclared"]
    crop = page["pageBoxes"]["cropDeclared"] or media
    if (
        not media
        or not _close(media[:2], [0, 0])
        or not _close(crop, page["pageBoxes"]["effective"])
    ):
        raise ValueError
    w, h = media[2:]
    left, b, r, t = crop
    angle = page["intrinsicRotation"]
    normalized = {
        0: [left, b, r, t],
        90: [b, w - r, t, w - left],
        180: [w - r, h - t, w - left, h - b],
        270: [h - t, left, h - b, r],
    }[angle]
    expected_media = [0, 0, h, w] if angle in (90, 270) else [0, 0, w, h]
    width, height = frame["canvas"]["size"]
    if not all(type(v) is int and v > 0 for v in (width, height)):
        raise ValueError
    pixel_scale = max(width / fw, height / fh)
    if (
        not _framework_dimensions_close(frame["normalizedMediaBox"], expected_media, pixel_scale)
        or not _framework_dimensions_close(frame["normalizedCropBox"], normalized, pixel_scale)
        or not _framework_dimensions_close(
            frame["pageSize"], page["pageSizeCanvasUnits"], pixel_scale
        )
    ):
        raise ValueError
    requested = frame["requestedCropTopLeft"]
    expected_bounds = [0, 0, width, height]
    if requested is not None:
        left, top, right, bottom = requested
        expected_bounds = [
            max(0, round(left * width / fw)),
            max(0, round(top * height / fh)),
            min(width, round(right * width / fw)),
            min(height, round(bottom * height / fh)),
        ]
    if (
        frame["cropPixelBounds"] != expected_bounds
        or frame["rounding"] != "python_round_then_clamp"
    ):
        raise ValueError
    pw, ph = page["pixelSize"]
    return _compose(
        page["renderCoordinates"]["pixelToPageAffine"], [pw / width, 0, 0, ph / height, 0, 0]
    )


def coordinate_links(mapping, captures, repairs=(), runs=()):
    """Derive independent original-page matrices without rewriting upstream bboxes."""
    from .recognition_sources import raw_pass_fingerprint

    links = []
    for capture in captures:
        link = {
            "passFingerprint": capture.get("fingerprint"),
            "status": "unverified",
            "mappingFingerprint": mapping.get("fingerprint"),
            "ocrTruthVerified": False,
            "contentCoverageVerified": False,
        }
        try:
            if (
                mapping["status"] != "verified"
                or mapping["fingerprint"] != fingerprint(mapping)
                or capture["fingerprint"] != raw_pass_fingerprint(capture)
                or capture.get("batch_page_no", capture["page_no"]) != 1
                or capture["page_no"] != mapping["originalPageNumber"]
            ):
                raise ValueError
            if "runFingerprint" in capture:
                from .recognition_batches import validated_batch_capture

                validated_batch_capture(capture, runs, captures)
                # This is an OCR input ordinal, never the PDF subset page number.
                if type(capture.get("tsvInputPageNumber")) is not int or capture[
                    "tsvInputPageNumber"
                ] not in (1, 2):
                    raise ValueError
            frame = capture["pixelFrame"]
            if frame["status"] != "input_pixels_matched" or frame["inputImage"] != capture["image"]:
                raise ValueError
            outer = _frame_to_page(frame, mapping)
            x, y, right, bottom = frame["cropPixelBounds"]
            if frame.get("processing") == "ruled_cell_crop_and_white_padding":
                transform = capture["transform"]
                repair_index, unit_index = transform["repairIndex"], transform["cellUnitIndex"]
                if type(repair_index) is not int or not 0 <= repair_index < len(repairs):
                    raise ValueError
                repair = repairs[repair_index]
                if type(unit_index) is not int or not 0 <= unit_index < len(repair["units"]):
                    raise ValueError
                unit = repair["units"][unit_index]
                tx, ty, tr, tb = frame["tableCropPixelBounds"]
                cw, ch = frame["canvas"]["size"]
                pad = unit["padding"]
                cl, ct, cr, cb = unit["inkCrop"]
                offset = [unit["cellPixelBox"][0] + cl - pad, unit["cellPixelBox"][1] + ct - pad]
                x, y = frame["unitPixelOrigin"]
                if (
                    capture["sourcePass"] != "table_repair"
                    or repair["policy"] != "ruled_cells_v2"
                    or repair["page_no"] != mapping["originalPageNumber"]
                    or repair["sourcePixelsSha256"] != frame["tableCropImage"]["sha256"]
                    or frame["tableCropImage"]["size"] != [tr - tx, tb - ty]
                    or not 0 <= tx < tr <= cw
                    or not 0 <= ty < tb <= ch
                    or repair["transform"]["cropPixelOrigin"] != [tx, ty]
                    or repair["transform"]["canvasPixels"] != [cw, ch]
                    or frame["unitFingerprint"] != unit["fingerprint"]
                    or unit["pixelOffset"] != offset
                    or frame["unitPixelOffset"] != offset
                    or [x, y] != [tx + offset[0], ty + offset[1]]
                    or transform["pixelOrigin"] != [x, y]
                    or frame["cellPixelBox"] != unit["cellPixelBox"]
                    or frame["inputImage"]["size"] != [cr - cl + 2 * pad, cb - ct + 2 * pad]
                    or frame["appliedClockwiseRotation"] != 0
                ):
                    raise ValueError
                inner = [1, 0, 0, 1, x, y]
                link["sourceSupportedInputPixelBounds"] = [pad, pad, cr - cl + pad, cb - ct + pad]
                link["syntheticPaddingIsSourceContent"] = False
            else:
                if frame["cropImage"]["size"] != [right - x, bottom - y]:
                    raise ValueError
                angle = frame["appliedClockwiseRotation"]
                if (
                    capture["sourcePass"] != "page_ocr"
                    or capture["transform"]["orientation"] != angle
                ):
                    raise ValueError
                width, height = frame["cropImage"]["size"]
                expected_size = [height, width] if angle in (90, 270) else [width, height]
                if frame["inputImage"]["size"] != expected_size:
                    raise ValueError
                link["sourceSupportedInputPixelBounds"] = [0, 0, *expected_size]
                inner = {
                    0: [1, 0, 0, 1, x, y],
                    90: [0, -1, 1, 0, x, y + height],
                    180: [-1, 0, 0, -1, x + width, y + height],
                    270: [0, 1, -1, 0, x + width, y],
                }[angle]
            matrix = _compose(outer, inner)
            if not all(math.isfinite(v) for v in matrix):
                raise ValueError
            link.update(
                status="verified",
                inputPixelToOriginalPageAffine=matrix,
                pageCoordinateOrigin="BOTTOMLEFT",
                reportedRecognitionBBoxesVerified=False,
                pixelFrameFingerprint=fingerprint(frame),
                scope="captured_input_pixels_to_original_pdf_coordinates_only",
            )
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            pass
        links.append(link)
    return links
