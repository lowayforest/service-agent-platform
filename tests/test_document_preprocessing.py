from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from app.document_preprocessing import (
    DocumentAudit,
    LegacyDocConverter,
    audit_document,
    classify_pdf_page_lengths,
    discover_documents,
    extract_docx_image_parts,
    inspect_docx_images,
    output_path_for,
    parts_to_markdown,
    preprocess_document,
    sha256_file,
    summarize_audits,
)
from app.document_loader import DocumentPart
from app.ocr_backends import PaddleOCRTextBackend, PaddleOCRVLBackend, is_gpu_device


class FakeOCRBackend:
    name = "fake-ocr"

    def extract(self, path: Path) -> list[DocumentPart]:
        return [DocumentPart(str(path), "第 1 页（OCR）", "图片中的航道文字")]


class PDFClassificationTests(unittest.TestCase):
    def test_classifies_text_scan_and_mixed_pages(self) -> None:
        self.assertEqual(classify_pdf_page_lengths([100, 80], 30), "text")
        self.assertEqual(classify_pdf_page_lengths([0, 10], 30), "scan")
        self.assertEqual(classify_pdf_page_lengths([100, 0], 30), "mixed")


class AuditTests(unittest.TestCase):
    def test_detects_pdf_without_filename_extension(self) -> None:
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "无扩展名资料"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as stream:
                writer.write(stream)

            audit = audit_document(source, pdf_page_limit=0)

            self.assertEqual(audit.suffix, ".pdf")
            self.assertEqual(audit.classification, "scan")
            self.assertTrue(audit.supported)

    def test_small_png_icon_is_recorded_as_decorative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "含国徽资料.docx"
            png_header = (
                b"\x89PNG\r\n\x1a\n"
                + (13).to_bytes(4, "big")
                + b"IHDR"
                + (74).to_bytes(4, "big")
                + (74).to_bytes(4, "big")
                + b"\x08\x06\x00\x00\x00"
            )
            with ZipFile(source, "w") as archive:
                archive.writestr("word/media/image1.png", png_header)

            with patch(
                "app.document_preprocessing.VERIFIED_DECORATIVE_IMAGE_SHA256",
                {hashlib.sha256(png_header).hexdigest()},
            ):
                audit = audit_document(source)
                image_counts = inspect_docx_images(source)

            self.assertEqual(image_counts, (1, 0, 1))
            self.assertEqual(audit.embedded_images, 1)
            self.assertEqual(audit.ocr_candidate_images, 0)
            self.assertEqual(audit.decorative_images, 1)

    def test_discovers_files_and_ignores_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("航道资料", encoding="utf-8")
            (root / ".hidden.txt").write_text("hidden", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "b.doc").write_bytes(b"legacy")
            self.assertEqual(
                [path.name for path in discover_documents([root])],
                ["a.txt", "b.doc"],
            )

    def test_audits_text_legacy_and_unsupported_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = root / "a.txt"
            legacy = root / "b.doc"
            unknown = root / "c.bin"
            text.write_text("航道资料", encoding="utf-8")
            legacy.write_bytes(b"legacy")
            unknown.write_bytes(b"unknown")

            text_audit = audit_document(text, base=root)
            legacy_audit = audit_document(legacy, base=root)
            unknown_audit = audit_document(unknown, base=root)

            self.assertEqual(text_audit.classification, "native_text")
            self.assertEqual(legacy_audit.classification, "legacy_doc")
            self.assertEqual(unknown_audit.classification, "unsupported")
            self.assertEqual(text_audit.sha256, sha256_file(text))

            summary = summarize_audits([text_audit, legacy_audit, unknown_audit])
            self.assertEqual(summary["documents"], 3)
            self.assertEqual(summary["classifications"]["legacy_doc"], 1)

    def test_missing_file_is_reported_as_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = audit_document(root / "missing.pdf", base=root)

            self.assertEqual(audit.classification, "error")
            self.assertEqual(audit.size_bytes, 0)
            self.assertTrue(audit.error)


