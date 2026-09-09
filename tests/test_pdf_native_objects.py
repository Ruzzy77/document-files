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
    assert result["status"] == "partial", result
    page = result["pages"][0]
    assert not page["completeness"]["eligibleForNativeCellReasoning"]
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
    assert verify(source, result)["status"] == "unverified"
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


def embedded_document(text="A B", *, transform=None, render_mode=0):
    """Package-owned font and synthetic PDF only; no external source or rendering."""
    from pathlib import Path

    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    name = "NativeInventoryFixtureVera"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont(name, str(Path(reportlab.__file__).parent / "fonts/Vera.ttf"))
        )

    def draw(canvas):
        if transform:
            transform(canvas)
        textobject = canvas.beginText(40, 220)
        textobject.setFont(name, 12)
        textobject.setTextRenderMode(render_mode)
        textobject.textOut(text)
        canvas.drawText(textobject)

    return document(draw)


def embedded_inventory(text="A B", **options):
    source = embedded_document(text)
    return source, inventory_pdf_native_objects(source, **options)


def test_embedded_format6_font_bytes_outline_and_source_empty_glyph_are_linked():
    source, result = embedded_inventory()
    assert result["status"] == "complete", result
    page = result["pages"][0]
    obj = page["objects"][0]
    font = next(f for f in page["fontResources"] if f["id"] == obj["fontResourceRef"])
    assert len(font["streams"]) == 2
    assert obj["nativeFontSha256"] == font["streams"][0]["decodedSha256"]
    assert obj["text"] == "A B"
    assert obj["paintBoundsStatus"] == "verified"
    first, space, last = obj["paintGlyphs"]
    assert first["basis"] == last["basis"] == "native_outline_control_hull"
    assert space["basis"] == "source_loca_zero_length"
    assert space["loca"][0] == space["loca"][1]
    assert space["sourceCode"] == "20" and space["controlPoints"] == []
    assert obj["paintSupportBounds"][0] <= obj["bounds"][0]
    assert obj["paintSupportBounds"][2] >= obj["bounds"][2]
    assert verify(source, result)["status"] == "verified"
    assert result["documentCompletenessVerified"] is False


def test_nonembedded_font_is_not_exempt_from_paint_bounds_check():
    source, result = inspect(lambda c: c.drawString(20, 20, "A B"))
    obj = result["pages"][0]["objects"][0]
    assert obj["text"] == "A B" and obj["paintBoundsStatus"] == "unverified"
    assert result["status"] == "partial"
    assert verify(source, result)["status"] == "unverified"


@pytest.mark.parametrize(
    "feature", ["null_outline", "zero_segments", "font_data", "substitute", "ambiguous"]
)
def test_embedded_font_native_mismatch_cannot_approve(feature, monkeypatch):
    import pypdfium2.raw as raw

    from document_files.document_model import pdf_native_objects as module

    if feature == "null_outline":
        monkeypatch.setattr(raw, "FPDFFont_GetGlyphPath", lambda *args: None)
    elif feature == "zero_segments":
        monkeypatch.setattr(raw, "FPDFGlyphPath_CountGlyphSegments", lambda *args: 0)
    elif feature == "font_data":
        original = raw.FPDFFont_GetFontData

        def changed(font, buffer, size, actual):
            result = original(font, buffer, size, actual)
            if buffer is not None and size:
                buffer[0] ^= 1
            return result

        monkeypatch.setattr(raw, "FPDFFont_GetFontData", changed)
    elif feature == "substitute":
        monkeypatch.setattr(raw, "FPDFFont_GetIsEmbedded", lambda *args: 0)
    else:
        original = module._one_byte_cmap

        def ambiguous(*args):
            result = original(*args)
            result["01"] = result["41"]
            return result

        monkeypatch.setattr(module, "_one_byte_cmap", ambiguous)
    source, result = embedded_inventory("A")
    assert result["status"] == "partial"
    assert result["pages"][0]["objects"][0]["paintBoundsStatus"] == "unverified"
    assert verify(source, result)["status"] == "unverified"


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_embedded_stroke_or_invisible_text_is_not_fill_outline_evidence(mode):
    source = embedded_document("A", render_mode=mode)
    result = inventory_pdf_native_objects(source)
    assert result["status"] == "partial"
    assert verify(source, result)["status"] == "unverified"


