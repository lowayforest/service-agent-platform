from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from app.document_preprocessing import (
    LegacyDocConverter,
    audit_document,
    discover_documents,
    preprocess_document,
    write_jsonl,
)
from app.ocr_backends import PaddleOCRTextBackend, PaddleOCRVLBackend


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将原始资料转换为可追溯的标准 Markdown。")
    parser.add_argument("sources", nargs="+", type=Path, help="要预处理的文件或目录")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed"),
        help="标准化文档输出目录",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifests/preprocess-results.jsonl"),
        help="处理结果台账",
    )
    parser.add_argument(
        "--pdf-page-limit",
        type=int,
        default=0,
        help="PDF 分类检查页数；0 表示全部页面，默认 0",
    )
    parser.add_argument(
        "--minimum-text-characters",
        type=int,
        default=30,
        help="页面低于多少字符时标记为疑似扫描页，默认 30",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在待 OCR、待转换、错误或空文件时返回非零状态",
    )
    parser.add_argument(
        "--ocr-backend",
        choices=("none", "paddleocr", "paddleocr-vl"),
        default="none",
        help="OCR 后端；paddleocr 适合本机轻量测试，paddleocr-vl 适合 GPU 结构化解析",
    )
    parser.add_argument(
        "--ocr-device",
        default="cpu",
        help="PaddleOCR 推理设备；轻量后端默认 cpu，PaddleOCR-VL 必须显式使用 gpu 或 gpu:0",
    )
    parser.add_argument(
        "--ocr-pipeline-version",
        choices=("v1", "v1.5", "v1.6"),
        default="v1.6",
        help="PaddleOCR-VL 流水线版本，默认 v1.6",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.pdf_page_limit < 0:
        raise SystemExit("--pdf-page-limit 不能小于 0")
    if args.minimum_text_characters < 1:
        raise SystemExit("--minimum-text-characters 必须大于 0")
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    ocr_backend = None
    if args.ocr_backend == "paddleocr-vl":
        try:
            ocr_backend = PaddleOCRVLBackend(
                pipeline_version=args.ocr_pipeline_version,
                device=args.ocr_device,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.ocr_backend == "paddleocr":
        ocr_backend = PaddleOCRTextBackend(device=args.ocr_device)

    files = discover_documents(args.sources)
    if not files:
        print("没有找到可预处理文件。")
        return 2

    converter = LegacyDocConverter()
    results = []
    for source in files:
        audit = audit_document(
            source,
            pdf_page_limit=args.pdf_page_limit,
            minimum_text_characters=args.minimum_text_characters,
        )
        result = preprocess_document(
            source,
            args.output_dir,
            audit=audit,
            converter=converter,
            ocr_backend=ocr_backend,
        )
        results.append(result)
        print(f"[{result.status}] {result.source}")

    write_jsonl(args.manifest, (result.as_dict() for result in results))
    counts = dict(sorted(Counter(result.status for result in results).items()))
    print(json.dumps({"documents": len(results), "statuses": counts}, ensure_ascii=False, indent=2))
    print(f"处理台账：{args.manifest}")

    incomplete = {"error", "needs_conversion", "needs_ocr", "partial_needs_ocr", "partial_needs_image_ocr", "empty", "unsupported"}
    return 1 if args.strict and any(result.status in incomplete for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
