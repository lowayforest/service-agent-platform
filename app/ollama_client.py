from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Sequence

from app.model_protocols import ModelServiceError


class OllamaError(ModelServiceError):
    """Raised when the local Ollama service cannot complete a request."""


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama 返回 HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OllamaError(
                f"无法连接 Ollama（{self.base_url}）。请确认服务已经启动。"
            ) from exc

    def embed(self, model: str, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        result = self._post(
            "/api/embed",
            {"model": model, "input": list(texts), "truncate": True},
        )
        embeddings = result.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise OllamaError("Ollama 未返回预期数量的向量。")
        return embeddings

    def chat(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        num_ctx: int,
        temperature: float = 0,
        seed: int = 42,
        max_tokens: int = 600,
    ) -> str:
        result = self._post(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "think": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "options": {
                    "num_ctx": num_ctx,
                    "temperature": temperature,
                    "seed": seed,
                    "num_predict": max_tokens,
                },
            },
        )
        message = result.get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("Ollama 未返回有效回答。")
        return content.strip()
