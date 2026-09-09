"""Synthetic source objects only: no rasterization/OCR or quality qualification."""

import hashlib
import io
from copy import deepcopy

import pytest

from document_files.document_model.pdf_native_objects import (
    inventory_pdf_native_objects,
    validate_native_inventory,
)
from document_files.document_model.recognition_coordinates import fingerprint


def document(draw):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfminer")
    canvas_module = pytest.importorskip("reportlab.pdfgen.canvas")
    stream = io.BytesIO()
    canvas = canvas_module.Canvas(stream, pagesize=(200, 300), invariant=True, pageCompression=0)
    draw(canvas)
    canvas.save()
    return stream.getvalue()


def inspect(draw, **options):
    source = document(draw)
    return source, inventory_pdf_native_objects(source, **options)


def verify(source, inventory, page=1):
    return validate_native_inventory(
        inventory, source_sha256=hashlib.sha256(source).hexdigest(), page=page
    )


def test_plain_source_lines_text_and_fill_are_preserved_and_counted():
    def draw(canvas):
        canvas.setLineWidth(1)
        canvas.line(20, 40, 20, 250)
        canvas.drawString(40, 220, "0007 | 7.250")
        canvas.setFillColorRGB(0.9, 0.9, 0.9)
        canvas.rect(20, 240, 150, 20, stroke=0, fill=1)

    source, result = inspect(draw)
    assert result["status"] == "complete", result
    page = result["pages"][0]
    assert page["completeness"]["eligibleForNativeCellReasoning"]
    assert page["paintCounts"] == {
        "sourcePaint": {"path": 2, "text": 1, "image": 0, "form": 0},
        "pdfiumObjects": {"path": 2, "text": 1, "image": 0, "form": 0},
        "matched": True,
    }
    line, text, fill = page["objects"]
    assert line["kind"] == "primitive_line" and line["segments"] == [[20, 260, 20, 50]]
    assert line["strokeWidth"] == 1 and line["strokeColor"] == [0, 0, 0, 255]
    assert line["dash"] == [] and not line["fill"]
    assert text["kind"] == "text" and text["text"] == "0007 | 7.250"
    assert fill["kind"] == "other" and fill["fill"] and not fill["stroke"]
    assert verify(source, result)["status"] == "verified"
    assert result["blankValueProven"] is False and not result["rendered"]


@pytest.mark.parametrize(
    "feature", ["clip", "form", "annotation", "transparency", "rotation", "unknown", "text_clip"]
)
def test_unsupported_source_content_cannot_be_complete(feature):
    def draw(c):
        if feature == "clip":
            path = c.beginPath()
            path.rect(0, 0, 100, 100)
            c.clipPath(path, stroke=0)
        elif feature == "form":
            c.beginForm("hidden")
            c.line(1, 1, 20, 20)
            c.endForm()
            c.doForm("hidden")
        elif feature == "annotation":
            c.linkURL("https://example.invalid", (10, 10, 40, 40))
        elif feature == "transparency":
            c.setStrokeAlpha(0.5)
        elif feature == "rotation":
            c.setPageRotation(90)
        elif feature == "unknown":
            c._code.append("0 mystery")
        elif feature == "text_clip":
            c._code.append("BT /F1 12 Tf 7 Tr 10 10 Td (x) Tj ET")
        c.line(20, 20, 20, 200)

    source, result = inspect(draw)
    assert result["status"] == "partial"
    assert verify(source, result)["status"] == "unverified"


@pytest.mark.parametrize("style", ["dash", "round_cap", "diagonal", "compound", "hairline"])
def test_nonprimitive_paths_are_preserved_without_stroke_approval(style):
    def draw(c):
        if style == "dash":
            c.setDash(3, 2)
        if style == "round_cap":
            c.setLineCap(1)
        if style == "hairline":
            c.setLineWidth(0)
        if style == "compound":
            path = c.beginPath()
            path.moveTo(20, 20)
            path.lineTo(20, 200)
            path.lineTo(80, 200)
            c.drawPath(path)
        else:
            c.line(20, 20, 80 if style == "diagonal" else 20, 200)

    _, result = inspect(draw)
    assert result["pages"][0]["objects"][0]["kind"] == "other"


@pytest.mark.parametrize(
    "limits",
    [
        {"maxInputBytes": 1},
        {"maxObjects": 1},
        {"maxOperators": 1},
        {"maxDecodedBytes": 1},
        {"maxPages": 1},
    ],
)
def test_budget_exhaustion_preserves_partial_inventory(limits):
    def draw(c):
        c.line(10, 10, 10, 90)
        c.drawString(20, 50, "test")
        c.showPage()
        c.line(20, 10, 20, 90)

    source, result = inspect(draw, limits=limits)
    assert result["status"] == "partial"
    if result["pages"] and limits != {"maxPages": 1}:
        assert verify(source, result)["status"] == "unverified"


def test_identity_and_paint_counts_are_rechecked_not_only_complete_flag():
    source, result = inspect(lambda c: c.line(20, 20, 20, 100))
    assert verify(source, result)["status"] == "verified"
    wrong = deepcopy(result)
    wrong["pages"][0]["sourceOperators"] = []
    wrong["pages"][0]["fingerprint"] = fingerprint(wrong["pages"][0])
    wrong["fingerprint"] = fingerprint(wrong)
    assert verify(source, wrong)["status"] == "unverified"
    assert (
        validate_native_inventory(result, source_sha256="f" * 64, page=1)["status"] == "unverified"
    )
    assert verify(source, result, page=2)["status"] == "unverified"


