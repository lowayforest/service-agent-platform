from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validate_ocr_batch import (
    _match_manifest_record,
    extract_ocr_page_numbers,
    format_page_ranges,
    render_markdown_report,
    select_ocr_sources,
    summarize,
    validate_document,
)


class OCRValidationTests(unittest.TestCase):
    def test_selects_only_ready_ocr_sources_from_full_corpus_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan = root / "扫描资料" / "同名.pdf"
            native = root / "文本资料" / "同名.pdf"
            duplicate = root / "扫描资料" / "重复.pdf"
            for source in (scan, native, duplicate):
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"pdf")
            records = [
                {
                    "source": "扫描资料/同名.pdf",
                    "status": "ready",
                    "parser": "paddleocr-vl-v1.6",
                },
                {
                    "source": "文本资料/同名.pdf",
                    "status": "ready",
                    "parser": "pypdf",
                },
                {
                    "source": "扫描资料/重复.pdf",
                    "status": "duplicate_skipped",
                    "parser": "none",
                },
            ]

            self.assertEqual(_match_manifest_record(scan, records), records[0])
            self.assertEqual(_match_manifest_record(native, records), records[1])
            self.assertEqual(
                select_ocr_sources([scan, native, duplicate], records),
                [scan],
            )

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

    def test_full_builder_absolute_output_path_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "raw" / "sample.pdf"
            output = root / "processed" / "分组" / "sample.pdf.md"
            source.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            source.write_bytes(b"fake-pdf")
            output.write_text("## 第 1 页（OCR）\n正文\n", encoding="utf-8")
            manifest = {
                "source": "分组/sample.pdf",
                "status": "ready",
                "parser": "paddleocr-vl-v1.6",
                "output": "sample.pdf.md",
                "output_path": str(output),
                "text_characters": 2,
                "warnings": [],
            }

            with patch("scripts.validate_ocr_batch._pdf_page_count", return_value=1):
                record = validate_document(source, manifest, cwd=root)

            self.assertTrue(record.passed)
            self.assertEqual(record.ocr_pages, [1])
            self.assertEqual(record.output, "processed/分组/sample.pdf.md")

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
