"""Transient source-bound PDF review images; no OCR or content-completion authority."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from importlib.metadata import version

VERSION = "document-files.pdf-review-images.v2"
MAX_SOURCE_BYTES = 128 * 1024**2
MAX_IMAGE_BYTES = 16 * 1024**2
MAX_IMAGE_PIXELS = 16000000


class PdfReviewImageError(ValueError):
    """Fixed error code only, with no source bytes or upstream exception text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require(condition, code="pdf_review_capture_invalid"):
    if not condition:
        raise PdfReviewImageError(code)


def _json(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return _sha(_json(value).encode())


@dataclass(frozen=True)
class PdfReviewImages:
    """Bytes are transient; descriptor access returns an independent JSON value."""

    png_images: tuple[bytes, ...] = field(repr=False)
    _descriptor: dict = field(repr=False)

    @property
    def descriptor(self) -> dict:
        return deepcopy(self._descriptor)

    def content_parts(self, indices: list[int] | None = None) -> list[dict]:
        selected = self.png_images if indices is None else [self.png_images[i] for i in indices]
        return [
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")
                },
            }
            for data in selected
        ]


class _BoundedPng(io.BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, data):
        _require(self.tell() + len(data) <= self.limit, "pdf_review_image_byte_budget_exceeded")
        return super().write(data)


def prepare_pdf_line_strips(
    content: bytes,
    capture: dict,
    strips: list[dict],
    *,
    deadline: float,
    cancelled: Callable[[], bool] | None = None,
) -> PdfReviewImages:
    """Lossless page strips for text-line reading, without the scaled page image.

    Each strip is ``{"id", "pageBounds"}`` in capture pixels; the descriptor records
    the strip id as ``requestedSlotKey`` and ``line_strip`` as its purpose. Strips
    locate pixels only; they establish no reading, order or completeness.
    """
    _require(isinstance(strips, list) and strips, "pdf_review_crop_invalid")
    return prepare_pdf_review_images(
        content,
        capture,
        crops=[
            {
                "pixelBounds": strip.get("pageBounds") if isinstance(strip, dict) else None,
                "kind": "line_strip",
                "slotKey": strip.get("id") if isinstance(strip, dict) else None,
            }
            for strip in strips
        ],
        include_page=False,
        deadline=deadline,
        cancelled=cancelled,
    )


