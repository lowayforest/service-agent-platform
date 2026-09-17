from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from app.document_preprocessing import (
    audit_document,
    discover_documents,
    summarize_audits,
    write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计原始资料格式和 PDF 文本可提取性。")
    parser.add_argument("sources", nargs="+", type=Path, help="要审计的文件或目录")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/manifests/document-audit.jsonl"),
        help="JSONL 台账输出位置",
    )
    parser.add_argument(
        "--pdf-page-limit",
        type=int,
        default=5,
        help="每份 PDF 检查页数；0 表示检查全部页面，默认 5",
    )
    parser.add_argument(
        "--minimum-text-characters",
        type=int,
        default=30,
        help="页面低于多少字符时标记为疑似扫描页，默认 30",
    )
    parser.add_argument("--verbose", action="store_true", help="显示 PDF 解析器警告")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.pdf_page_limit < 0:
        raise SystemExit("--pdf-page-limit 不能小于 0")
    if args.minimum_text_characters < 1:
        raise SystemExit("--minimum-text-characters 必须大于 0")
    if not args.verbose:
        logging.getLogger("pypdf").setLevel(logging.ERROR)

    files = discover_documents(args.sources)
    if not files:
        print("没有找到可审计文件。")
        return 2

    records = [
        audit_document(
            path,
            pdf_page_limit=args.pdf_page_limit,
            minimum_text_characters=args.minimum_text_characters,
        )
        for path in files
    ]
    write_jsonl(args.output, (record.as_dict() for record in records))
    summary = summarize_audits(records)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"审计台账：{args.output}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
