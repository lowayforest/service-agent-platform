from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from app.chunking import TextChunk, chunk_parts
from app.client_factory import create_embedding_client
from app.config import settings
from app.document_loader import iter_supported_files, load_document
from app.document_preprocessing import (
    DocumentAudit,
    LegacyDocConverter,
    audit_document,
    discover_documents,
    display_path,
    output_path_for,
    preprocess_document,
    write_jsonl,
)
from app.ocr_backends import PaddleOCRTextBackend, PaddleOCRVLBackend
from app.model_protocols import ModelServiceError
from app.vector_store import VectorStore


PIPELINE_VERSION = 3
COMPLETED_STATUSES = {
    "duplicate_skipped",
    "ready",
}
PARTIAL_INDEXABLE_STATUSES = {
    "partial_needs_image_ocr",
    "partial_needs_ocr",
}
OCR_QUEUE_STATUSES = {
    "needs_ocr",
    "partial_needs_image_ocr",
    "partial_needs_ocr",
}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="可续跑地审计、预处理全部资料，并在验收通过后原子构建知识库索引。"
    )
    parser.add_argument("sources", nargs="+", type=Path, help="原始资料文件或目录")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/full-build"),
        help="标准 Markdown 输出目录",
    )
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=Path("data/manifests/full-build"),
        help="审计、预处理、断点和汇总台账目录",
    )
    parser.add_argument(
        "--ocr-backend",
        choices=("none", "paddleocr", "paddleocr-vl"),
        default="none",
        help="none 仅检测分流；paddleocr 或 paddleocr-vl 会实际处理扫描件和 DOCX 图片",
    )
    parser.add_argument(
        "--ocr-device",
        default="cpu",
        help="轻量 OCR 默认 cpu；PaddleOCR-VL 必须显式使用 gpu 或 gpu:0",
    )
    parser.add_argument(
        "--ocr-pipeline-version",
        choices=("v1", "v1.5", "v1.6"),
        default="v1.6",
        help="PaddleOCR-VL 流水线版本",
    )
    parser.add_argument(
        "--pdf-page-limit",
        type=int,
        default=0,
        help="PDF 审计页数；0 表示检查全部页面",
    )
    parser.add_argument(
        "--minimum-text-characters",
        type=int,
        default=30,
        help="单页低于多少字符时视为疑似扫描页",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="忽略已有断点并重新处理；用于更换解析策略后强制重跑",
    )
    parser.add_argument(
        "--preprocess-only",
        action="store_true",
        help="只生成标准文件和台账，不构建或替换索引",
    )
    parser.add_argument(
        "--deduplicate",
        action="store_true",
        help=(
            "按 SHA-256 跳过内容完全相同的副本；保留排序后的第一份作为标准来源，"
            "不删除或修改原始文件"
        ),
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="允许任务存在未完成文件；显式构建时仅纳入已有可用输出",
    )
    parser.add_argument(
        "--include-processed",
        action="append",
        type=Path,
        default=[],
        help="额外纳入索引的已验收 Markdown 文件或目录，可重复使用",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=settings.index_path,
        help="最终索引位置，默认使用 INDEX_PATH",
    )
    parser.add_argument("--chunk-size", type=int, default=900, help="普通分段字符数")
    parser.add_argument("--overlap", type=int, default=120, help="相邻分段重叠字符数")
    parser.add_argument("--embedding-batch-size", type=int, default=8, help="向量化批次大小")
    return parser.parse_args(argv)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"断点文件 {path} 第 {line_number} 行不是有效 JSON。") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _source_root(inputs: Iterable[Path]) -> Path:
    roots = []
    for item in inputs:
        resolved = item.expanduser().resolve()
        roots.append(resolved if resolved.is_dir() else resolved.parent)
    return Path(os.path.commonpath([str(root) for root in roots]))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _make_ocr_backend(args: argparse.Namespace) -> Any:
    if args.ocr_backend == "paddleocr-vl":
        return PaddleOCRVLBackend(
            pipeline_version=args.ocr_pipeline_version,
            device=args.ocr_device,
        )
    if args.ocr_backend == "paddleocr":
        return PaddleOCRTextBackend(device=args.ocr_device)
    return None


def _audit_from_record(record: Dict[str, Any]) -> Optional[DocumentAudit]:
    payload = record.get("audit")
    if not isinstance(payload, dict):
        return None
    try:
        return DocumentAudit(**payload)
    except TypeError:
        return None


def _can_reuse_audit(
    record: Optional[Dict[str, Any]],
    source: Path,
    *,
    pdf_page_limit: int,
    minimum_text_characters: int,
) -> bool:
    if not record:
        return False
    stat = source.stat()
    return (
        record.get("pipeline_version") == PIPELINE_VERSION
        and record.get("size_bytes") == stat.st_size
        and record.get("mtime_ns") == stat.st_mtime_ns
        and record.get("audit_options")
        == {
            "pdf_page_limit": pdf_page_limit,
            "minimum_text_characters": minimum_text_characters,
        }
        and _audit_from_record(record) is not None
    )