def test_embedded_rotated_character_transform_is_not_guessed():
    source = embedded_document("A", transform=lambda c: c.rotate(15))
    result = inventory_pdf_native_objects(source)
    assert result["status"] == "partial"
    assert "transform_unsupported" in result["pages"][0]["objects"][0]["paintBoundsReason"]


def test_outline_overhang_expands_metrics_bounds_instead_of_clipping(monkeypatch):
    from pypdfium2 import PdfObject

    original = PdfObject.get_bounds

    def metrics_inside_actual_outline(obj):
        if obj.type == 1:
            return (42, 221, 43, 222)
        return original(obj)

    monkeypatch.setattr(PdfObject, "get_bounds", metrics_inside_actual_outline)
    source, result = embedded_inventory("A")
    obj = result["pages"][0]["objects"][0]
    assert obj["bounds"] == [42, 78, 43, 79]
    assert obj["paintSupportBounds"][0] < 42
    assert obj["paintSupportBounds"][2] > 43
    assert obj["paintSupportBounds"][1] < 78
    assert verify(source, result)["status"] == "verified"


@pytest.mark.parametrize(
    "limit,value",
    [("maxStreams", 2), ("maxDecodedBytes", 35_000), ("maxSegments", 20), ("maxOperators", 200)],
)
def test_font_inspection_shares_original_total_budgets(limit, value):
    source, result = embedded_inventory(limits={limit: value})
    assert result["status"] == "partial"
    assert verify(source, result)["status"] == "unverified"


@pytest.mark.parametrize(
    "field", ["matrix", "nativeFontSha256", "glyphId", "paintSupportBounds", "version"]
)
def test_font_and_outline_receiver_rechecks_links_after_refingerprinting(field):
    source, result = embedded_inventory("A B")
    page = result["pages"][0]
    obj = page["objects"][0]
    if field == "matrix":
        obj["paintGlyphs"][0]["matrix"][0] *= 2
    elif field == "glyphId":
        obj["paintGlyphs"][0]["glyphId"] = obj["paintGlyphs"][1]["glyphId"]
    elif field == "version":
        result["version"] = "document-files.pdf-native-objects.v1"
    elif field == "nativeFontSha256":
        obj[field] = "0" * 64
    else:
        obj[field][2] -= 1
    obj["fingerprint"] = fingerprint(obj)
    page["fingerprint"] = fingerprint(page)
    result["fingerprint"] = fingerprint(result)
    assert verify(source, result)["status"] == "unverified"


def font_program_fixture():
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdftypes import resolve1

    from document_files.document_model.pdf_native_objects import _decoded

    source = embedded_document("A B")
    page = next(PDFPage.get_pages(io.BytesIO(source)))
    for value in resolve1(resolve1(page.resources)["Font"]).values():
        font = resolve1(value)
        descriptor = resolve1(font.get("FontDescriptor", {}))
        if "FontFile2" in descriptor:
            return _decoded(resolve1(descriptor["FontFile2"]), 1_000_000)[0]
    raise AssertionError("synthetic embedded font missing")


@pytest.mark.parametrize("feature", ["format", "multiple", "loca", "glyph_budget"])
def test_reportlab_adapter_rejects_unsupported_or_invalid_font_tables(feature):
    import struct
    import time

    from reportlab.pdfbase.ttfonts import TTFontParser

    from document_files.document_model.pdf_native_objects import DEFAULT_LIMITS, _truetype_format6

    data = bytearray(font_program_fixture())
    parser = TTFontParser(io.BytesIO(data))
    cmap = parser.table["cmap"]["offset"]
    if feature == "format":
        struct.pack_into(">H", data, cmap + 12, 4)
    elif feature == "multiple":
        struct.pack_into(">H", data, cmap + 2, 2)
    elif feature == "loca":
        # Last offset is invalid regardless of whether loca uses ushort/ulong.
        loca = parser.table["loca"]
        data[loca["offset"] : loca["offset"] + loca["length"]] = b"\xff" * loca["length"]
    else:
        struct.pack_into(">H", data, parser.table["maxp"]["offset"] + 4, 60000)
    with pytest.raises(ValueError):
        _truetype_format6(
            bytes(data), dict.fromkeys(DEFAULT_LIMITS, 0), DEFAULT_LIMITS, time.monotonic() + 10
        )


