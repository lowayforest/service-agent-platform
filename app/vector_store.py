from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

from app.chunking import TextChunk
from app.model_protocols import EmbeddingClient


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    source: str
    locator: str
    text: str
    score: float


def normalize(vector: Sequence[float]) -> List[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        return [0.0 for _ in vector]
    return [value / length for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def lexical_tokens(text: str) -> Set[str]:
    lowered = text.lower()
    latin = set(re.findall(r"[a-z0-9][a-z0-9._/-]*", lowered))
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    bigrams = {"".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)}
    return latin | bigrams


def lexical_score(query: str, text: str) -> float:
    query_tokens = lexical_tokens(query)
    if not query_tokens:
        return 0.0
    text_tokens = lexical_tokens(text)
    return len(query_tokens & text_tokens) / len(query_tokens)


class VectorStore:
    def __init__(
        self,
        index_path: Path,
        embedding_model: str,
        client: EmbeddingClient,
        *,
        embedding_backend: str = "ollama",
    ) -> None:
        self.index_path = index_path
        self.embedding_model = embedding_model
        self.embedding_backend = embedding_backend.strip().lower()
        self.client = client
        self._chunks: List[Dict] = []
        self._mtime: Optional[float] = None
        self.reload()

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def reload(self) -> None:
        if not self.index_path.exists():
            self._chunks = []
            self._mtime = None
            return
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        model = payload.get("embedding_model")
        if model != self.embedding_model:
            raise ValueError(
                f"索引使用 {model!r}，当前配置使用 {self.embedding_model!r}。请重新构建索引或修改配置。"
            )
        stored_backend = payload.get("embedding_backend")
        if stored_backend is None and self.embedding_backend != "ollama":
            raise ValueError(
                "索引未记录向量后端，不能确认它是否与当前 OpenAI 兼容向量服务一致。"
                "请使用当前向量服务重新构建索引。"
            )
        if stored_backend is not None and stored_backend != self.embedding_backend:
            raise ValueError(
                f"索引使用向量后端 {stored_backend!r}，当前配置使用 "
                f"{self.embedding_backend!r}。请重新构建索引或修改配置。"
            )
        self._chunks = payload.get("chunks", [])
        self._mtime = self.index_path.stat().st_mtime

    def reload_if_changed(self) -> None:
        current_mtime = self.index_path.stat().st_mtime if self.index_path.exists() else None
        if current_mtime != self._mtime:
            self.reload()

    def build(self, chunks: Iterable[TextChunk], batch_size: int = 8) -> int:
        chunk_list = list(chunks)
        indexed: List[Dict] = []
        for start in range(0, len(chunk_list), batch_size):
            batch = chunk_list[start : start + batch_size]
            vectors = self.client.embed(self.embedding_model, [chunk.text for chunk in batch])
            for chunk, vector in zip(batch, vectors):
                item = chunk.as_dict()
                item["embedding"] = normalize(vector)
                indexed.append(item)

        payload = {
            "version": 2,
            "embedding_backend": self.embedding_backend,
            "embedding_model": self.embedding_model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "chunks": indexed,
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        os.replace(temporary_path, self.index_path)
        self.reload()
        return len(indexed)

    def search(self, query: str, top_k: int = 4) -> List[SearchResult]:
        self.reload_if_changed()
        if not self._chunks:
            return []
        query_vector = normalize(self.client.embed(self.embedding_model, [query])[0])
        results = []
        for item in self._chunks:
            semantic = max(0.0, cosine_similarity(query_vector, item["embedding"]))
            searchable_text = " ".join(
                (item.get("source", ""), item.get("locator", ""), item["text"])
            )
            lexical = lexical_score(query, searchable_text)
            score = semantic * 0.8 + lexical * 0.2
            results.append(
                SearchResult(
                    chunk_id=item["chunk_id"],
                    source=item["source"],
                    locator=item["locator"],
                    text=item["text"],
                    score=score,
                )
            )
        results.sort(key=lambda result: result.score, reverse=True)
        return results[: max(1, top_k)]