class PreprocessTests(unittest.TestCase):
    def test_legacy_converter_supports_macos_textutil(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "旧资料.doc"
            output = root / "converted"
            source.write_bytes(b"legacy")
            converter = LegacyDocConverter(executable="/usr/bin/textutil")

            def fake_run(command, **kwargs):
                Path(command[4]).write_bytes(b"converted")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch(
                "app.document_preprocessing.subprocess.run",
                side_effect=fake_run,
            ) as run:
                converted = converter.convert_to_docx(source, output)

            self.assertEqual(converter.name, "textutil")
            self.assertTrue(converted.exists())
            self.assertEqual(run.call_args.args[0][1:4], ["-convert", "docx", "-output"])

    def test_markdown_preserves_source_and_locator(self) -> None:
        markdown = parts_to_markdown(
            "资料/标准.docx",
            [DocumentPart("资料/标准.docx", "第一条", "航道标准正文")],
        )
        self.assertIn("原始来源：`资料/标准.docx`", markdown)
        self.assertIn("## 第一条", markdown)
        self.assertIn("航道标准正文", markdown)

    def test_preprocesses_text_and_writes_manifest_ready_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "资料.txt"
            output = root / "processed"
            source.write_text("第一条 航道资料", encoding="utf-8")

            result = preprocess_document(source, output, base=root)

            self.assertEqual(result.status, "ready")
            destination = output_path_for(source, output, root)
            self.assertTrue(destination.exists())
            self.assertIn("第一条 航道资料", destination.read_text(encoding="utf-8"))

    def test_marks_legacy_doc_when_converter_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "旧资料.doc"
            source.write_bytes(b"legacy")
            result = preprocess_document(
                source,
                root / "processed",
                base=root,
                converter=LegacyDocConverter(executable=""),
            )
            self.assertEqual(result.status, "needs_conversion")
            self.assertIsNone(result.output)

    def test_scan_without_ocr_is_reported_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.pdf"
            source.write_bytes(b"not-read-because-audit-is-provided")
            audit = DocumentAudit(
                source="scan.pdf",
                suffix=".pdf",
                size_bytes=source.stat().st_size,
                sha256=sha256_file(source),
                classification="scan",
                supported=True,
                page_count=1,
                sampled_pages=1,
                text_pages=0,
                low_text_pages=1,
                extracted_characters=0,
            )
            result = preprocess_document(
                source,
                root / "processed",
                base=root,
                audit=audit,
            )
            self.assertEqual(result.status, "needs_ocr")
            self.assertIsNone(result.output)

    def test_scan_with_ocr_is_written_as_ready_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.pdf"
            source.write_bytes(b"handled-by-fake-ocr")
            audit = DocumentAudit(
                source="scan.pdf",
                suffix=".pdf",
                size_bytes=source.stat().st_size,
                sha256=sha256_file(source),
                classification="scan",
                supported=True,
                page_count=1,
                sampled_pages=1,
                text_pages=0,
                low_text_pages=1,
                extracted_characters=0,
            )

            result = preprocess_document(
                source,
                root / "processed",
                base=root,
                audit=audit,
                ocr_backend=FakeOCRBackend(),
            )

            self.assertEqual(result.status, "ready")
            self.assertEqual(result.parser, "fake-ocr")
            self.assertIn("图片中的航道文字", Path(root / result.output).read_text())

    def test_extracts_embedded_docx_images_with_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "images.docx"
            with ZipFile(source, "w") as archive:
                archive.writestr("word/media/image1.png", b"fake-image")
                archive.writestr("word/media/image2.emf", b"unsupported")

            parts, warnings = extract_docx_image_parts(source, FakeOCRBackend())

            self.assertEqual(len(parts), 1)
            self.assertIn("内嵌图片 1", parts[0].locator)
            self.assertEqual(len(warnings), 1)
            self.assertIn(".emf", warnings[0])

    def test_corrupt_docx_is_reported_without_stopping_the_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "corrupt.docx"
            source.write_bytes(b"not-a-docx")

            result = preprocess_document(source, root / "processed", base=root)

            self.assertEqual(result.status, "error")
            self.assertIsNone(result.output)
            self.assertTrue(result.warnings)

    def test_result_can_be_serialized_as_json(self) -> None:
        audit = DocumentAudit(
            source="a.txt",
            suffix=".txt",
            size_bytes=1,
            sha256="abc",
            classification="native_text",
            supported=True,
        )
        self.assertEqual(json.loads(json.dumps(audit.as_dict()))["source"], "a.txt")


class PaddleOCRBackendTests(unittest.TestCase):
    def test_vl_requires_explicit_gpu_device(self) -> None:
        for device in ("cpu", "npu", "gpu:-1", "gpu:abc", "GPU:0"):
            with self.subTest(device=device), self.assertRaisesRegex(ValueError, "安全限制"):
                PaddleOCRVLBackend(device=device)
        self.assertTrue(is_gpu_device("gpu"))
        self.assertTrue(is_gpu_device("gpu:0"))

    def test_vl_refuses_cpu_only_paddle_before_loading_model(self) -> None:
        fake_paddle = SimpleNamespace(
            device=SimpleNamespace(is_compiled_with_cuda=lambda: False)
        )
        with patch.dict("sys.modules", {"paddle": fake_paddle}):
            with self.assertRaisesRegex(RuntimeError, "不是 CUDA GPU 版"):
                PaddleOCRVLBackend(device="gpu:0")._get_pipeline()

    def test_vl_refuses_missing_gpu_before_loading_model(self) -> None:
        fake_paddle = SimpleNamespace(
            device=SimpleNamespace(
                is_compiled_with_cuda=lambda: True,
                cuda=SimpleNamespace(device_count=lambda: 1),
            )
        )
        with patch.dict("sys.modules", {"paddle": fake_paddle}):
            with self.assertRaisesRegex(RuntimeError, "只检测到 1 张 GPU"):
                PaddleOCRVLBackend(device="gpu:1")._get_pipeline()

    def test_converts_general_ocr_results_to_document_parts(self) -> None:
        pipeline = SimpleNamespace(
            predict=lambda **_: [
                SimpleNamespace(json={"res": {"rec_texts": ["航道", "水深计划"]}}),
                SimpleNamespace(json={"res": {"rec_texts": []}}),
            ]
        )
        backend = PaddleOCRTextBackend(pipeline=pipeline)

        parts = backend.extract(Path("scan.pdf"))

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, "航道\n水深计划")

    def test_converts_pipeline_markdown_results_to_document_parts(self) -> None:
        pipeline = SimpleNamespace(
            predict=lambda **_: [
                SimpleNamespace(markdown={"markdown_texts": "# 第一页\n\n航道内容"}),
                SimpleNamespace(markdown={"markdown_texts": ""}),
                SimpleNamespace(markdown={"text": "第二页内容"}),
            ]
        )
        backend = PaddleOCRVLBackend(device="gpu:0", pipeline=pipeline)

        parts = backend.extract(Path("scan.pdf"))

        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].locator, "第 1 页（OCR）")
        self.assertEqual(parts[1].locator, "第 3 页（OCR）")

    def test_vl_uses_original_zero_based_page_index_when_results_skip_pages(self) -> None:
        results = [
            SimpleNamespace(markdown={"page_index": 0, "markdown_texts": "第一页"}),
            SimpleNamespace(markdown={"page_index": 4, "markdown_texts": "第五页"}),
        ]
        pipeline = SimpleNamespace(
            predict=lambda **_: results
        )
        backend = PaddleOCRVLBackend(device="gpu:0", pipeline=pipeline)

        parts = backend.extract(Path("scan.pdf"))

        self.assertEqual([part.locator for part in parts], [
            "第 1 页（OCR）",
            "第 5 页（OCR）",
        ])

    def test_text_backend_reads_page_index_from_serialized_result(self) -> None:
        pipeline = SimpleNamespace(
            predict=lambda **_: [
                SimpleNamespace(
                    json={
                        "res": {
                            "page_index": 6,
                            "rec_texts": ["第七页内容"],
                        }
                    }
                )
            ]
        )
        backend = PaddleOCRTextBackend(pipeline=pipeline)

        parts = backend.extract(Path("scan.pdf"))

        self.assertEqual(parts[0].locator, "第 7 页（OCR）")


class PreprocessCLITests(unittest.TestCase):
    def test_vl_cpu_is_rejected_before_document_discovery(self) -> None:
        from scripts import preprocess

        with patch("sys.argv", ["preprocess", "missing.pdf", "--ocr-backend", "paddleocr-vl"]):
            with patch.object(preprocess, "discover_documents") as discover:
                with self.assertRaisesRegex(SystemExit, "安全限制"):
                    preprocess.main()
                discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