@pytest.mark.parametrize(
    "feature", ["duplicate", "truncated", "outside", "ligature", "usecmap", "range"]
)
def test_cmap_invalid_or_unsupported_mapping_is_not_silently_accepted(feature):
    import time

    from document_files.document_model.pdf_native_objects import DEFAULT_LIMITS, _one_byte_cmap

    prefix = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        b"/CIDSystemInfo << /Registry (test) /Ordering (test) /Supplement 0 >> def "
        b"/CMapName /test def /CMapType 2 def "
        b"1 begincodespacerange <00> <7F> endcodespacerange "
    )
    tail = b" endcmap CMapName currentdict /CMap defineresource pop end end"
    bodies = {
        "duplicate": b"2 beginbfchar <41> <0041> <41> <0042> endbfchar",
        "truncated": b"2 beginbfchar <41> <0041> endbfchar",
        "outside": b"1 beginbfchar <FF> <0041> endbfchar",
        "ligature": b"1 beginbfchar <41> <00410042> endbfchar",
        "usecmap": b"/other usecmap 1 beginbfchar <41> <0041> endbfchar",
        "range": b"1 beginbfrange <00> <FF> <0041> endbfrange",
    }
    with pytest.raises(ValueError):
        _one_byte_cmap(
            prefix + bodies[feature] + tail,
            dict.fromkeys(DEFAULT_LIMITS, 0),
            DEFAULT_LIMITS,
            time.monotonic() + 10,
        )


def test_font_program_budget_failure_prevents_native_page_parse(monkeypatch):
    from pypdfium2 import PdfDocument

    source = embedded_document()

    def forbidden(*args, **kwargs):
        raise AssertionError("native page parser ran after font source budget failure")

    monkeypatch.setattr(PdfDocument, "__getitem__", forbidden)
    result = inventory_pdf_native_objects(source, limits={"maxStreams": 2})
    assert result["status"] == "partial"
    page = result["pages"][0]
    assert page["objects"] == []
    assert any(
        i["code"] == "pdfium_objects_not_enumerated_for_unsupported_source" for i in page["issues"]
    )


def test_repeated_font_selection_reuses_source_stream_inventory():
    from reportlab.pdfbase import pdfmetrics

    # Register the package fixture, then use it twice in the same source page.
    embedded_document()
    assert "NativeInventoryFixtureVera" in pdfmetrics.getRegisteredFontNames()

    def draw(c):
        for y in (200, 220):
            c.setFont("NativeInventoryFixtureVera", 12)
            c.drawString(40, y, "A B")

    source, result = inspect(draw)
    page = result["pages"][0]
    used = [f for f in page["fontResources"] if f["status"] == "verified"]
    assert len(used) == 1 and len(used[0]["streams"]) == 2
    assert result["usage"]["maxStreams"] == 3
    assert verify(source, result)["status"] == "verified"


def test_receiver_does_not_accept_empty_glyph_by_unicode_name(monkeypatch):
    import pypdfium2.raw as raw

    from document_files.document_model import pdf_native_objects as module

    original = module._truetype_format6

    def nonempty_space(*args):
        result = original(*args)
        glyph = result["codeToGlyph"]["20"]
        # Keep the program offsets ordered but remove the actual zero extent.
        result["glyphOffsets"][glyph] -= 1
        return result

    monkeypatch.setattr(module, "_truetype_format6", nonempty_space)
    native = raw.FPDFFont_GetGlyphPath
    monkeypatch.setattr(
        raw,
        "FPDFFont_GetGlyphPath",
        lambda font, u, size: None if u == 32 else native(font, u, size),
    )
    source, result = embedded_inventory()
    assert result["status"] == "partial"
    assert "outline_unavailable" in result["pages"][0]["objects"][0]["paintBoundsReason"]
    assert verify(source, result)["status"] == "unverified"


