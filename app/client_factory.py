from __future__ import annotations

from app.config import Settings
from app.model_protocols import ChatClient, EmbeddingClient
from app.ollama_client import OllamaClient
from app.openai_client import OpenAICompatibleClient


OPENAI_BACKENDS = {"openai", "vllm", "vllm-ascend"}


def _normalized_backend(value: str, setting_name: str) -> str:
    backend = value.strip().lower()
    if backend == "ollama" or backend in OPENAI_BACKENDS:
        return backend
    choices = "ollama、openai、vllm、vllm-ascend"
    raise ValueError(f"{setting_name}={value!r} 不受支持，可选值为：{choices}。")


def create_chat_client(settings: Settings) -> ChatClient:
    backend = _normalized_backend(settings.chat_backend, "CHAT_BACKEND")
    if backend == "ollama":
        return OllamaClient(settings.chat_base_url, settings.request_timeout)
    return OpenAICompatibleClient(
        settings.chat_base_url,
        settings.chat_api_key,
        settings.request_timeout,
        enable_thinking=settings.chat_enable_thinking,
    )


def create_embedding_client(settings: Settings) -> EmbeddingClient:
    backend = _normalized_backend(settings.embedding_backend, "EMBEDDING_BACKEND")
    if backend == "ollama":
        return OllamaClient(settings.embedding_base_url, settings.request_timeout)
    return OpenAICompatibleClient(
        settings.embedding_base_url,
        settings.embedding_api_key,
        settings.request_timeout,
    )
