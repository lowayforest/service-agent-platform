from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from app.chunking import TextChunk, chunk_parts
from app.config import settings
from app.document_loader import iter_supported_files, load_document
from app.ollama_client import OllamaClient, OllamaError
from app.vector_store import VectorStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="解析文档并构建本地向量索引。")
    parser.add_argument("sources", nargs="+", type=Path, help="要导入的文件或目录")
    parser.add_argument("--chunk-size", type=int, default=900, help="分段字符数，默认 900")
    parser.add_argument("--overlap", type=int, default=120, help="相邻分段重叠字符数，默认 120")
    parser.add_argument("--batch-size", type=int, default=8, help="向量化批次大小，默认 8")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = iter_supported_files(args.sources)
    if not files:
        print("没有找到支持的文件。当前支持：md、txt、pdf、docx、xlsx。", file=sys.stderr)
        return 2

    chunks: List[TextChunk] = []
    failures = []
    for file_path in files:
        try:
            parts = load_document(file_path)
            file_chunks = chunk_parts(parts, args.chunk_size, args.overlap)
            chunks.extend(file_chunks)
            print(f"已解析 {file_path}: {len(file_chunks)} 个片段")
        except Exception as exc:  # 单个坏文件不应阻止其他资料入库
            failures.append((file_path, str(exc)))
            print(f"跳过 {file_path}: {exc}", file=sys.stderr)

    if not chunks:
        print("没有生成任何可索引文本。", file=sys.stderr)
        return 2

    client = OllamaClient(settings.ollama_base_url, settings.request_timeout)
    store = VectorStore(settings.index_path, settings.embedding_model, client)
    try:
        count = store.build(chunks, args.batch_size)
    except (OllamaError, ValueError) as exc:
        print(f"构建索引失败：{exc}", file=sys.stderr)
        return 1

    print(f"索引构建完成：{count} 个片段 -> {settings.index_path}")
    if failures:
        print(f"另有 {len(failures)} 个文件解析失败，请检查上方日志。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
