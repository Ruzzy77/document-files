from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from test_extraction_migration import write_text_pdf

from document_files.extraction_errors import BudgetExceededError, ExtractionError
from document_files.native_adapters import PDFKitVisionAdapter


@unittest.skipUnless(sys.platform == "darwin", "PDFKit requires macOS")
class PDFContinuationTest(unittest.TestCase):
    def test_fallback_ranges_and_terminal_single_page_budget(self):
        from pypdf import PdfReader, PdfWriter

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            page_path, path = base / "page.pdf", base / "long.pdf"
            write_text_pdf(
                page_path, b"Native text which exceeds one character on every page"
            )
            writer = PdfWriter()
            for _ in range(201):
                writer.add_page(PdfReader(page_path).pages[0])
            writer.write(path)
            adapter = PDFKitVisionAdapter(base / "runtime")
            with mock.patch.object(
                adapter, "_build", side_effect=ExtractionError("native unavailable")
            ):
                first = adapter.extract(path, format_id="pdf")
                resumed = adapter.resume(path, format_id="pdf", previous=first)
            self.assertEqual(len(resumed.units), 201)
            self.assertEqual(resumed.completeness, "complete")
            self.assertIn("pypdf_fallback", resumed.units[-1].quality_flags)

            adapter.budgets = replace(adapter.budgets, max_unit_content_chars=1)
            terminal = adapter.resume(path, format_id="pdf", previous=first)
            self.assertEqual(terminal.units, first.units)
            self.assertNotIn(
                "pdf_page_range_pending", {issue.code for issue in terminal.issues}
            )
            self.assertIn(
                "pdf_page_unit_budget_exhausted",
                {issue.code for issue in terminal.issues},
            )
            with self.assertRaises(BudgetExceededError):
                adapter.extract(path, format_id="pdf")


if __name__ == "__main__":
    unittest.main()
