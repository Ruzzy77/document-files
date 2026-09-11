"""Synthetic source/render integrity regressions, not OCR or model qualification."""

import base64
import hashlib
import io
import json
import time
from copy import deepcopy

import pytest

from document_files.interpretation import pdf_review_images as review

pdfium = pytest.importorskip("pypdfium2")
Image = pytest.importorskip("PIL.Image")
canvas = pytest.importorskip("reportlab.pdfgen.canvas")

from document_files.document_model import recognition_visual, recognition_worker  # noqa: E402
from document_files.document_model.recognition_sources import page_render_fingerprint  # noqa: E402


@pytest.fixture
def source(monkeypatch):
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(120, 80), invariant=True)
    pdf.drawString(8, 40, "A 1.020")
    pdf.rect(5, 5, 110, 65)
    pdf.showPage()
    pdf.drawString(10, 20, "Second page")
    pdf.save()
    content = output.getvalue()
    # Baseline has no OCR or CV analysis. The capture's RGB and coordinates are real.
    monkeypatch.setattr(recognition_visual, "observe_rgb", lambda *a, **kw: {"status": "not_run"})
    document = pdfium.PdfDocument(content)
    try:
        document.init_forms()
        capture = recognition_worker.capture_full_page_render(
            document, 0, hashlib.sha256(content).hexdigest()
        )
    finally:
        document.close()
    assert capture["status"] == "captured"
    monkeypatch.setattr(
        recognition_visual, "observe_rgb", lambda *a, **kw: pytest.fail("unexpected pixel analysis")
    )
    return content, capture


def run(source, **kwargs):
    content, capture = source
    return review.prepare_pdf_review_images(
        content, capture, deadline=kwargs.pop("deadline", time.monotonic() + 30), **kwargs
    )


def resign(capture):
    capture["fingerprint"] = page_render_fingerprint(capture)


def test_exact_render_and_crop_with_no_public_image_bytes(source):
    content, capture = source
    original = deepcopy(capture)
    crop = {"pixelBounds": [15, 30, 150, 120], "kind": "full_slot", "slotKey": "slot-1"}
    result = run(source, crop=crop)
    assert capture == original
    assert crop["pixelBounds"] == [15, 30, 150, 120]
    descriptor = result.descriptor
    assert descriptor["sourceSha256"] == hashlib.sha256(content).hexdigest()
    assert descriptor["sourceCaptureFingerprint"] == capture["fingerprint"]
    assert descriptor["sourcePixelSha256"] == capture["pixelSha256"]
    assert descriptor["usage"]["renderCalls"] == 1
    assert descriptor["usage"]["imagePixels"] == 360 * 240 + 135 * 90
    assert descriptor["captureReproduced"] is True
    for flag in (
        "contentCompletenessVerified",
        "ocrTruthVerified",
        "blankValueProven",
        "readingOrderVerified",
    ):
        assert descriptor[flag] is False
    assert "data:image" not in json.dumps(descriptor)
    images = []
    try:
        for index, data in enumerate(result.png_images):
            images.append(Image.open(io.BytesIO(data)))
            item = descriptor["images"][index]
            assert images[-1].mode == "RGB"
            assert hashlib.sha256(images[-1].tobytes()).hexdigest() == item["pixelSha256"]
            assert hashlib.sha256(data).hexdigest() == item["pngSha256"]
            assert item["pngBytes"] == len(data)
            assert item["slotAssociationVerified"] is False
            assert (
                base64.b64decode(result.content_parts()[index]["image_url"]["url"].split(",")[1])
                == data
            )
        with images[0].crop((15, 30, 150, 120)) as expected:
            assert expected.tobytes() == images[1].tobytes()
    finally:
        for image in images:
            image.close()
    a, b, c, d, e, f = capture["renderCoordinates"]["pixelToPageAffine"]
    assert descriptor["images"][1]["pixelToOriginalPageAffine"] == [
        a,
        b,
        c,
        d,
        a * 15 + c * 30 + e,
        b * 15 + d * 30 + f,
    ]
    descriptor["images"][0]["pixelSize"][0] = 1
    assert result.descriptor["images"][0]["pixelSize"] == [360, 240]