def _can_resume_ready(
    record: Optional[Dict[str, Any]],
    audit: DocumentAudit,
    ocr_backend: str,
) -> bool:
    if (
        not record
        or record.get("pipeline_version") != PIPELINE_VERSION
        or record.get("sha256") != audit.sha256
    ):
        return False
    needs_image_processing = audit.classification in {"scan", "mixed"} or bool(
        audit.embedded_images
    )
    if (
        needs_image_processing
        and ocr_backend != "none"
        and record.get("ocr_backend") != ocr_backend
    ):
        return False
    result = record.get("result")
    output_path = record.get("output_path")
    return bool(
        isinstance(result, dict)
        and result.get("status") == "ready"
        and isinstance(output_path, str)
        and Path(output_path).is_file()
    )


def _checkpoint(manifest_dir: Path, records: Sequence[Dict[str, Any]]) -> None:
    write_jsonl(manifest_dir / "build-state.jsonl", records)
    write_jsonl(
        manifest_dir / "document-audit.jsonl",
        (record["audit"] for record in records),
    )
    write_jsonl(
        manifest_dir / "preprocess-results.jsonl",
        (
            {
                **record["result"],
                "sha256": record["sha256"],
                "classification": record["audit"]["classification"],
                "output_path": record.get("output_path"),
                "ocr_backend": record["ocr_backend"],
                "duplicate_of": record.get("duplicate_of"),
            }
            for record in records
        ),
    )
    write_jsonl(manifest_dir / "ocr-queue.jsonl", _ocr_queue_rows(records))
    write_jsonl(manifest_dir / "duplicate-groups.jsonl", _duplicate_groups(records))


def _ocr_queue_rows(records: Sequence[Dict[str, Any]]) -> list[Dict[str, Any]]:
    rows = []
    for record in records:
        result = record.get("result", {})
        status = result.get("status")
        if status not in OCR_QUEUE_STATUSES:
            continue
        audit = record.get("audit", {})
        page_count = audit.get("page_count")
        rows.append(
            {
                "source": record.get("source"),
                "source_path": record.get("source_path"),
                "status": status,
                "classification": audit.get("classification"),
                "page_count": page_count,
                "text_pages": audit.get("text_pages"),
                "low_text_pages": audit.get("low_text_pages"),
                "size_bytes": record.get("size_bytes"),
                "sha256": record.get("sha256"),
                "recommended_for_pilot": isinstance(page_count, int)
                and 1 <= page_count <= 3,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            not row["recommended_for_pilot"],
            row["page_count"] if isinstance(row["page_count"], int) else float("inf"),
            row["size_bytes"] if isinstance(row["size_bytes"], int) else float("inf"),
            row["source"] or "",
        ),
    )


def _duplicate_groups(records: Sequence[Dict[str, Any]]) -> list[Dict[str, Any]]:
    records_by_checksum: Dict[str, list[Dict[str, Any]]] = {}
    for record in records:
        checksum = record.get("sha256")
        source = record.get("source")
        if checksum and source:
            records_by_checksum.setdefault(checksum, []).append(record)

    groups = []
    for checksum, group_records in sorted(records_by_checksum.items()):
        if len(group_records) <= 1:
            continue
        ordered = sorted(group_records, key=lambda record: str(record["source"]))
        sources = [str(record["source"]) for record in ordered]
        skipped_sources = [
            str(record["source"])
            for record in ordered
            if record.get("result", {}).get("status") == "duplicate_skipped"
        ]
        canonical_sources = [
            str(record["source"])
            for record in ordered
            if record.get("result", {}).get("status") != "duplicate_skipped"
        ]
        groups.append(
            {
                "sha256": checksum,
                "count": len(sources),
                "canonical_source": (
                    canonical_sources[0] if canonical_sources else sources[0]
                ),
                "skipped_count": len(skipped_sources),
                "skipped_sources": skipped_sources,
                "sources": sources,
            }
        )
    return groups


def _duplicate_result(source: str, canonical_source: str) -> Dict[str, Any]:
    return {
        "source": source,
        "status": "duplicate_skipped",
        "parser": "none",
        "output": None,
        "parts": 0,
        "text_characters": 0,
        "warnings": [
            f"内容与 {canonical_source} 的 SHA-256 完全相同；仅跳过处理，原始文件保持不变。"
        ],
    }


