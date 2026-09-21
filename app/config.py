from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    chat_model: str = os.getenv("CHAT_MODEL", "qwen3.5:9b")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    index_path: Path = Path(os.getenv("INDEX_PATH", "data/index.json"))
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "4"))
    rag_min_score: float = float(os.getenv("RAG_MIN_SCORE", "0.45"))
    num_ctx: int = int(os.getenv("RAG_NUM_CTX", "8192"))
    request_timeout: float = float(os.getenv("OLLAMA_TIMEOUT", "600"))


settings = Settings()