@pytest.mark.parametrize("operators", ["Q", "BT", "1 2", "(bad) w", "[1 2", "BT 10 Tr (x) Tj ET"])
def test_incomplete_or_invalid_source_operations_remain_partial(operators):
    source, result = inspect(lambda c: c._code.append(operators))
    assert result["status"] == "partial"
    assert verify(source, result)["status"] == "unverified"


def test_bounded_stream_decompression_and_unknown_filters():
    import zlib

    pytest.importorskip("pdfminer")
    from pdfminer.pdftypes import PDFStream
    from pdfminer.psparser import LIT

    from document_files.document_model.pdf_native_objects import _decoded

    compressed = PDFStream({"Filter": LIT("FlateDecode")}, zlib.compress(b"x" * 20000))
    with pytest.raises(ValueError):
        _decoded(compressed, 100)
    unknown = PDFStream({"Filter": LIT("Unknown")}, b"bytes")
    with pytest.raises(ValueError):
        _decoded(unknown, 100)


def test_empty_pdfium_clip_reference_requires_independent_source_support():
    source, result = inspect(lambda c: c.line(20, 20, 20, 100))
    line = result["pages"][0]["objects"][0]
    assert line["rawClipPathCount"] == -1
    assert line["clipPathCount"] == 0
    assert line["clipBasis"] == "source_no_clip_operators_and_pdfium_no_ref"
    wrong = deepcopy(result)
    target = wrong["pages"][0]
    target["objects"][0]["clipBasis"] = "assumed_no_clip"
    target["objects"][0]["fingerprint"] = fingerprint(target["objects"][0])
    target["fingerprint"] = fingerprint(target)
    wrong["fingerprint"] = fingerprint(wrong)
    assert verify(source, wrong)["status"] == "unverified"


def test_hiding_clipping_issue_does_not_make_inventory_eligible():
    def draw(c):
        path = c.beginPath()
        path.rect(0, 0, 100, 100)
        c.clipPath(path, stroke=0)
        c.line(20, 20, 20, 90)

    source, result = inspect(draw)
    page = result["pages"][0]
    page["issues"] = []
    page["completeness"] = {k: k != "unsupportedContentPresent" for k in page["completeness"]}
    page["fingerprint"] = fingerprint(page)
    result["fingerprint"] = fingerprint(result)
    assert verify(source, result)["status"] == "unverified"


def test_source_image_is_preserved_as_non_line_without_loading_pixels():
    image_module = pytest.importorskip("PIL.Image")
    from reportlab.lib.utils import ImageReader

    image = image_module.new("RGB", (2, 2), "black")
    source, result = inspect(lambda c: c.drawImage(ImageReader(image), 40, 40, 20, 20))
    assert result["status"] == "complete", result
    assert result["pages"][0]["objects"][0]["kind"] == "image"
    assert result["pages"][0]["paintCounts"]["sourcePaint"]["image"] == 1
    assert verify(source, result)["status"] == "verified"


def test_native_inventory_does_not_modify_input_bytes():
    source, result = inspect(lambda c: c.drawString(20, 20, "tiny native text"))
    before = hashlib.sha256(source).hexdigest()
    assert result["sourceSha256"] == before
    assert hashlib.sha256(source).hexdigest() == before
    assert result["rendered"] is False and result["ocrExecuted"] is False


def test_unsupported_stream_is_checked_before_native_page_load(monkeypatch):
    import pypdfium2 as pdfium

    original = pdfium.PdfDocument
    source = document(lambda c: c._code.append("1 unsupported_operator"))

    class Guard:
        def __init__(self, content):
            self.document = original(content)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.document.close()

        def __len__(self):
            return len(self.document)

        def __getitem__(self, _):
            pytest.fail("unsupported source reached native page parser")

    monkeypatch.setattr(pdfium, "PdfDocument", Guard)
    result = inventory_pdf_native_objects(source)
    assert result["status"] == "partial"
    assert not result["pages"][0]["objects"]
    assert any(
        i["code"] == "pdfium_objects_not_enumerated_for_unsupported_source"
        for i in result["pages"][0]["issues"]
    )


def test_primitive_segment_cannot_be_changed_independently_of_source_path():
    source, result = inspect(lambda c: c.line(20, 20, 20, 100))
    obj = result["pages"][0]["objects"][0]
    obj["segments"][0][0] = obj["segments"][0][2] = 25
    obj["fingerprint"] = fingerprint(obj)
    result["pages"][0]["fingerprint"] = fingerprint(result["pages"][0])
    result["fingerprint"] = fingerprint(result)
    assert verify(source, result)["status"] == "unverified"


def test_unbalanced_source_state_cannot_be_approved_by_copied_counts():
    source, result = inspect(lambda c: c.drawString(20, 20, "test"))
    page = result["pages"][0]
    # Retain the operator/count metadata but mutate BT into a balanced-arity no-op.
    next(op for op in page["sourceOperators"] if op["operator"] == "BT")["operator"] = "n"
    page["fingerprint"] = fingerprint(page)
    result["fingerprint"] = fingerprint(result)
    assert verify(source, result)["status"] == "unverified"