def test_full_page_only(source):
    result = run(source)
    assert len(result.png_images) == 1
    assert result.descriptor["images"][0]["requestedPurpose"] == "full_page"


def test_source_change(source):
    with pytest.raises(review.PdfReviewImageError, match="source_changed"):
        run((source[0] + b"\n", source[1]))


def test_capture_mutation_without_new_fingerprint(source):
    source[1]["pixelSha256"] = "a" * 64
    with pytest.raises(review.PdfReviewImageError, match="capture_invalid"):
        run(source)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("engineVersion", "different", "recipe_unsupported"),
        ("pdfiumVersion", "different", "recipe_unsupported"),
        ("intrinsicRotation", 90, "recipe_unsupported"),
        ("intrinsicRotation", False, "recipe_unsupported"),
        ("intrinsicRotation", None, "recipe_unsupported"),
        ("status", "failed", "recipe_unsupported"),
        ("pixelSha256", "b" * 64, "pixels_changed"),
        ("page_no", 3, "page_invalid"),
        ("page_no", True, "capture_invalid"),
        ("page_no", 2, "pixels_changed"),
        ("pageSizeCanvasUnits", [121, 80], "geometry_changed"),
        ("processedPixelBounds", [0, 0, 359, 240], "capture_invalid"),
        ("displayCanvasToPixelScale", [2.0, 3.0], "coordinates_changed"),
    ],
)
def test_changed_capture_rejected(source, field, value, code):
    source[1][field] = value
    resign(source[1])
    with pytest.raises(review.PdfReviewImageError, match=code):
        run(source)


def test_changed_coordinate_mapping(source):
    source[1]["renderCoordinates"]["pixelToPageAffine"][4] += 1
    resign(source[1])
    with pytest.raises(review.PdfReviewImageError, match="coordinates_changed"):
        run(source)


def test_unknown_transform_rejected(source):
    source[1]["renderCoordinates"]["status"] = "unverified"
    resign(source[1])
    with pytest.raises(review.PdfReviewImageError, match="capture_invalid"):
        run(source)


@pytest.mark.parametrize(
    "crop",
    [
        {},
        {"pixelBounds": [0, 0, 1, 1], "kind": "detail"},
        {"pixelBounds": [0, 0, 0, 1], "kind": "detail", "slotKey": None},
        {"pixelBounds": [0, 0, 361, 240], "kind": "detail", "slotKey": None},
        {"pixelBounds": [-1, 0, 1, 1], "kind": "detail", "slotKey": None},
        {"pixelBounds": [False, 0, 1, 1], "kind": "detail", "slotKey": None},
        {"pixelBounds": [0.0, 0, 1, 1], "kind": "detail", "slotKey": None},
        {"pixelBounds": [0, 0, 1, 1], "kind": "full_slot", "slotKey": None},
        {"pixelBounds": [0, 0, 1, 1], "kind": "blank", "slotKey": "s"},
    ],
)
def test_invalid_crops(source, crop):
    with pytest.raises(review.PdfReviewImageError, match="crop_invalid"):
        run(source, crop=crop)


def test_pixel_limit_before_render(source, monkeypatch):
    capture = source[1]
    capture["pixelSize"] = capture["plannedPixelSize"] = [4001, 4000]
    capture["processedPixelBounds"] = [0, 0, 4001, 4000]
    resign(capture)
    monkeypatch.setattr(pdfium, "PdfDocument", lambda *_: pytest.fail("opened oversized PDF"))
    with pytest.raises(review.PdfReviewImageError, match="pixel_budget_exceeded"):
        run(source)


