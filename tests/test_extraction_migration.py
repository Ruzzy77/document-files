from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from dataclasses import FrozenInstanceError
from io import BytesIO
from pathlib import Path
from unittest import mock

from document_files.extraction_errors import BudgetExceededError, ExtractionError
from document_files.extraction_protocol import (
    RESULT_SCHEMA_VERSION,
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractedUnit,
    ExtractionEnvelope,
    builtin_adapter_descriptor,
    run_builtin_extraction,
)
from document_files.extraction_registry import build_default_registry
from document_files.extraction_rhwp import RhwpPageTextAdapter
from document_files.extractors import ExtractionResult, UnitDraft, extract_hwpx
from document_files.hwp5_adapter_main import (
    HWPAdapterError,
    _decode_paragraph,
    _extract,
    _inflate_raw_deflate,
    _records,
)
from document_files.hwp_structure import SectionStructure, link_document_memos
from document_files.native_adapters import _PDF_VISION_SOURCE, PDFKitVisionAdapter


def write_text_pdf(path: Path, text: bytes = b"Source backed PDF page") -> None:
    stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_number} 0 obj\n".encode())
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(pdf)


def external_descriptor(
    *,
    supports_geometry: bool = False,
    supports_ocr: bool = False,
    may_emit_partial: bool = True,
) -> AdapterDescriptor:
    return AdapterDescriptor.from_config(
        adapter_id="test.external.reader",
        adapter_version="1.0.0",
        config={},
        capabilities=AdapterCapabilities(
            format_ids=("hwp",),
            structural_unit_types=("paragraph",),
            execution_mode="jsonl_subprocess",
            supports_geometry=supports_geometry,
            supports_ocr=supports_ocr,
            may_emit_partial=may_emit_partial,
        ),
    )


def result_script(result: dict) -> str:
    encoded = json.dumps(result, ensure_ascii=False)
    return f"import sys\nsys.stdin.readline()\nprint({encoded!r})\n"


