from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Iterable, List

from app.document_loader import DocumentPart


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    source: str
    locator: str
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


def _make_chunk(part: DocumentPart, sequence: int, text: str) -> TextChunk:
    raw_id = f"{part.source}|{part.locator}|{sequence}|{text}"
    chunk_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
    return TextChunk(chunk_id, part.source, part.locator, text)


def _chunk_tabular_part(
    part: DocumentPart, text: str, chunk_size: int
) -> List[TextChunk]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    table_indexes = [index for index, line in enumerate(lines) if "|" in line]
    if len(table_indexes) < 3 or len(table_indexes) / len(lines) < 0.6:
        return []

    header_index = table_indexes[0]
    prefix = "\n".join(lines[: header_index + 1])
    rows = lines[header_index + 1 :]
    if not rows:
        return []

    # 长目录按较短片段组织，避免目标行埋在 900 字片段尾部；每段重复表头，
    # 让“序号 61”一类精确问题在嵌入与关键词检索中都保留列语义。
    target_size = min(chunk_size, max(240, chunk_size // 2))
    groups: List[List[str]] = []
    current: List[str] = []
    for row in rows:
        candidate = "\n".join([prefix, *current, row])
        if current and len(candidate) > target_size:
            groups.append(current)
            current = [row]
        else:
            current.append(row)
    if current:
        groups.append(current)

    return [
        _make_chunk(part, sequence, "\n".join([prefix, *group]))
        for sequence, group in enumerate(groups, start=1)
    ]


def chunk_parts(
    parts: Iterable[DocumentPart], chunk_size: int = 900, overlap: int = 120
) -> List[TextChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0 且小于 chunk_size")

    chunks: List[TextChunk] = []
    for part in parts:
        normalized = re.sub(r"[ \t]+", " ", part.text)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
        if not normalized:
            continue
        tabular_chunks = _chunk_tabular_part(part, normalized, chunk_size)
        if tabular_chunks:
            chunks.extend(tabular_chunks)
            continue
        start = 0
        sequence = 1
        while start < len(normalized):
            end = min(start + chunk_size, len(normalized))
            if end < len(normalized):
                candidate = normalized[start:end]
                split_at = max(candidate.rfind("\n\n"), candidate.rfind("。"), candidate.rfind("；"))
                if split_at >= chunk_size // 2:
                    end = start + split_at + 1
            text = normalized[start:end].strip()
            if text:
                chunks.append(_make_chunk(part, sequence, text))
                sequence += 1
            if end >= len(normalized):
                break
            start = max(end - overlap, start + 1)
    return chunks