def preprocess_all(
    args: argparse.Namespace,
    files: Sequence[Path],
    source_root: Path,
    ocr_backend: Any,
) -> tuple[list[Dict[str, Any]], Dict[str, int]]:
    state_path = args.manifest_dir / "build-state.jsonl"
    previous_rows = [] if args.no_resume else _read_jsonl(state_path)
    previous = {
        row["source"]: row
        for row in previous_rows
        if isinstance(row.get("source"), str)
    }
    converter = LegacyDocConverter()
    records: list[Dict[str, Any]] = []
    counters = Counter()
    canonical_by_checksum: Dict[str, str] = {}

    for number, source in enumerate(files, start=1):
        source_name = display_path(source, source_root)
        old = previous.get(source_name)
        if _can_reuse_audit(
            old,
            source,
            pdf_page_limit=args.pdf_page_limit,
            minimum_text_characters=args.minimum_text_characters,
        ):
            audit = _audit_from_record(old)
            assert audit is not None
            counters["reused_audits"] += 1
        else:
            audit = audit_document(
                source,
                base=source_root,
                pdf_page_limit=args.pdf_page_limit,
                minimum_text_characters=args.minimum_text_characters,
            )
            counters["audited"] += 1

        canonical_source = (
            canonical_by_checksum.get(audit.sha256) if audit.sha256 else None
        )
        duplicate_of = canonical_source if args.deduplicate and canonical_source else None
        if args.deduplicate and audit.sha256 and canonical_source is None:
            canonical_by_checksum[audit.sha256] = source_name

        if duplicate_of:
            result_payload = _duplicate_result(source_name, duplicate_of)
            output_path = None
            counters["duplicates_skipped"] += 1
            action = "duplicate_skipped"
        elif _can_resume_ready(old, audit, args.ocr_backend):
            result_payload = old["result"]
            output_path = old["output_path"]
            counters["resumed_ready"] += 1
            action = "resume"
        else:
            result = preprocess_document(
                source,
                args.output_dir,
                base=source_root,
                audit=audit,
                converter=converter,
                ocr_backend=ocr_backend,
            )
            result_payload = result.as_dict()
            destination = output_path_for(source, args.output_dir, source_root)
            output_path = str(destination.resolve()) if result.output else None
            counters["processed"] += 1
            action = result.status

        stat = source.stat()
        record = {
            "pipeline_version": PIPELINE_VERSION,
            "source": source_name,
            "source_path": str(source.resolve()),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": audit.sha256,
            "ocr_backend": args.ocr_backend,
            "audit_options": {
                "pdf_page_limit": args.pdf_page_limit,
                "minimum_text_characters": args.minimum_text_characters,
            },
            "updated_at": _utc_now(),
            "audit": audit.as_dict(),
            "result": result_payload,
            "output_path": output_path,
            "duplicate_of": duplicate_of,
        }
        records.append(record)
        _checkpoint(args.manifest_dir, records)
        print(f"[{number}/{len(files)}] [{action}] {source_name}")

    return records, dict(counters)


def _indexable_outputs(
    records: Sequence[Dict[str, Any]], allow_incomplete: bool
) -> list[Path]:
    accepted = {"ready"}
    if allow_incomplete:
        accepted.update(PARTIAL_INDEXABLE_STATUSES)
    outputs = []
    for record in records:
        result = record["result"]
        output_path = record.get("output_path")
        if result.get("status") in accepted and output_path and Path(output_path).is_file():
            outputs.append(Path(output_path))
    return outputs


def _collect_index_files(
    outputs: Sequence[Path], include_processed: Sequence[Path]
) -> list[Path]:
    candidates = list(outputs)
    if include_processed:
        candidates.extend(iter_supported_files(include_processed))
    unique = {
        path.resolve(): path
        for path in candidates
        if path.suffix.lower() in {".md", ".txt"}
    }
    return [unique[key] for key in sorted(unique, key=str)]


def build_index_atomically(
    files: Sequence[Path],
    index_path: Path,
    *,
    chunk_size: int,
    overlap: int,
    embedding_batch_size: int,
) -> tuple[int, Optional[Path]]:
    chunks: list[TextChunk] = []
    failures = []
    for path in files:
        try:
            chunks.extend(chunk_parts(load_document(path), chunk_size, overlap))
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        raise RuntimeError("索引输入解析失败：\n" + "\n".join(failures))
    if not chunks:
        raise RuntimeError("没有生成可索引片段，正式索引保持不变。")

    index_path = index_path.expanduser()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = index_path.with_name(f".{index_path.name}.candidate-{os.getpid()}")
    backup: Optional[Path] = None
    client = create_embedding_client(settings)
    try:
        store = VectorStore(
            candidate,
            settings.embedding_model,
            client,
            embedding_backend=settings.embedding_backend,
        )
        count = store.build(chunks, embedding_batch_size)
        if index_path.exists():
            backup_dir = index_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = backup_dir / f"{index_path.stem}-{stamp}{index_path.suffix}"
            shutil.copy2(index_path, backup)
        os.replace(candidate, index_path)
        return count, backup
    finally:
        candidate.unlink(missing_ok=True)


