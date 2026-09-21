from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validate_ocr_batch import (
    extract_ocr_page_numbers,
    format_page_ranges,
    render_markdown_report,
    summarize,
    validate_document,
)


class OCRValidationTests(unittest.TestCase):
    def test_extracts_page_numbers_and_formats_ranges(self) -> None:
        markdown = "## 第 1 页（OCR）\n正文\n\n## 第 3 页（OCR）\n正文"

        self.assertEqual(extract_ocr_page_numbers(markdown), [1, 3])
        self.assertEqual(format_page_ranges([1, 2, 3, 5, 8, 9]), "1-3, 5, 8-9")

    def test_accepts_missing_output_page_only_when_render_is_exactly_blank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.pdf"
            output = root / "sample.pdf.md"
            source.write_bytes(b"fake-pdf")
            output.write_text(
                "# sample.pdf\n\n## 第 1 页（OCR）\n\n正文\n\n"
                "## 第 3 页（OCR）\n\n<table><tr><td>数据</td></tr></table>\n",
                encoding="utf-8",
            )
            manifest = {
                "source": "sample.pdf",
                "status": "ready",
                "parser": "paddleocr-vl-v1.6",
                "output": str(output),
                "text_characters": 10,
                "warnings": [],
            }

            with patch("scripts.validate_ocr_batch._pdf_page_count", return_value=3):
                with patch(
                    "scripts.validate_ocr_batch.detect_confirmed_blank_pages",
                    return_value=[2],
                ):
                    record = validate_document(source, manifest, cwd=root)

            self.assertTrue(record.passed)
            self.assertEqual(record.ocr_pages, [1, 3])
            self.assertEqual(record.confirmed_blank_pages, [2])
            self.assertEqual(record.html_tables, 1)

    def test_rejects_uncovered_duplicate_and_out_of_range_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.pdf"
            output = root / "sample.pdf.md"
            source.write_bytes(b"fake-pdf")
            output.write_text(
                "## 第 1 页（OCR）\n正文\n## 第 1 页（OCR）\n重复\n"
                "## 第 4 页（OCR）\n越界\n",
                encoding="utf-8",
            )
            manifest = {
                "source": "sample.pdf",
                "status": "ready",
                "parser": "paddleocr-vl-v1.6",
                "output": str(output),
                "warnings": [],
            }

            with patch("scripts.validate_ocr_batch._pdf_page_count", return_value=3):
                with patch(
                    "scripts.validate_ocr_batch.detect_confirmed_blank_pages",
                    return_value=[],
                ):
                    record = validate_document(source, manifest, cwd=root)

            self.assertFalse(record.passed)
            self.assertEqual(record.duplicate_page_labels, [1])
            self.assertEqual(record.out_of_range_page_labels, [4])
            self.assertEqual(record.uncovered_pages, [2, 3])

    def test_report_keeps_manual_review_pending_after_automatic_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.pdf"
            output = root / "sample.pdf.md"
            source.write_bytes(b"fake-pdf")
            output.write_text("## 第 1 页（OCR）\n正文\n", encoding="utf-8")
            manifest = {
                "source": "sample.pdf",
                "status": "ready",
                "parser": "paddleocr-vl-v1.6",
                "output": str(output),
                "warnings": [],
            }
            with patch("scripts.validate_ocr_batch._pdf_page_count", return_value=1):
                record = validate_document(source, manifest, cwd=root)

        batch_summary = summarize([record])
        report = render_markdown_report([record], batch_summary)

        self.assertEqual(batch_summary["automatic_passed"], 1)
        self.assertEqual(batch_summary["manual_review_pending"], 1)
        self.assertIn("自动通过", report)
        self.assertIn("不得将本报告中的“自动通过”等同于可发布", report)


if __name__ == "__main__":
    unittest.main()