def test_combined_pixel_limit_before_render(source, monkeypatch):
    capture = source[1]
    capture["pixelSize"] = capture["plannedPixelSize"] = [3000, 3000]
    capture["processedPixelBounds"] = [0, 0, 3000, 3000]
    resign(capture)
    monkeypatch.setattr(pdfium, "PdfDocument", lambda *_: pytest.fail("opened oversized PDF"))
    with pytest.raises(review.PdfReviewImageError, match="pixel_budget_exceeded"):
        run(source, crop={"pixelBounds": [0, 0, 3000, 3000], "kind": "detail", "slotKey": None})


def test_source_bytes_limit(source, monkeypatch):
    monkeypatch.setattr(review, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(review.PdfReviewImageError, match="source_byte_budget_exceeded"):
        run(source)


def test_png_bytes_limit(source, monkeypatch):
    monkeypatch.setattr(review, "MAX_IMAGE_BYTES", 20)
    with pytest.raises(review.PdfReviewImageError, match="image_byte_budget_exceeded"):
        run(source)


@pytest.mark.parametrize(
    "invalid", ["/tmp/doc.pdf", "https://x/doc.pdf", bytearray(b"%PDF-"), b"broken"]
)
def test_no_paths_urls_or_nonpdf(source, invalid):
    with pytest.raises(review.PdfReviewImageError, match="source_invalid"):
        run((invalid, source[1]))


@pytest.mark.parametrize("invalid", [True, float("inf"), float("nan"), "tomorrow"])
def test_invalid_deadline(source, invalid):
    with pytest.raises(review.PdfReviewImageError, match="configuration_invalid"):
        run(source, deadline=invalid)


def test_expired_and_cancelled_before_open(source, monkeypatch):
    monkeypatch.setattr(pdfium, "PdfDocument", lambda *_: pytest.fail("unexpected open"))
    with pytest.raises(review.PdfReviewImageError, match="timeout"):
        run(source, deadline=time.monotonic() - 1)
    with pytest.raises(review.PdfReviewImageError, match="cancelled"):
        run(source, cancelled=lambda: True)


def test_cancel_after_render_closes_resources(source, monkeypatch):
    original_render = pdfium.PdfPage.render
    original_close = pdfium.PdfDocument.close
    state = {"rendered": False, "closed": False}

    def render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        state["rendered"] = True
        return result

    def close(*args, **kwargs):
        state["closed"] = True
        return original_close(*args, **kwargs)

    monkeypatch.setattr(pdfium.PdfPage, "render", render)
    monkeypatch.setattr(pdfium.PdfDocument, "close", close)
    with pytest.raises(review.PdfReviewImageError, match="cancelled"):
        run(source, cancelled=lambda: state["rendered"])
    assert state["closed"]


def test_input_freeze_before_native_open(source, monkeypatch):
    original_document = pdfium.PdfDocument

    def opening(*args, **kwargs):
        source[1]["pixelSha256"] = "f" * 64
        return original_document(*args, **kwargs)

    monkeypatch.setattr(pdfium, "PdfDocument", opening)
    assert run(source).descriptor["sourcePixelSha256"] != "f" * 64


def test_identical_preparations_have_stable_identity(source):
    first = run(source).descriptor
    second = run(source).descriptor
    assert first["fingerprint"] == second["fingerprint"]
    assert first["usage"]["elapsedSeconds"] >= 0
    assert second["usage"]["elapsedSeconds"] >= 0


def test_timeout_after_render_closes_resources(source, monkeypatch):
    original_render = pdfium.PdfPage.render
    original_close = pdfium.PdfDocument.close
    state = {"now": 10.0, "closed": False}

    def render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        state["now"] = 31.0
        return result

    def close(*args, **kwargs):
        state["closed"] = True
        return original_close(*args, **kwargs)

    monkeypatch.setattr(pdfium.PdfPage, "render", render)
    monkeypatch.setattr(pdfium.PdfDocument, "close", close)
    monkeypatch.setattr(review.time, "monotonic", lambda: state["now"])
    with pytest.raises(review.PdfReviewImageError, match="timeout"):
        run(source, deadline=30.0)
    assert state["closed"]


def test_cancel_after_encode(source, monkeypatch):
    original_save = Image.Image.save
    state = {"encoded": False}

    def save(*args, **kwargs):
        result = original_save(*args, **kwargs)
        state["encoded"] = True
        return result

    monkeypatch.setattr(Image.Image, "save", save)
    with pytest.raises(review.PdfReviewImageError, match="cancelled"):
        run(source, cancelled=lambda: state["encoded"])


def test_close_failure_does_not_skip_document_cleanup(source, monkeypatch):
    original_close = pdfium.PdfBitmap.close
    original_document_close = pdfium.PdfDocument.close
    state = {"documentClosed": False}

    def bitmap_close(*args, **kwargs):
        original_close(*args, **kwargs)
        raise RuntimeError("private upstream error")

    def document_close(*args, **kwargs):
        state["documentClosed"] = True
        return original_document_close(*args, **kwargs)

    monkeypatch.setattr(pdfium.PdfBitmap, "close", bitmap_close)
    monkeypatch.setattr(pdfium.PdfDocument, "close", document_close)
    with pytest.raises(review.PdfReviewImageError, match="resource_close_failed"):
        run(source)
    assert state["documentClosed"]


def test_mutated_actual_geometry_rejected_before_render(source, monkeypatch):
    original_bbox = pdfium.PdfPage.get_bbox

    def bbox(*args, **kwargs):
        result = list(original_bbox(*args, **kwargs))
        result[0] += 1
        return tuple(result)

    monkeypatch.setattr(pdfium.PdfPage, "get_bbox", bbox)
    monkeypatch.setattr(pdfium.PdfPage, "render", lambda *a, **kw: pytest.fail("unexpected render"))
    with pytest.raises(review.PdfReviewImageError, match="geometry_changed"):
        run(source)


def test_line_strips_are_lossless_crops_without_the_page_image(source):
    content, capture = source
    strips = [
        {"id": "s0", "pageBounds": [0, 0, 60, 30]},
        {"id": "s1", "pageBounds": [0, 30, 120, 80]},
    ]
    result = review.prepare_pdf_line_strips(
        content, capture, strips, deadline=time.monotonic() + 30
    )
    images = result.descriptor["images"]
    assert [
        (i["requestedPurpose"], i["requestedSlotKey"], i["sourcePixelBounds"]) for i in images
    ] == [
        ("line_strip", "s0", [0, 0, 60, 30]),
        ("line_strip", "s1", [0, 30, 120, 80]),
    ]
    assert all(i["resampling"] is False for i in images) and len(result.png_images) == 2
    assert result.descriptor["version"] == "document-files.pdf-review-images.v2"
    assert result.descriptor["limits"]["images"] == 2
    page = run(source)
    with (
        Image.open(io.BytesIO(page.png_images[0])) as full,
        Image.open(io.BytesIO(result.png_images[1])) as strip,
    ):
        assert strip.size == (120, 50)
        assert full.crop((0, 30, 120, 80)).tobytes() == strip.tobytes()
    again = review.prepare_pdf_line_strips(content, capture, strips, deadline=time.monotonic() + 30)
    assert again.descriptor["fingerprint"] == result.descriptor["fingerprint"]
    for invalid in (
        [],
        [{"id": "s0", "pageBounds": [0, 0, 361, 30]}],
        [{"id": None, "pageBounds": [0, 0, 60, 30]}],
    ):
        with pytest.raises(review.PdfReviewImageError, match="crop_invalid"):
            review.prepare_pdf_line_strips(
                content, capture, invalid, deadline=time.monotonic() + 30
            )
    with pytest.raises(review.PdfReviewImageError, match="crop_invalid"):
        run(source, crop={"pixelBounds": [0, 0, 1, 1], "kind": "detail", "slotKey": None}, crops=[])