def _validate_args(args: argparse.Namespace) -> None:
    missing_sources = [str(path) for path in args.sources if not path.expanduser().exists()]
    if missing_sources:
        raise ValueError("原始资料路径不存在：" + "、".join(missing_sources))
    missing_includes = [
        str(path) for path in args.include_processed if not path.expanduser().exists()
    ]
    if missing_includes:
        raise ValueError("附加处理结果路径不存在：" + "、".join(missing_includes))
    if args.pdf_page_limit < 0:
        raise ValueError("--pdf-page-limit 不能小于 0")
    if args.minimum_text_characters < 1:
        raise ValueError("--minimum-text-characters 必须大于 0")
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size 必须大于 0")
    if args.overlap < 0 or args.overlap >= args.chunk_size:
        raise ValueError("--overlap 必须大于等于 0 且小于 --chunk-size")
    if args.embedding_batch_size < 1:
        raise ValueError("--embedding-batch-size 必须大于 0")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        _validate_args(args)
        ocr_backend = _make_ocr_backend(args)
    except ValueError as exc:
        print(str(exc))
        return 2

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    files = discover_documents(args.sources)
    if not files:
        print("没有找到可处理文件。")
        return 2

    source_root = _source_root(args.sources)
    for directory in (args.output_dir, args.manifest_dir):
        if any(
            source.expanduser().resolve().is_dir()
            and _is_within(directory, source.expanduser().resolve())
            for source in args.sources
        ):
            print(f"输出目录不能位于原始资料目录内部：{directory}")
            return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.manifest_dir.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    try:
        records, activity = preprocess_all(args, files, source_root, ocr_backend)
    except ValueError as exc:
        print(f"无法读取断点台账：{exc}")
        return 2
    statuses = Counter(record["result"]["status"] for record in records)
    classifications = Counter(record["audit"]["classification"] for record in records)
    incomplete = [
        record["source"]
        for record in records
        if record["result"]["status"] not in COMPLETED_STATUSES
    ]
    ocr_queue = _ocr_queue_rows(records)
    duplicate_groups = _duplicate_groups(records)
    duplicate_checksums = [group["sha256"] for group in duplicate_groups]

    summary: Dict[str, Any] = {
        "started_at": started_at,
        "finished_at": _utc_now(),
        "source_root": str(source_root),
        "documents": len(records),
        "classifications": dict(sorted(classifications.items())),
        "statuses": dict(sorted(statuses.items())),
        "activity": activity,
        "incomplete_documents": incomplete,
        "deduplication_enabled": args.deduplicate,
        "unique_document_count": len(records)
        - sum(int(group["count"]) - 1 for group in duplicate_groups),
        "ocr_queue_count": len(ocr_queue),
        "ocr_pilot_candidates": sum(
            bool(record["recommended_for_pilot"]) for record in ocr_queue
        ),
        "duplicate_checksums": duplicate_checksums,
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_document_count": sum(
            int(group["count"]) for group in duplicate_groups
        ),
        "duplicate_excess_count": sum(
            int(group["count"]) - 1 for group in duplicate_groups
        ),
        "duplicate_skipped_count": sum(
            int(group["skipped_count"]) for group in duplicate_groups
        ),
        "ocr_backend": args.ocr_backend,
        "index_replaced": False,
    }

    if args.deduplicate and summary["duplicate_skipped_count"]:
        print(
            f"已按 SHA-256 跳过 {summary['duplicate_skipped_count']} 份重复副本；"
            "原始文件保持不变。"
        )

    if incomplete and not args.allow_incomplete:
        print(
            f"发现 {len(incomplete)} 份未完成资料，正式索引保持不变。"
            "请查看 preprocess-results.jsonl，处理后使用同一命令续跑。"
        )
    elif not args.preprocess_only:
        outputs = _indexable_outputs(records, args.allow_incomplete)
        index_files = _collect_index_files(outputs, args.include_processed)
        try:
            chunk_count, backup = build_index_atomically(
                index_files,
                args.index_path,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
                embedding_batch_size=args.embedding_batch_size,
            )
        except (ModelServiceError, RuntimeError, ValueError) as exc:
            summary["index_error"] = str(exc)
            _write_json(args.manifest_dir / "summary.json", summary)
            print(f"构建索引失败，原索引保持不变：{exc}")
            return 1
        summary.update(
            {
                "index_replaced": True,
                "index_path": str(args.index_path),
                "indexed_documents": len(index_files),
                "indexed_chunks": chunk_count,
                "index_backup": str(backup) if backup else None,
            }
        )

    _write_json(args.manifest_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"处理汇总：{args.manifest_dir / 'summary.json'}")
    return 1 if incomplete and not args.allow_incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
