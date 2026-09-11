"""Serialized single-page subsets must not change identity between runs."""

import hashlib
import io
import re

import pypdfium2 as pdfium

from document_files.document_model.recognition_worker import deterministic_subset


def _subset():
    document = pdfium.PdfDocument.new()
    try:
        document.new_page(120, 80)
        stream = io.BytesIO()
        document.save(stream)
    finally:
        document.close()
    return stream.getvalue()


def _render_sha(content):
    document = pdfium.PdfDocument(content)
    try:
        bitmap = document[0].render(scale=1)
        return hashlib.sha256(bitmap.to_pil().convert("RGB").tobytes()).hexdigest()
    finally:
        document.close()


def test_file_identifier_and_clock_time_are_replaced_without_moving_bytes():
    first, second = _subset(), _subset()
    assert len(first) == len(second)
    a = deterministic_subset(first, "a" * 64, 2)
    b = deterministic_subset(second, "a" * 64, 2)
    assert a == b and len(a) == len(first)
    assert re.search(rb"/ID\s*\[\s*<([0-9A-F]+)>", a).group(1) != re.search(
        rb"/ID\s*\[\s*<([0-9A-Fa-f]+)>", first
    ).group(1)
    assert re.search(rb"/CreationDate\s*\(D:([^)]*)\)", a).group(1) == re.sub(
        rb"[0-9]", b"0", re.search(rb"/CreationDate\s*\(D:([^)]*)\)", first).group(1)
    )
    # A different source page keeps its own identity; the page still opens and renders.
    assert deterministic_subset(first, "a" * 64, 3) != a
    assert deterministic_subset(first, "b" * 64, 2) != a
    assert _render_sha(a) == _render_sha(first)


def test_unexpected_identifier_shape_is_left_untouched():
    odd = b"%PDF-1.7\ntrailer<</ID[<ABCD><ABCDEF>]>>\n%%EOF\n"
    assert deterministic_subset(odd, "a" * 64, 1) == odd
    plain = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
    assert deterministic_subset(plain, "a" * 64, 1) == plain
