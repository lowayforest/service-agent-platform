from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _boolean_env(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false、1/0、yes/no 或 on/off。")


_legacy_ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
_legacy_timeout = os.getenv("OLLAMA_TIMEOUT", "600")


@dataclass(frozen=True)
class Settings:
    chat_backend: str = os.getenv("CHAT_BACKEND", "ollama")
    chat_base_url: str = os.getenv("CHAT_BASE_URL", _legacy_ollama_base_url).rstrip("/")
    chat_api_key: str = os.getenv("CHAT_API_KEY", "")
    chat_model: str = os.getenv("CHAT_MODEL", "qwen3.5:9b")
    chat_enable_thinking: bool = _boolean_env("CHAT_ENABLE_THINKING")
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "ollama")
    embedding_base_url: str = os.getenv(
        "EMBEDDING_BASE_URL", _legacy_ollama_base_url
    ).rstrip("/")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    index_path: Path = Path(os.getenv("INDEX_PATH", "data/index.json"))
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "4"))
    rag_min_score: float = float(os.getenv("RAG_MIN_SCORE", "0.45"))
    num_ctx: int = int(os.getenv("RAG_NUM_CTX", "8192"))
    generation_temperature: float = float(os.getenv("RAG_TEMPERATURE", "0"))
    generation_seed: int = int(os.getenv("RAG_SEED", "42"))
    generation_max_tokens: int = int(os.getenv("RAG_MAX_TOKENS", "600"))
    request_timeout: float = float(os.getenv("MODEL_TIMEOUT", _legacy_timeout))

    @property
    def ollama_base_url(self) -> str:
        """Backward-compatible alias for older integrations."""
        return _legacy_ollama_base_url


settings = Settings()