def adjacent_text_document():
    embedded_document()  # Register the package-owned synthetic font.

    def draw(c):
        c.setFont("NativeInventoryFixtureVera", 12)
        c.drawString(20, 140, "Qty")
        c.drawString(120, 140, "0 ")

    return document(draw)


def test_generated_projection_space_and_literal_space_remain_distinct():
    source = adjacent_text_document()
    result = inventory_pdf_native_objects(source)
    assert result["status"] == "complete", result
    first, second = result["pages"][0]["objects"]
    assert first["text"] == "Qty " and first["sourceText"] == "Qty"
    assert second["text"] == second["sourceText"] == "0 "
    generated = first["projectionCharacters"][-1]
    assert generated == {
        "textPageIndex": 3,
        "unicode": 32,
        "apiGenerated": 1,
        "objectMembership": "no_object",
    }
    assert len(first["nativeCharacters"]) == len(first["paintGlyphs"]) == 3
    literal = second["nativeCharacters"][-1]
    assert literal["unicode"] == 32 and literal["apiGenerated"] == 0
    assert literal["objectMembership"] == "same_object"
    assert second["paintGlyphs"][-1]["basis"] == "source_loca_zero_length"
    assert second["paintSupportBounds"][2] >= second["bounds"][2]
    assert verify(source, result)["status"] == "verified"


@pytest.mark.parametrize(
    "feature",
    ["unknown_generated", "not_generated", "foreign_owner", "additional_actual", "unicode_changed"],
)
def test_generated_projection_requires_exact_native_status_and_membership(feature, monkeypatch):
    import pypdfium2.raw as raw

    generated = raw.FPDFText_IsGenerated
    owner = raw.FPDFText_GetTextObject
    unicode = raw.FPDFText_GetUnicode
    if feature in {"unknown_generated", "not_generated", "additional_actual"}:
        value = -1 if feature == "unknown_generated" else 0
        monkeypatch.setattr(
            raw, "FPDFText_IsGenerated", lambda tp, i: value if i == 3 else generated(tp, i)
        )
    if feature in {"foreign_owner", "additional_actual"}:
        other_index = 4 if feature == "foreign_owner" else 0
        monkeypatch.setattr(
            raw,
            "FPDFText_GetTextObject",
            lambda tp, i: owner(tp, other_index) if i == 3 else owner(tp, i),
        )
    if feature == "unicode_changed":
        monkeypatch.setattr(
            raw, "FPDFText_GetUnicode", lambda tp, i: ord("X") if i == 3 else unicode(tp, i)
        )
    source = adjacent_text_document()
    result = inventory_pdf_native_objects(source)
    assert result["status"] == "partial"
    assert result["pages"][0]["objects"][0]["paintBoundsStatus"] == "unverified"
    assert verify(source, result)["status"] == "unverified"


@pytest.mark.parametrize(
    "field",
    [
        "generated",
        "owner",
        "order",
        "sourceText",
        "range",
        "scan",
        "glyph_membership",
        "old_version",
    ],
)
def test_v3_receiver_rechecks_projection_source_and_glyph_membership(field):
    source = adjacent_text_document()
    result = inventory_pdf_native_objects(source)
    page = result["pages"][0]
    obj = page["objects"][0]
    if field == "generated":
        obj["projectionCharacters"][-1]["apiGenerated"] = 0
    elif field == "owner":
        obj["projectionCharacters"][-1]["objectMembership"] = "other_object"
    elif field == "order":
        obj["projectionCharacters"] = obj["projectionCharacters"][::-1]
    elif field == "sourceText":
        obj["sourceText"] += " "
    elif field == "range":
        obj["projectionRange"] = [0, 3]
    elif field == "scan":
        obj["membershipScan"]["scannedCharacters"] -= 1
    elif field == "glyph_membership":
        obj["paintGlyphs"][-1]["textPageIndex"] = 3
    else:
        result["version"] = "document-files.pdf-native-objects.v2"
    obj["fingerprint"] = fingerprint(obj)
    page["fingerprint"] = fingerprint(page)
    result["fingerprint"] = fingerprint(result)
    assert verify(source, result)["status"] == "unverified"