def prepare_pdf_review_images(
    content: bytes,
    capture: dict,
    *,
    crop: dict | None = None,
    crops: list[dict] | None = None,
    include_page: bool = True,
    deadline: float,
    cancelled: Callable[[], bool] | None = None,
) -> PdfReviewImages:
    """Re-render one original page once, matching the previous capture exactly.

    ``capture`` is the unmodified full-page capture, not its provenance wrapper.
    Crop bounds use integer pixel edges [x0,x1) x [y0,y1), including the outer
    image edge. ``kind='full_slot'`` records requested purpose only. It cannot
    establish a structural slot association, blank value, reading order or OCR
    correctness. ``crops`` lists several crops (``line_strip`` strips carry their
    strip id as ``slotKey``); ``include_page=False`` omits the scaled page image.
    No global observations/issues are mutated.

    Native render/decode calls have before/after deadline checks; hard preemption
    remains the owned job supervisor's responsibility, as in the existing worker.
    """
    started = time.monotonic()
    _require(
        type(deadline) in (int, float)
        and math.isfinite(deadline)
        and (cancelled is None or callable(cancelled)),
        "pdf_review_configuration_invalid",
    )

    def check_time():
        if cancelled is not None and cancelled():
            raise PdfReviewImageError("pdf_review_cancelled")
        if time.monotonic() >= deadline:
            raise PdfReviewImageError("pdf_review_timeout")

    check_time()
    _require(type(content) is bytes and content.startswith(b"%PDF-"), "pdf_review_source_invalid")
    _require(len(content) <= MAX_SOURCE_BYTES, "pdf_review_source_byte_budget_exceeded")
    _require(isinstance(capture, dict), "pdf_review_capture_invalid")
    _require(crop is None or crops is None, "pdf_review_crop_invalid")
    _require(type(include_page) is bool, "pdf_review_configuration_invalid")
    document = page = bitmap = native_image = image = None
    details = []
    try:
        import pypdfium2 as pdfium

        from ..document_model.recognition_sources import page_render_fingerprint
        from ..document_model.recognition_visual import visual_profile
        from ..document_model.recognition_worker import _render_coordinate_evidence

        frozen = deepcopy(capture)
        requested = (
            deepcopy(crops) if crops is not None else [deepcopy(crop)] if crop is not None else []
        )
        _require(isinstance(requested, list) and len(requested) <= 64, "pdf_review_crop_invalid")
        _require(include_page or requested, "pdf_review_crop_invalid")
        _require(len(_json(frozen).encode()) <= 16 * 1024**2)
        _require(frozen.get("fingerprint") == page_render_fingerprint(frozen))
        source_hash = _sha(content)
        check_time()
        _require(frozen.get("sourceSha256") == source_hash, "pdf_review_source_changed")
        expected_profile = {
            "scale": 3.0,
            "maxPixels": MAX_IMAGE_PIXELS,
            "additionalRotation": 0,
            "crop": [0, 0, 0, 0],
            "background": [255, 255, 255, 255],
            "drawAnnotations": True,
            "drawForms": True,
            "pixelMode": "RGB",
            "visualObservation": visual_profile(),
        }
        _require(
            frozen.get("version") == "document-files.full-page-render.v1"
            and frozen.get("status") == "captured"
            and frozen.get("scope") == "full_displayed_page_media_crop_intersection"
            and frozen.get("engine") == "pypdfium2"
            and frozen.get("engineVersion") == version("pypdfium2")
            and frozen.get("pdfiumVersion") == str(pdfium.PDFIUM_INFO)
            and _json(frozen.get("profile")) == _json(expected_profile)
            and frozen.get("issues") == []
            and type(frozen.get("intrinsicRotation")) is int
            and frozen["intrinsicRotation"] == 0,
            "pdf_review_recipe_unsupported",
        )
        page_number = frozen.get("page_no")
        _require(type(page_number) is int and page_number > 0)
        dimensions = frozen.get("pixelSize")
        _require(
            isinstance(dimensions, list)
            and len(dimensions) == 2
            and all(type(v) is int and v > 0 for v in dimensions)
        )
        width, height = dimensions
        _require(width * height <= MAX_IMAGE_PIXELS, "pdf_review_pixel_budget_exceeded")
        full_bounds = [0, 0, width, height]
        _require(
            frozen.get("plannedPixelSize") == dimensions
            and frozen.get("processedPixelBounds") == full_bounds
            and frozen.get("pixelCoordinateOrigin") == "TOPLEFT"
        )
        coordinates = frozen.get("renderCoordinates")
        _require(
            isinstance(coordinates, dict)
            and coordinates.get("status") == "verified"
            and coordinates.get("version") == "document-files.render-coordinates.v1"
        )
        total_pixels = width * height if include_page else 0
        for selected in requested:
            _require(
                isinstance(selected, dict) and set(selected) == {"pixelBounds", "kind", "slotKey"},
                "pdf_review_crop_invalid",
            )
            bounds = selected["pixelBounds"]
            _require(
                isinstance(bounds, list)
                and len(bounds) == 4
                and all(type(v) is int for v in bounds),
                "pdf_review_crop_invalid",
            )
            x0, y0, x1, y1 = bounds
            _require(0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height, "pdf_review_crop_invalid")
            _require(
                selected["kind"] in {"detail", "full_slot", "line_strip"}
                and (
                    selected["slotKey"] is None
                    or (
                        isinstance(selected["slotKey"], str)
                        and 0 < len(selected["slotKey"]) <= 256
                        and not any(ord(c) < 32 for c in selected["slotKey"])
                    )
                ),
                "pdf_review_crop_invalid",
            )
            _require(
                selected["kind"] == "detail" or selected["slotKey"] is not None,
                "pdf_review_crop_invalid",
            )
            total_pixels += (x1 - x0) * (y1 - y0)
            _require(total_pixels <= MAX_IMAGE_PIXELS, "pdf_review_pixel_budget_exceeded")
        check_time()
        document = pdfium.PdfDocument(content)
        document.init_forms()
        check_time()
        _require(page_number <= len(document), "pdf_review_page_invalid")
        page = document[page_number - 1]
        canvas_width, canvas_height = page.get_width(), page.get_height()
        _require(all(math.isfinite(v) and v > 0 for v in (canvas_width, canvas_height)))
        actual_geometry = {
            "pageSizeCanvasUnits": [canvas_width, canvas_height],
            "intrinsicRotation": page.get_rotation(),
            "pageBoxes": {
                "effective": list(page.get_bbox()),
                "mediaDeclared": page.get_mediabox(fallback_ok=False),
                "cropDeclared": page.get_cropbox(fallback_ok=False),
                "boxCoordinateOrigin": "BOTTOMLEFT",
                "declaredBoxesMayBeInherited": True,
            },
            "plannedPixelSize": [math.ceil(canvas_width * 3.0), math.ceil(canvas_height * 3.0)],
        }
        _require(
            all(_json(frozen.get(k)) == _json(v) for k, v in actual_geometry.items()),
            "pdf_review_geometry_changed",
        )
        _require(actual_geometry["intrinsicRotation"] == 0, "pdf_review_recipe_unsupported")
        check_time()
        bitmap = page.render(
            scale=3.0,
            rotation=0,
            crop=(0, 0, 0, 0),
            fill_color=(255, 255, 255, 255),
            draw_annots=True,
            may_draw_forms=True,
        )
        check_time()
        native_image = bitmap.to_pil()
        image = native_image.convert("RGB")
        _require(list(image.size) == dimensions, "pdf_review_geometry_changed")
        actual_coordinates = _render_coordinate_evidence(page, bitmap)
        _require(
            actual_coordinates.get("status") == "verified"
            and _json(actual_coordinates) == _json(coordinates),
            "pdf_review_coordinates_changed",
        )
        _require(
            _json(frozen.get("displayCanvasToPixelScale"))
            == _json([width / canvas_width, height / canvas_height]),
            "pdf_review_coordinates_changed",
        )
        raw_sha = _sha(image.tobytes())
        check_time()
        _require(raw_sha == frozen.get("pixelSha256"), "pdf_review_pixels_changed")
        a, b, c, d, e, f = actual_coordinates["pixelToPageAffine"]
        inputs = [(image, full_bounds, "full_page", None)] if include_page else []
        for selected in requested:
            check_time()
            detail = image.crop(tuple(selected["pixelBounds"]))
            details.append(detail)
            inputs.append((detail, selected["pixelBounds"], selected["kind"], selected["slotKey"]))
        png_images, descriptors, total_bytes = [], [], 0
        for ordinal, (current, bounds, purpose, slot_key) in enumerate(inputs):
            check_time()
            pixel_sha = raw_sha if purpose == "full_page" else _sha(current.tobytes())
            check_time()
            with _BoundedPng(MAX_IMAGE_BYTES - total_bytes) as output:
                current.save(output, format="PNG", compress_level=6, optimize=False)
                data = output.getvalue()
            check_time()
            total_bytes += len(data)
            x0, y0, x1, y1 = bounds
            affine = [a, b, c, d, a * x0 + c * y0 + e, b * x0 + d * y0 + f]
            descriptors.append(
                {
                    "id": f"page-{page_number}-image-{ordinal}",
                    "requestedPurpose": purpose,
                    "requestedSlotKey": slot_key,
                    "slotAssociationVerified": False,
                    "sourcePixelBounds": list(bounds),
                    "pixelBoundsConvention": "half_open_pixel_edges",
                    "pixelSize": list(current.size),
                    "pixelMode": "RGB",
                    "pixelSha256": pixel_sha,
                    "pngSha256": _sha(data),
                    "pngBytes": len(data),
                    "pixelToOriginalPageAffine": affine,
                    "resampling": False,
                }
            )
            png_images.append(data)
        check_time()
        descriptor = {
            "version": VERSION,
            "sourceSha256": source_hash,
            "pageNo": page_number,
            "sourceCaptureFingerprint": frozen["fingerprint"],
            "sourcePixelSha256": raw_sha,
            "engine": "pypdfium2",
            "engineVersion": frozen["engineVersion"],
            "pdfiumVersion": frozen["pdfiumVersion"],
            "renderProfile": deepcopy(expected_profile),
            "renderProfileSha256": _digest(expected_profile),
            "renderCoordinates": deepcopy(actual_coordinates),
            "pngEncoder": {
                "library": "Pillow",
                "version": version("Pillow"),
                "compressLevel": 6,
                "optimize": False,
            },
            "images": descriptors,
            "usage": {
                "renderCalls": 1,
                "renderPixels": width * height,
                "imagePixels": total_pixels,
                "pngBytes": total_bytes,
                "elapsedSeconds": time.monotonic() - started,
            },
            "limits": {
                "sourceBytes": MAX_SOURCE_BYTES,
                "imageBytes": MAX_IMAGE_BYTES,
                "imagePixels": MAX_IMAGE_PIXELS,
                "images": max(2, len(inputs)),
            },
            "captureReproduced": True,
            "contentCompletenessVerified": False,
            "ocrTruthVerified": False,
            "blankValueProven": False,
            "readingOrderVerified": False,
        }
        identity = deepcopy(descriptor)
        # Timing describes this preparation, not the source/image identity.
        del identity["usage"]["elapsedSeconds"]
        descriptor["fingerprint"] = _digest(identity)
        return PdfReviewImages(tuple(png_images), descriptor)
    except PdfReviewImageError:
        raise
    except ImportError:
        raise PdfReviewImageError("pdf_review_renderer_unavailable") from None
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, OverflowError):
        raise PdfReviewImageError("pdf_review_render_or_capture_invalid") from None
    finally:
        # Each returned byte string owns its data; all native/PIL handles close.
        failing = sys.exc_info()[0] is not None
        close_failed = False
        for resource in (*details, image, native_image, bitmap, page, document):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    close_failed = True
        if close_failed and not failing:
            raise PdfReviewImageError("pdf_review_resource_close_failed") from None
