from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence

from app.model_protocols import ModelServiceError


class OpenAICompatibleError(ModelServiceError):
    """Raised when a vLLM-compatible OpenAI endpoint returns an error."""


class OpenAICompatibleClient:
    """Minimal client shared by CUDA vLLM and vLLM-Ascend deployments."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 600,
        *,
        enable_thinking: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.enable_thinking = enable_thinking

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        try:
            hostname = (urllib.parse.urlparse(url).hostname or "").lower()
            if hostname in {"127.0.0.1", "localhost", "::1"}:
                # Model APIs are local services in the production design.  Do
                # not let HTTP(S)_PROXY accidentally route loopback traffic.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                response_context = opener.open(request, timeout=self.timeout)
            else:
                response_context = urllib.request.urlopen(request, timeout=self.timeout)
            with response_context as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleError(
                f"OpenAI 兼容模型服务返回 HTTP {exc.code}: {detail[:2000]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OpenAICompatibleError(
                f"无法连接 OpenAI 兼容模型服务（{self.base_url}）。请确认 vLLM 服务已经启动。"
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OpenAICompatibleError("OpenAI 兼容模型服务返回了无效 JSON。") from exc
        if not isinstance(result, dict):
            raise OpenAICompatibleError("OpenAI 兼容模型服务返回格式不是 JSON 对象。")
        return result

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self._post(
            "/embeddings",
            {
                "model": model,
                "input": list(texts),
                "encoding_format": "float",
            },
        )
        data = result.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise OpenAICompatibleError("向量服务未返回预期数量的向量。")

        indexed: dict[int, list[float]] = {}
        for fallback_index, item in enumerate(data):
            if not isinstance(item, dict):
                raise OpenAICompatibleError("向量服务返回了无效的 data 项。")
            index = item.get("index", fallback_index)
            vector = item.get("embedding")
            if isinstance(index, bool) or not isinstance(index, int):
                raise OpenAICompatibleError("向量服务返回了无效的向量序号。")
            if not isinstance(vector, list) or not vector:
                raise OpenAICompatibleError("向量服务返回了空向量或无效向量。")
            if not all(isinstance(value, (int, float)) for value in vector):
                raise OpenAICompatibleError("向量服务返回了非数值向量。")
            indexed[index] = [float(value) for value in vector]

        expected = set(range(len(texts)))
        if set(indexed) != expected:
            raise OpenAICompatibleError("向量服务返回的向量序号不完整或重复。")
        return [indexed[index] for index in range(len(texts))]

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
        # num_ctx is enforced when vLLM starts via --max-model-len.  Keeping it
        # in the common interface preserves one RAG call path for both backends.
        del num_ctx
        result = self._post(
            "/chat/completions",
            {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "seed": seed,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {
                    "enable_thinking": self.enable_thinking,
                },
            },
        )
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAICompatibleError("生成服务没有返回 choices。")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OpenAICompatibleError("生成服务未返回有效回答。")
        return content.strip()