class BuiltinAdapterTest(unittest.TestCase):
    def test_builtin_result_is_deterministic_immutable_and_identity_neutral(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.md"
            path.write_text("# 제목\n\n본문", encoding="utf-8")
            first = run_builtin_extraction(path, "markdown")
            second = run_builtin_extraction(path, "markdown")

        self.assertEqual(first.manifest_hash, second.manifest_hash)
        self.assertEqual(first.completeness, "complete")
        self.assertEqual(first.descriptor.adapter_id, "document-files.builtin.markdown")
        self.assertEqual(first.units[0].unit_type, "heading")
        self.assertNotIn("document_id", first.to_dict())
        self.assertNotIn("revision_id", first.to_dict())
        self.assertNotIn("source_path", first.to_dict())
        with self.assertRaises(TypeError):
            first.units[0].structure_path["injected"] = True
        with self.assertRaises(FrozenInstanceError):
            first.completeness = "partial"

    def test_builtin_empty_result_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.txt"
            path.write_text("", encoding="utf-8")
            result = run_builtin_extraction(path, "text")

        self.assertEqual(result.completeness, "partial")
        self.assertEqual(result.units, ())
        self.assertEqual(result.issues[0].code, "no_extractable_text")

    @mock.patch("document_files.extraction_protocol.extract")
    def test_builtin_unverified_reading_order_is_declared_and_partial(
        self, extract
    ) -> None:
        unit_types = {"docx": "paragraph", "pdf": "page", "pptx": "slide_text"}
        extract.side_effect = lambda _path, adapter_name: ExtractionResult(
            units=[UnitDraft(unit_types[adapter_name], {"ordinal": 1}, "text")]
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.bin"
            path.write_bytes(b"fixture")
            for adapter_name in ("docx", "pdf", "pptx"):
                with self.subTest(adapter=adapter_name):
                    result = run_builtin_extraction(path, adapter_name)
                    self.assertFalse(
                        result.descriptor.capabilities.preserves_reading_order
                    )
                    self.assertEqual(result.completeness, "partial")
                    self.assertIn(
                        "reading_order_unverified",
                        {issue.code for issue in result.issues},
                    )

    def test_xlsx_invalid_font_family_is_ignored_in_temporary_copy(self) -> None:
        from xml.etree import ElementTree

        from openpyxl import Workbook
        from openpyxl.styles import Font

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "invalid-font-family.xlsx"
            normalized = root / "rewritten.xlsx"
            workbook = Workbook()
            workbook.active["A1"] = "색인 대상"
            workbook.active["A1"].font = Font(name="Arial", family=2)
            workbook.save(path)

            with (
                zipfile.ZipFile(path) as source,
                zipfile.ZipFile(normalized, "w", allowZip64=True) as target,
            ):
                for member in source.infolist():
                    data = source.read(member)
                    if member.filename == "xl/styles.xml":
                        styles = ElementTree.fromstring(data)
                        family = next(
                            element
                            for element in styles.iter()
                            if element.tag.rsplit("}", 1)[-1] == "family"
                        )
                        family.set("val", "49")
                        data = ElementTree.tostring(
                            styles,
                            encoding="utf-8",
                            xml_declaration=True,
                        )
                    target.writestr(member, data)
            normalized.replace(path)
            before = path.read_bytes()

            result = run_builtin_extraction(path, "xlsx")

            self.assertEqual(path.read_bytes(), before)

        self.assertEqual(result.completeness, "complete")
        self.assertEqual(result.descriptor.adapter_version, "source-units-v5")
        self.assertEqual([unit.content for unit in result.units], ["A1=색인 대상"])
        self.assertIn(
            "xlsx_invalid_font_family_ignored",
            {issue.code for issue in result.issues},
        )


class ExternalAdapterTest(unittest.TestCase):
    def test_external_adapter_preserves_virtual_environment_launcher_path(self) -> None:
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (
                sys.executable,
                "-c",
                result_script(
                    {
                        "schema_version": RESULT_SCHEMA_VERSION,
                        "completeness": "partial",
                        "units": [],
                        "issues": [],
                    }
                ),
            ),
        )

        self.assertEqual(adapter.command[0], sys.executable)

    def test_external_jsonl_reads_only_fd_and_returns_valid_envelope(self) -> None:
        script = f"""
import json
import importlib.util
import sys

request = json.loads(sys.stdin.readline())
with open(request["input"]["path"], encoding="utf-8") as source:
    content = source.read()
print(json.dumps({{
    "schema_version": {RESULT_SCHEMA_VERSION!r},
    "completeness": "complete",
    "units": [{{
        "unit_type": "paragraph",
        "structure_path": {{"paragraph": 1}},
        "content": content,
    }}],
    "issues": [],
}}, ensure_ascii=False))
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "confidential-source.hwp"
            path.write_text("업무 원문", encoding="utf-8")
            before = path.read_bytes()
            adapter = ExternalJSONLAdapter(
                external_descriptor(),
                (sys.executable, "-c", script),
            )
            result = adapter.extract(path, format_id="hwp")

            self.assertEqual(path.read_bytes(), before)

        self.assertEqual(result.completeness, "complete")
        self.assertEqual(result.units[0].content, "업무 원문")
        self.assertEqual(result.units[0].structure_path["paragraph"], 1)
        self.assertNotIn("path", result.to_dict())

    def test_external_jsonl_preserves_unicode_line_separators_inside_content(
        self,
    ) -> None:
        content = "첫 줄\u2028둘째 줄\u2029셋째 줄"
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "partial",
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"paragraph": 1},
                    "content": content,
                }
            ],
            "issues": [],
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("본문", encoding="utf-8")
            envelope = adapter.extract(path, format_id="hwp")

        self.assertEqual(envelope.units[0].content, content)

    def test_external_jsonl_still_rejects_two_physical_result_lines(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "partial",
            "units": [],
            "issues": [],
        }
        encoded = json.dumps(result)
        script = (
            "import sys\nsys.stdin.readline()\n"
            f"sys.stdout.write({(encoded + chr(10) + encoded + chr(10))!r})\n"
        )
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", script),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("본문", encoding="utf-8")
            with self.assertRaisesRegex(ExtractionError, "exactly one"):
                adapter.extract(path, format_id="hwp")

    def test_external_output_cannot_set_core_identity_or_source_path(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"paragraph": 1},
                    "content": "text",
                    "source_path": "/private/source.hwp",
                }
            ],
            "issues": [],
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(ExtractionError, "core-owned field"):
                adapter.extract(path, format_id="hwp")

    def test_external_output_cannot_set_uri_or_url_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            for forbidden_field in ("uri", "url", "original_uri", "source_url"):
                with self.subTest(field=forbidden_field):
                    result = {
                        "schema_version": RESULT_SCHEMA_VERSION,
                        "completeness": "complete",
                        "units": [
                            {
                                "unit_type": "paragraph",
                                "structure_path": {"paragraph": 1},
                                "content": "text",
                            }
                        ],
                        "issues": [
                            {
                                "code": "external_reference",
                                "message": "reference",
                                "details": {
                                    forbidden_field: "file:///private/source.hwp"
                                },
                            }
                        ],
                    }
                    adapter = ExternalJSONLAdapter(
                        external_descriptor(),
                        (sys.executable, "-c", result_script(result)),
                    )
                    with self.assertRaisesRegex(ExtractionError, "core-owned field"):
                        adapter.extract(path, format_id="hwp")

    def test_external_ocr_markers_require_declared_capability_and_exact_method(
        self,
    ) -> None:
        base_unit = {
            "unit_type": "paragraph",
            "structure_path": {"paragraph": 1},
            "content": "ocr text",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")

            for unit in (
                {**base_unit, "derivation_method": "ocr"},
                {**base_unit, "quality_flags": ["ocr"]},
                {**base_unit, "derivation_method": "ocr_v2"},
            ):
                with self.subTest(unit=unit):
                    result = {
                        "schema_version": RESULT_SCHEMA_VERSION,
                        "completeness": "complete",
                        "units": [unit],
                        "issues": [],
                    }
                    adapter = ExternalJSONLAdapter(
                        external_descriptor(),
                        (sys.executable, "-c", result_script(result)),
                    )
                    with self.assertRaises(ExtractionError):
                        adapter.extract(path, format_id="hwp")

            valid_result = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "completeness": "complete",
                "units": [
                    {
                        **base_unit,
                        "derivation_method": "ocr",
                        "quality_flags": ["ocr"],
                    }
                ],
                "issues": [],
            }
            adapter = ExternalJSONLAdapter(
                external_descriptor(supports_ocr=True),
                (sys.executable, "-c", result_script(valid_result)),
            )
            envelope = adapter.extract(path, format_id="hwp")
            self.assertEqual(envelope.units[0].derivation_method, "ocr")

    def test_external_result_schema_is_strict(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [],
            "issues": [],
            "document_metadata": {"title": "adapter-controlled"},
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(ExtractionError, "unknown fields"):
                adapter.extract(path, format_id="hwp")

    def test_external_warning_forces_honest_partial_result(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"paragraph": 1},
                    "content": "partial text",
                }
            ],
            "issues": [
                {
                    "code": "embedded_object_skipped",
                    "message": "One embedded object was not extracted.",
                    "severity": "warning",
                }
            ],
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            envelope = adapter.extract(path, format_id="hwp")

        self.assertEqual(envelope.completeness, "partial")

    def test_external_timeout_is_bounded(self) -> None:
        script = "import sys, time\nsys.stdin.readline()\ntime.sleep(2)\n"
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", script),
            AdapterBudgets(timeout_seconds=0.05),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(BudgetExceededError, "timeout"):
                adapter.extract(path, format_id="hwp")

    def test_external_stdout_is_hard_bounded(self) -> None:
        script = "import sys\nsys.stdin.readline()\nsys.stdout.write('x' * 100000)\n"
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", script),
            AdapterBudgets(max_stdout_bytes=512),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(BudgetExceededError, "stdout"):
                adapter.extract(path, format_id="hwp")


class PackagedAdapterTest(unittest.TestCase):
    def test_pdf_native_source_is_installed_package_data(self) -> None:
        self.assertTrue(_PDF_VISION_SOURCE.is_file())
        self.assertEqual(_PDF_VISION_SOURCE.parent.name, "native")
        self.assertEqual(_PDF_VISION_SOURCE.parent.parent.name, "document_files")

    def test_default_registry_routes_pdf_and_binary_hwp_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_default_registry(Path(temporary) / "runtime")

        pdf = registry.resolve("pdf").descriptor
        hwp = registry.resolve("hwp").descriptor
        self.assertEqual(pdf.adapter_id, "document-files.native.pdfkit-vision")
        self.assertTrue(pdf.capabilities.supports_ocr)
        self.assertTrue(pdf.capabilities.supports_geometry)
        self.assertIn("table_cell", pdf.capabilities.structural_unit_types)
        self.assertEqual(hwp.adapter_id, "document-files.native.office-vision.hwp")
        self.assertTrue(hwp.capabilities.supports_ocr)
        self.assertTrue(hwp.capabilities.may_emit_partial)

    def test_hwp_and_hwpx_structure_upgrade_requires_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_default_registry(Path(temporary) / "runtime")

        self.assertFalse(
            registry.accepts_projection(
                "hwp",
                "document-files.hwp5.spec-partial",
                "1.0.0+source.ee6920f82733",
                "636b97fef8e7a824315f7398170b37ff304c71495da1c0f6ad6a5a26b01a8207",
            )
        )
        self.assertFalse(
            registry.accepts_projection(
                "hwpx",
                "document-files.hwpx.content-router",
                "1.0.0+source.6e30c615a7b4",
                "7657fe15069210b062a68711808ed87f5928de3e271bacf333a138d17976fbf7",
            )
        )
        self.assertFalse(
            registry.accepts_projection(
                "hwp",
                "document-files.hwp5.spec-partial",
                "0.0.0",
                "0" * 64,
            )
        )

    def test_rhwp_adapter_uses_only_inherited_fd_and_discards_source_locator(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "rhwp"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "if sys.argv[1] == '--version':\n"
                "    print('rhwp v0.8.2')\n"
                "    raise SystemExit(0)\n"
                "assert sys.argv[1] == 'export-text'\n"
                "assert sys.argv[2].startswith('/dev/fd/')\n"
                "with open(sys.argv[2], encoding='utf-8') as source:\n"
                "    text = source.read()\n"
                "print(json.dumps({'schemaVersion': '1.0', 'pageCount': 1, "
                "'pages': [{'page': 0, 'text': text}], "
                "'source': '/private/source.hwp'}, ensure_ascii=False))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            path = root / "confidential.hwp"
            path.write_text("배포용 본문", encoding="utf-8")
            before = path.read_bytes()
            adapter = RhwpPageTextAdapter(root / "runtime", executable=executable)

            result = adapter.extract(path, format_id="hwp")

            self.assertEqual(path.read_bytes(), before)

        self.assertEqual([unit.content for unit in result.units], ["배포용 본문"])
        self.assertEqual(result.units[0].structure_path["page"], 1)
        self.assertNotIn("source", result.to_dict())
        self.assertEqual(result.completeness, "partial")

    @unittest.skipUnless(sys.platform == "darwin", "PDFKit is available only on macOS")
    def test_pdf_native_adapter_treats_blank_page_as_observed(self) -> None:
        from pypdf import PdfReader, PdfWriter

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_page = root / "text.pdf"
            path = root / "text-with-blank-page.pdf"
            write_text_pdf(first_page, b"Searchable first page with enough native text")
            writer = PdfWriter()
            writer.add_page(PdfReader(first_page).pages[0])
            writer.add_blank_page(width=100, height=100)
            with path.open("wb") as handle:
                writer.write(handle)
            result = PDFKitVisionAdapter(root / "runtime").extract(
                path,
                format_id="pdf",
            )

        self.assertEqual(result.completeness, "complete")
        self.assertEqual(len(result.units), 1)
        self.assertNotIn(
            "pdf_page_without_text", {issue.code for issue in result.issues}
        )

    @unittest.skipUnless(sys.platform == "darwin", "PDFKit is available only on macOS")
    def test_pdf_native_adapter_uses_hybrid_ocr_and_keeps_text_pdf_complete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "text.pdf"
            write_text_pdf(
                path, b"This native PDF page has enough searchable text for indexing"
            )
            adapter = PDFKitVisionAdapter(root / "runtime")
            result = adapter.extract(path, format_id="pdf")

        self.assertEqual(adapter.config["ocr_scope"], "hybrid")
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(
            [unit.derivation_method for unit in result.units],
            ["native_text"],
        )
        self.assertIn("reading_order_unverified", result.units[0].quality_flags)
        self.assertEqual({i.code for i in result.issues}, {"pdf_page_range_observed"})
        self.assertTrue(all(i.severity == "info" for i in result.issues))

    @unittest.skipUnless(sys.platform == "darwin", "PDFKit is available only on macOS")
    def test_pdf_native_adapter_falls_back_to_pypdf_on_native_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "fallback.pdf"
            write_text_pdf(path, b"PDFKit compatibility fallback text")
            adapter = PDFKitVisionAdapter(root / "runtime")
            with (
                mock.patch.object(adapter, "_build", return_value=Path(sys.executable)),
                mock.patch(
                    "document_files.native_adapters.ExternalJSONLAdapter.extract",
                    side_effect=ExtractionError("native PDF adapter failed"),
                ),
            ):
                result = adapter.extract(path, format_id="pdf")

        self.assertEqual(result.descriptor, adapter.descriptor)
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(
            [unit.content for unit in result.units],
            ["PDFKit compatibility fallback text"],
        )
        self.assertIn("pypdf_fallback", result.units[0].quality_flags)
        self.assertIn("reading_order_unverified", result.units[0].quality_flags)
        self.assertEqual({i.code for i in result.issues}, {"pdf_page_range_observed"})
        self.assertTrue(all(i.severity == "info" for i in result.issues))

    def test_hwpx_security_change_does_not_invalidate_other_builtin_formats(
        self,
    ) -> None:
        self.assertEqual(
            builtin_adapter_descriptor("hwpx").adapter_version,
            "source-units-v9",
        )
        self.assertEqual(
            builtin_adapter_descriptor("markdown").adapter_version,
            "source-units-v4",
        )

    def test_hwpx_router_preserves_zip_hwpx_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_default_registry(Path(temporary) / "runtime")
            path = Path(temporary) / "sample.hwpx"
            xml = """<?xml version="1.0" encoding="UTF-8"?>
            <hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">
              <hp:p><hp:run><hp:t>정상 HWPX</hp:t></hp:run></hp:p>
            </hs:sec>
            """
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Contents/section0.xml", xml)

            binary_hwp = registry.resolve("hwp").native
            with mock.patch.object(
                binary_hwp,
                "extract",
                side_effect=AssertionError("binary HWP adapter must not be called"),
            ) as binary_extract:
                result = registry.resolve("hwpx").extract(path, format_id="hwpx")

        binary_extract.assert_not_called()
        self.assertEqual([unit.content for unit in result.units], ["정상 HWPX"])
        self.assertEqual(
            result.descriptor.adapter_id,
            "document-files.native.office-vision.hwpx",
        )

    def test_hwpx_router_sends_ole_bytes_to_binary_hwp_adapter_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_default_registry(Path(temporary) / "runtime")
            path = Path(temporary) / "misnamed.hwpx"
            original = bytes.fromhex("d0cf11e0a1b11ae1") + b"binary-hwp-fixture"
            path.write_bytes(original)

            binary_hwp = registry.resolve("hwp").native
            binary_result = ExtractionEnvelope.create(
                descriptor=binary_hwp.descriptor,
                completeness="partial",
                units=(
                    ExtractedUnit(
                        unit_type="section_paragraph",
                        structure_path={"section": 1, "paragraph": 1},
                        content="binary HWP 본문",
                        quality_flags=("binary_hwp", "structure_partial"),
                    ),
                ),
            )
            with mock.patch.object(
                binary_hwp,
                "extract",
                return_value=binary_result,
            ) as binary_extract:
                result = registry.resolve("hwpx").extract(path, format_id="hwpx")

            self.assertEqual(path.read_bytes(), original)

        binary_extract.assert_called_once_with(path, format_id="hwp")
        self.assertEqual([unit.content for unit in result.units], ["binary HWP 본문"])
        self.assertEqual(result.completeness, "partial")
        self.assertIn(
            "hwpx_contains_binary_hwp",
            {issue.code for issue in result.issues},
        )
        self.assertEqual(
            result.descriptor.adapter_id,
            "document-files.native.office-vision.hwpx",
        )

    def test_rhwp_output_is_stopped_at_the_shared_runtime_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "rhwp"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "if sys.argv[1] == '--version':\n"
                " print('rhwp v0.8.2')\n"
                "else:\n"
                " sys.stdout.write('X' * 100000)\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            source = root / "source.hwp"
            source.write_bytes(b"fixture")
            adapter = RhwpPageTextAdapter(root / "runtime", executable=executable)
            adapter.budgets = AdapterBudgets(max_stdout_bytes=128)
            with self.assertRaises(BudgetExceededError):
                adapter.extract(source, format_id="hwp")
            self.assertEqual(source.read_bytes(), b"fixture")

    def test_hwp_structure_preserves_cells_notes_and_distinct_source_occurrences(
        self,
    ) -> None:
        normal = {"head_type": "none", "level": 1}
        reader = SectionStructure(
            1,
            "Section0",
            [
                normal,
                {"head_type": "outline", "level": 2},
                {"head_type": "number", "level": 1},
            ],
            [{"name": "Normal"}],
        )

        def paragraph(record, level, shape=0):
            data = bytearray(24)
            struct.pack_into("<H", data, 8, shape)
            reader.observe(record, 0x42, level, bytes(data))

        def cell(record, level, row, col, row_span=1, col_span=1, header=False):
            data = bytearray(34)
            struct.pack_into("<H", data, 6, 4 if header else 0)
            struct.pack_into("<HHHH", data, 8, col, row, col_span, row_span)
            reader.observe(record, 0x48, level, bytes(data))

        paragraph(1, 0)
        reader.text(2, 1, 1, "본문")
        reader.observe(3, 0x47, 1, b"tbl "[::-1])
        reader.observe(4, 0x4D, 2, struct.pack("<IHH", 0, 2, 2))
        cell(5, 2, 0, 0, col_span=2, header=True)
        paragraph(6, 2)
        reader.text(7, 3, 2, "반복")
        cell(8, 2, 1, 0)
        paragraph(9, 2)
        reader.text(10, 3, 3, "반복")
        reader.observe(11, 0x47, 3, b"tbl "[::-1])
        reader.observe(12, 0x4D, 4, struct.pack("<IHH", 0, 1, 1))
        cell(13, 4, 0, 0)
        paragraph(14, 4)
        cell(15, 2, 1, 1)
        paragraph(16, 2)
        reader.text(17, 3, 4, "100")
        paragraph(18, 0, 1)
        reader.text(19, 1, 5, "제목")
        paragraph(20, 0, 2)
        reader.text(21, 1, 6, "목록")
        reader.observe(22, 0x47, 1, b"fn  "[::-1] + struct.pack("<I", 4))
        reader.observe(23, 0x48, 2, bytes(16))
        paragraph(24, 2)
        reader.text(25, 3, 7, "주석")
        units, issues = reader.finish()
        text = [u for u in units if u["content"]]
        self.assertEqual(
            [u["content"] for u in text],
            ["본문", "반복", "반복", "100", "제목", "목록", "주석"],
        )
        repeated = [u["structure_path"] for u in text if u["content"] == "반복"]
        self.assertEqual([u["cell"] for u in repeated], ["r5", "r8"])
        self.assertEqual(repeated[0]["col_span"], 2)
        self.assertEqual(sum(u["unit_type"] == "table" for u in units), 2)
        self.assertEqual(text[-3]["unit_type"], "heading")
        self.assertEqual(text[-2]["unit_type"], "list_item")
        self.assertEqual(text[-1]["unit_type"], "footnote")
        self.assertEqual(text[-1]["structure_path"]["owner_paragraph_record"], 20)
        self.assertFalse(any(i["code"].startswith("hwp_table_") for i in issues))

    def test_hwp_endnote_and_equation_keep_source_owned_locations(self) -> None:
        reader = SectionStructure(
            1, "Section0", [{"head_type": "none"}], [{"name": "Normal"}]
        )
        reader.observe(1, 0x42, 0, bytes(24))
        reader.text(2, 1, 1, "참조")
        reader.observe(3, 0x47, 1, b"en  "[::-1] + struct.pack("<I", 7))
        reader.observe(4, 0x48, 2, bytes(16))
        reader.observe(5, 0x42, 2, bytes(24))
        reader.text(6, 3, 2, "미주")
        reader.observe(7, 0x42, 0, bytes(24))
        reader.observe(8, 0x47, 1, b"eqed"[::-1])
        script = "x + y"
        reader.observe(
            9, 0x58, 2, struct.pack("<IH", 0, len(script)) + script.encode("utf-16-le")
        )
        units, _issues = reader.finish()
        note = next(u for u in units if u["content"] == "미주")
        self.assertEqual(note["unit_type"], "endnote")
        self.assertEqual(note["structure_path"]["owner_paragraph_record"], 1)
        equation = next(u for u in units if u["content"] == script)
        self.assertEqual(equation["structure_path"]["record"], 9)
        self.assertEqual(equation["structure_path"]["owner_paragraph_record"], 7)
        self.assertEqual(
            equation["structure_path"]["text_representation"], "hwp_equation_script"
        )

    def test_hwpx_nested_text_order_cells_and_notes_are_not_duplicated(self) -> None:
        xml = """<sec><p><run><t>앞</t><tbl rowCnt="1" colCnt="2"><tr>
          <tc header="1"><cellAddr colAddr="0" rowAddr="0"/><cellSpan colSpan="1" rowSpan="1"/>
            <subList><p><run><t>항목</t></run></p></subList></tc>
          <tc><cellAddr colAddr="1" rowAddr="0"/><cellSpan colSpan="1" rowSpan="1"/>
            <subList><p><run><t>100<tab/>원</t></run></p></subList></tc>
        </tr></tbl><t>뒤</t><footNote number="1">
          <subList><p><run><t>주석</t></run></p></subList>
        </footNote></run></p></sec>"""
        source = BytesIO()
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("Contents/section0.xml", xml)
            archive.writestr(
                "Contents/section10.xml", "<sec><p><run><t>열</t></run></p></sec>"
            )
            archive.writestr(
                "Contents/section2.xml", "<sec><p><run><t>둘</t></run></p></sec>"
            )
        result = extract_hwpx(source)
        text = [u for u in result.units if u.content]
        self.assertEqual(
            [u.content for u in text],
            ["앞", "항목", "100\t원", "뒤", "주석", "둘", "열"],
        )
        self.assertEqual(text[2].structure_path["col"], 1)
        self.assertEqual(text[4].unit_type, "footnote")
        self.assertEqual(
            text[4].structure_path["owner_paragraph"],
            text[0].structure_path["paragraph_element"],
        )
        self.assertFalse(result.issues)

        package_order = BytesIO()
        with zipfile.ZipFile(package_order, "w") as archive:
            archive.writestr("Contents/section0.xml", "<sec><p><t>끝</t></p></sec>")
            archive.writestr(
                "Contents/section2.xml", xml.replace('colAddr="1"', 'colAddr="bad"')
            )
            archive.writestr(
                "Contents/content.hpf",
                '<package><manifest><item id="a" href="section0.xml"/>'
                '<item id="b" href="section2.xml"/></manifest>'
                '<spine><itemref idref="b"/><itemref idref="a"/></spine></package>',
            )
        partial = extract_hwpx(package_order)
        self.assertEqual(
            [u.content for u in partial.units if u.content],
            ["앞", "항목", "100\t원", "뒤", "주석", "끝"],
        )
        unmapped = next(u for u in partial.units if u.content == "100\t원")
        self.assertNotIn("col", unmapped.structure_path)
        self.assertIn("cell", unmapped.structure_path)
        self.assertIn(
            "hwpx_table_geometry_partial", {issue["code"] for issue in partial.issues}
        )

    def test_hwp5_paragraph_control_decoder_preserves_text_boundaries(self) -> None:
        def extended_control(code: int) -> bytes:
            return struct.pack("<H", code) + (b"\x00" * 12) + struct.pack("<H", code)

        payload = (
            "산업".encode("utf-16-le")
            + extended_control(9)
            + "AI".encode("utf-16-le")
            + struct.pack("<H", 10)
            + struct.pack("<H", 30)
            + "과제".encode("utf-16-le")
            + struct.pack("<H", 13)
        )

        text, controls, anomalies = _decode_paragraph(payload)

        self.assertEqual(text, "산업\tAI\n 과제")
        self.assertEqual(dict(controls), {9: 1, 10: 1, 30: 1, 13: 1})
        self.assertEqual(anomalies, [])

    def test_hwp_memo_siblings_and_native_field_metadata(self) -> None:
        reader = SectionStructure(1, "BodyText/Section0", [{}], [{}])
        reader.observe(1, 0x42, 0, b"\0" * 12)
        command = "native field instruction".encode("utf-16-le")
        data = (
            b"klc%"
            + struct.pack("<IBH", 0, 0, len(command) // 2)
            + command
            + struct.pack("<I", 77)
        )
        reader.observe(2, 0x47, 1, data)
        reader.observe(3, 0x5D, 1, b"\0" * 4)
        reader.observe(4, 0x48, 1, b"\0" * 16)
        reader.observe(5, 0x42, 1, b"\0" * 12)
        reader.text(6, 2, 1, "Memo text")
        reader.observe(7, 0x42, 0, b"\0" * 12)
        reader.text(8, 1, 2, "Body after memo")
        units, issues = reader.finish()
        memo = next(u for u in units if u["content"] == "Memo text")
        self.assertEqual(memo["unit_type"], "comment")
        self.assertEqual(memo["structure_path"]["memo_list_record"], 3)
        body = next(u for u in units if u["content"] == "Body after memo")
        self.assertNotIn("note", body["structure_path"])
        field = next(u for u in units if u["unit_type"] == "field")
        self.assertEqual(field["structure_path"]["field_id"], 77)
        self.assertEqual(field["structure_path"]["field_type"], "click_here")
        self.assertNotIn("hwp_container_structure_partial", {i["code"] for i in issues})
        self.assertIn("hwp_memo_attachment_unresolved", {i["code"] for i in issues})

    def test_hwp_field_range_and_unique_memo_token_preserve_text(self) -> None:
        def marker(code, token=0):
            return (
                struct.pack("<H", code)
                + b"em%%"
                + b"\0" * 4
                + struct.pack("<IH", token, code)
            )

        payload = (
            "  가😀".encode("utf-16-le")
            + marker(3)
            + "본문".encode("utf-16-le")
            + marker(4, 41)
            + "끝  ".encode("utf-16-le")
        )
        markers = []
        text, _, anomalies = _decode_paragraph(payload, field_markers=markers)
        self.assertEqual(text, "가😀본문끝")
        self.assertFalse(anomalies)
        command = "MEMO/".encode("utf-16-le")
        header = (
            b"knu%"
            + struct.pack("<IBH", 0, 0, len(command) // 2)
            + command
            + struct.pack("<II", 77, 900)
        )
        for duplicate in (False, True):
            with self.subTest(duplicate_header=duplicate):
                reader = SectionStructure(1, "BodyText/Section0", [{}], [{}])
                reader.observe(1, 0x42, 0, b"\0" * 12)
                reader.fields(2, 1, markers)
                reader.text(2, 1, 1, text)
                reader.observe(3, 0x47, 1, header)
                if duplicate:
                    reader.observe(4, 0x47, 1, header)
                reader.observe(5, 0x5D, 1, struct.pack("<I", 41))
                reader.observe(6, 0x48, 1, b"\0" * 16)
                reader.observe(7, 0x42, 1, b"\0" * 12)
                reader.text(8, 2, 2, "Memo text")
                units, issues = reader.finish()
                issues = link_document_memos(units, issues)
                memo = next(u for u in units if u["content"] == "Memo text")
                fields = [
                    u["structure_path"] for u in units if u["unit_type"] == "field"
                ]
                self.assertEqual(
                    [u["content"] for u in units if u["content"]], [text, "Memo text"]
                )
                if duplicate:
                    self.assertTrue(all("field_range" not in f for f in fields))
                    self.assertNotIn("memo_attachment", memo["structure_path"])
                    self.assertIn(
                        "hwp_field_range_partial", {i["code"] for i in issues}
                    )
                else:
                    span = fields[0]["field_range"]
                    self.assertEqual(span["start"]["offset_utf16"], 13)
                    self.assertEqual(
                        text[
                            span["start"]["content_offset"] : span["end"][
                                "content_offset"
                            ]
                        ],
                        "본문",
                    )
                    self.assertEqual(fields[0]["field_header_tail_value"], 900)
                    self.assertEqual(
                        memo["structure_path"]["memo_attachment"]["field_id"], 77
                    )
                    self.assertNotIn(
                        "hwp_memo_attachment_unresolved", {i["code"] for i in issues}
                    )

    def test_hwpx_field_ranges_memo_and_highlight_preserve_text(self) -> None:
        xml = """<sec><p><run><t>Before</t><ctrl>
          <fieldBegin id="10" type="MEMO" fieldid="3"><parameters>
            <stringParam name="author">Writer</stringParam>
          </parameters>
          <subList><p><run><t>Memo text</t></run></p></subList></fieldBegin>
          </ctrl><t>Marked<markpenBegin color="#FFFF00"/> value<markpenEnd/></t>
          <ctrl><fieldEnd beginIDRef="10" fieldid="3"/></ctrl><t>After</t></run></p></sec>"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fields.hwpx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Contents/section0.xml", xml)
            result = extract_hwpx(path)
        self.assertEqual(
            [u.content for u in result.units if u.content],
            ["Before", "Memo text", "Marked value", "After"],
        )
        memo = next(u for u in result.units if u.content == "Memo text")
        self.assertEqual(memo.unit_type, "comment")
        self.assertEqual(memo.structure_path["note"], "10")
        field = next(u for u in result.units if u.unit_type == "field")
        self.assertIn("end_element", field.structure_path)
        marked = next(u for u in result.units if u.content == "Marked value")
        self.assertEqual(marked.structure_path["field_path"], ["10"])
        self.assertEqual(len(marked.structure_path["format_markers"]), 2)
        self.assertFalse(any(i["severity"] == "warning" for i in result.issues))

    def test_hwp5_record_and_raw_deflate_boundaries_are_bounded(self) -> None:
        payload = "본문".encode("utf-16-le")
        short_header = 0x43 | (1 << 10) | (len(payload) << 20)
        record_stream = struct.pack("<I", short_header) + payload
        compressed = zlib.compressobj(wbits=-15)
        deflated = compressed.compress(record_stream) + compressed.flush()

        inflated = _inflate_raw_deflate(deflated, limit=1024)
        records = list(_records(inflated, max_records=2))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1:3], (0x43, 1))
        self.assertEqual(records[0][3], payload)
        with self.assertRaisesRegex(Exception, "byte budget"):
            _inflate_raw_deflate(deflated, limit=2)

    def test_hwp5_child_enforces_exact_fd_and_result_budgets(self) -> None:
        paragraphs = []
        for text in ("첫째", "둘째"):
            payload = text.encode("utf-16-le")
            header = 0x43 | (1 << 10) | (len(payload) << 20)
            paragraphs.append(struct.pack("<I", header) + payload)
        section = b"".join(paragraphs)
        file_header = bytearray(40)
        file_header[: len(b"HWP Document File")] = b"HWP Document File"

        class FakeCompound:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def exists(self, name):
                return name == "FileHeader"

            def listdir(self, **_kwargs):
                return [["BodyText", "Section0"]]

            def openstream(self, name):
                if name == "FileHeader":
                    return BytesIO(bytes(file_header))
                return BytesIO(section)

        opened_file_descriptors = []

        def open_fake_compound(source, **_kwargs):
            self.assertTrue(hasattr(source, "read"))
            opened_file_descriptors.append(source.fileno())
            return FakeCompound()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.hwp"
            path.write_bytes(b"fixture")
            file_descriptor = path.open("rb")
            try:
                base_request = {
                    "schema_version": "document-files.extraction-request.v1",
                    "operation": "extract",
                    "input": {
                        "kind": "read_only_file_descriptor",
                        "file_descriptor": file_descriptor.fileno(),
                        "path": f"/dev/fd/{file_descriptor.fileno()}",
                        "format_id": "hwp",
                    },
                    "config": {},
                    "budgets": {
                        "max_units": 1,
                        "max_unit_content_chars": 100,
                        "max_total_content_chars": 100,
                    },
                }
                with mock.patch(
                    "document_files.hwp5_adapter_main.olefile.OleFileIO",
                    side_effect=open_fake_compound,
                ):
                    with self.assertRaisesRegex(HWPAdapterError, "unit count"):
                        _extract(base_request)
                    self.assertEqual(len(opened_file_descriptors), 1)
                    self.assertNotEqual(
                        opened_file_descriptors[0], file_descriptor.fileno()
                    )
                    traversal = {
                        **base_request,
                        "input": {
                            **base_request["input"],
                            "path": "/dev/fd/../../tmp/other.hwp",
                        },
                    }
                    with self.assertRaisesRegex(HWPAdapterError, "invalid input"):
                        _extract(traversal)
            finally:
                file_descriptor.close()


if __name__ == "__main__":
    unittest.main()
