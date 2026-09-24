from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.document_loader import DocumentPart
from app.document_preprocessing import DocumentAudit, sha256_file
from app.model_protocols import ModelServiceError
from scripts import build_knowledge_base


class FakeOCRBackend:
    name = "fake-ocr"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, path: Path) -> list[DocumentPart]:
        self.calls += 1
        return [DocumentPart(str(path), "第 1 页（OCR）", "扫描件中的航道文字")]


class FakeEmbeddingClient:
    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FailingEmbeddingClient:
    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ModelServiceError("模拟向量化失败")


class KnowledgeBuilderTests(unittest.TestCase):
    @staticmethod
    def run_main(arguments: list[str]) -> int:
        with redirect_stdout(io.StringIO()):
            return build_knowledge_base.main(arguments)

    def test_completed_ocr_output_can_be_reused_by_main_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scan.pdf.md"
            output.write_text("OCR 结果", encoding="utf-8")
            audit = DocumentAudit(
                source="scan.pdf",
                suffix=".pdf",
                size_bytes=8,
                sha256="same",
                classification="scan",
                supported=True,
            )
            record = {
                "pipeline_version": build_knowledge_base.PIPELINE_VERSION,
                "sha256": "same",
                "ocr_backend": "paddleocr",
                "result": {"status": "ready"},
                "output_path": str(output),
            }

            self.assertTrue(build_knowledge_base._can_resume_ready(record, audit, "none"))
            self.assertFalse(
                build_knowledge_base._can_resume_ready(record, audit, "paddleocr-vl")
            )

    def test_recovers_ocr_backend_from_parser_after_old_index_build(self) -> None:
        record = {
            "ocr_backend": "none",
            "result": {"parser": "paddleocr-vl-v1.6"},
        }

        self.assertEqual(
            build_knowledge_base._preserved_ocr_backend(record, "none"),
            "paddleocr-vl",
        )

    def test_checkpoint_writes_sorted_ocr_queue_and_duplicate_source_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifests = Path(directory)
            records = []
            for source, pages, checksum, status in (
                ("大扫描.pdf", 20, "same", "needs_ocr"),
                ("小扫描.pdf", 2, "other", "needs_ocr"),
                ("重复件.pdf", 20, "same", "partial_needs_ocr"),
            ):
                records.append(
                    {
                        "pipeline_version": build_knowledge_base.PIPELINE_VERSION,
                        "source": source,
                        "source_path": f"/raw/{source}",
                        "size_bytes": pages * 100,
                        "mtime_ns": 1,
                        "sha256": checksum,
                        "ocr_backend": "none",
                        "audit_options": {},
                        "updated_at": "now",
                        "audit": DocumentAudit(
                            source=source,
                            suffix=".pdf",
                            size_bytes=pages * 100,
                            sha256=checksum,
                            classification="scan",
                            supported=True,
                            page_count=pages,
                            sampled_pages=pages,
                            text_pages=0,
                            low_text_pages=pages,
                            extracted_characters=0,
                        ).as_dict(),
                        "result": {
                            "source": source,
                            "status": status,
                            "parser": "none",
                            "output": None,
                            "parts": 0,
                            "text_characters": 0,
                            "warnings": [],
                        },
                        "output_path": None,
                    }
                )

            build_knowledge_base._checkpoint(manifests, records)

            queue = [
                json.loads(line)
                for line in (manifests / "ocr-queue.jsonl").read_text().splitlines()
            ]
            duplicates = [
                json.loads(line)
                for line in (manifests / "duplicate-groups.jsonl").read_text().splitlines()
            ]
            self.assertEqual(queue[0]["source"], "小扫描.pdf")
            self.assertTrue(queue[0]["recommended_for_pilot"])
            self.assertEqual(duplicates[0]["count"], 2)
            self.assertEqual(duplicates[0]["canonical_source"], "大扫描.pdf")
            self.assertEqual(duplicates[0]["skipped_count"], 0)
            self.assertEqual(duplicates[0]["skipped_sources"], [])
            self.assertEqual(duplicates[0]["sources"], ["大扫描.pdf", "重复件.pdf"])

    def test_deduplicate_processes_one_copy_and_records_skipped_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            canonical = raw / "01-标准文件.pdf"
            duplicate = raw / "02-重复文件.pdf"
            canonical.write_bytes(b"identical-pdf")
            duplicate.write_bytes(b"identical-pdf")
            output = root / "processed"
            manifests = root / "manifests"
            backend = FakeOCRBackend()

            def fake_audit(path: Path, **_: object) -> DocumentAudit:
                return DocumentAudit(
                    source=path.name,
                    suffix=".pdf",
                    size_bytes=path.stat().st_size,
                    sha256=sha256_file(path),
                    classification="scan",
                    supported=True,
                    page_count=1,
                    sampled_pages=1,
                    text_pages=0,
                    low_text_pages=1,
                    extracted_characters=0,
                )

            with (
                patch.object(build_knowledge_base, "audit_document", side_effect=fake_audit),
                patch.object(
                    build_knowledge_base,
                    "PaddleOCRTextBackend",
                    return_value=backend,
                ),
            ):
                code = self.run_main(
                    [
                        str(raw),
                        "--output-dir",
                        str(output),
                        "--manifest-dir",
                        str(manifests),
                        "--ocr-backend",
                        "paddleocr",
                        "--deduplicate",
                        "--preprocess-only",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(backend.calls, 1)
            self.assertTrue((output / "01-标准文件.pdf.md").is_file())
            self.assertFalse((output / "02-重复文件.pdf.md").exists())

            summary = json.loads((manifests / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["statuses"], {"duplicate_skipped": 1, "ready": 1})
            self.assertTrue(summary["deduplication_enabled"])
            self.assertEqual(summary["unique_document_count"], 1)
            self.assertEqual(summary["duplicate_excess_count"], 1)
            self.assertEqual(summary["duplicate_skipped_count"], 1)
            self.assertEqual(summary["incomplete_documents"], [])

            duplicate_groups = [
                json.loads(line)
                for line in (manifests / "duplicate-groups.jsonl").read_text().splitlines()
            ]
            self.assertEqual(duplicate_groups[0]["canonical_source"], canonical.name)
            self.assertEqual(duplicate_groups[0]["skipped_sources"], [duplicate.name])

            results = [
                json.loads(line)
                for line in (manifests / "preprocess-results.jsonl").read_text().splitlines()
            ]
            skipped = next(row for row in results if row["status"] == "duplicate_skipped")
            self.assertEqual(skipped["duplicate_of"], canonical.name)

    def test_deduplicate_excludes_ready_duplicate_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            (raw / "01-标准文件.txt").write_text("第一条 航道资料", encoding="utf-8")
            (raw / "02-重复文件.txt").write_text("第一条 航道资料", encoding="utf-8")
            output = root / "processed"
            manifests = root / "manifests"
            index = root / "index.json"

            with patch.object(
                build_knowledge_base,
                "create_embedding_client",
                return_value=FakeEmbeddingClient(),
            ):
                code = self.run_main(
                    [
                        str(raw),
                        "--output-dir",
                        str(output),
                        "--manifest-dir",
                        str(manifests),
                        "--index-path",
                        str(index),
                        "--deduplicate",
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(index.read_text(encoding="utf-8"))
            self.assertTrue(payload["chunks"])
            self.assertEqual(
                {chunk["source"] for chunk in payload["chunks"]},
                {"01-标准文件.txt.md"},
            )
            summary = json.loads((manifests / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["indexed_documents"], 1)
            self.assertEqual(summary["duplicate_skipped_count"], 1)

    def test_index_is_backed_up_and_replaced_only_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "资料.md"
            document.write_text("# 第一条\n\n航道资料", encoding="utf-8")
            index = root / "index.json"
            index.write_text("old-index", encoding="utf-8")

            with patch.object(
                build_knowledge_base,
                "create_embedding_client",
                return_value=FakeEmbeddingClient(),
            ):
                chunks, backup = build_knowledge_base.build_index_atomically(
                    [document],
                    index,
                    chunk_size=900,
                    overlap=120,
                    embedding_batch_size=8,
                )

            self.assertEqual(chunks, 1)
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), "old-index")
            payload = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["chunks"]), 1)

    def test_failed_index_build_preserves_existing_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "资料.md"
            document.write_text("# 第一条\n\n航道资料", encoding="utf-8")
            index = root / "index.json"
            index.write_text("old-index", encoding="utf-8")

            with patch.object(
                build_knowledge_base,
                "create_embedding_client",
                return_value=FailingEmbeddingClient(),
            ):
                with self.assertRaisesRegex(ModelServiceError, "模拟向量化失败"):
                    build_knowledge_base.build_index_atomically(
                        [document],
                        index,
                        chunk_size=900,
                        overlap=120,
                        embedding_batch_size=8,
                    )

            self.assertEqual(index.read_text(encoding="utf-8"), "old-index")

    def test_preserves_relative_directories_and_resumes_ready_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            source = raw / "法规" / "资料.txt"
            source.parent.mkdir(parents=True)
            source.write_text("第一条 航道资料", encoding="utf-8")
            output = root / "processed"
            manifests = root / "manifests"
            arguments = [
                str(raw),
                "--output-dir",
                str(output),
                "--manifest-dir",
                str(manifests),
                "--preprocess-only",
            ]

            self.assertEqual(self.run_main(arguments), 0)
            destination = output / "法规" / "资料.txt.md"
            self.assertTrue(destination.is_file())

            self.assertEqual(self.run_main(arguments), 0)
            summary = json.loads((manifests / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["activity"]["resumed_ready"], 1)
            self.assertEqual(summary["statuses"], {"ready": 1})

    def test_scan_is_visible_and_cannot_replace_index_without_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            source = raw / "扫描件.pdf"
            source.write_bytes(b"fake-pdf")
            output = root / "processed"
            manifests = root / "manifests"
            index = root / "index.json"
            index.write_text("old-index", encoding="utf-8")
            audit = DocumentAudit(
                source="扫描件.pdf",
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

            with patch.object(build_knowledge_base, "audit_document", return_value=audit):
                code = self.run_main(
                    [
                        str(raw),
                        "--output-dir",
                        str(output),
                        "--manifest-dir",
                        str(manifests),
                        "--index-path",
                        str(index),
                    ]
                )

            self.assertEqual(code, 1)
            self.assertEqual(index.read_text(encoding="utf-8"), "old-index")
            summary = json.loads((manifests / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["statuses"], {"needs_ocr": 1})
            self.assertFalse(summary["index_replaced"])

    def test_scan_is_processed_when_ocr_backend_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            source = raw / "扫描件.pdf"
            source.write_bytes(b"fake-pdf")
            output = root / "processed"
            manifests = root / "manifests"
            audit = DocumentAudit(
                source="扫描件.pdf",
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
            backend = FakeOCRBackend()

            with (
                patch.object(build_knowledge_base, "audit_document", return_value=audit),
                patch.object(
                    build_knowledge_base,
                    "PaddleOCRTextBackend",
                    return_value=backend,
                ),
            ):
                code = self.run_main(
                    [
                        str(raw),
                        "--output-dir",
                        str(output),
                        "--manifest-dir",
                        str(manifests),
                        "--ocr-backend",
                        "paddleocr",
                        "--preprocess-only",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(backend.calls, 1)
            markdown = output / "扫描件.pdf.md"
            self.assertIn("扫描件中的航道文字", markdown.read_text(encoding="utf-8"))
            summary = json.loads((manifests / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["statuses"], {"ready": 1})

            reused_code = self.run_main(
                [
                    str(raw),
                    "--output-dir",
                    str(output),
                    "--manifest-dir",
                    str(manifests),
                    "--ocr-backend",
                    "none",
                    "--preprocess-only",
                ]
            )
            self.assertEqual(reused_code, 0)
            state = json.loads(
                (manifests / "build-state.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual(state["ocr_backend"], "paddleocr")
            self.assertEqual(state["result"]["parser"], "fake-ocr")


if __name__ == "__main__":
    unittest.main()
