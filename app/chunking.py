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
                raw_id = f"{part.source}|{part.locator}|{sequence}|{text}"
                chunk_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
                chunks.append(TextChunk(chunk_id, part.source, part.locator, text))
                sequence += 1
            if end >= len(normalized):
                break
            start = max(end - overlap, start + 1)
    return chunks
