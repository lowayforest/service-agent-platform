from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.chunking import TextChunk
from app.client_factory import create_chat_client, create_embedding_client
from app.config import Settings
from app.ollama_client import OllamaClient
from app.openai_client import OpenAICompatibleClient, OpenAICompatibleError
from app.vector_store import VectorStore


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_chat_uses_vllm_openai_contract_and_disables_thinking(self) -> None:
        client = OpenAICompatibleClient(
            "http://127.0.0.1:8100/v1",
            "secret",
            enable_thinking=False,
        )
        response = {
            "choices": [{"message": {"role": "assistant", "content": "依据证据回答"}}]
        }

        with patch.object(client, "_post", return_value=response) as post:
            answer = client.chat("qwen3.5", "系统", "问题", 8192, 0, 42, 600)

        self.assertEqual(answer, "依据证据回答")
        path, payload = post.call_args.args
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(payload["model"], "qwen3.5")
        self.assertEqual(payload["max_tokens"], 600)
        self.assertEqual(
            payload["chat_template_kwargs"],
            {"enable_thinking": False},
        )

    def test_embeddings_are_reordered_by_openai_index(self) -> None:
        client = OpenAICompatibleClient("http://127.0.0.1:8101/v1")
        response = {
            "data": [
                {"index": 1, "embedding": [0, 1]},
                {"index": 0, "embedding": [1, 0]},
            ]
        }

        with patch.object(client, "_post", return_value=response) as post:
            vectors = client.embed("qwen3-embedding", ["甲", "乙"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(post.call_args.args[0], "/embeddings")

    def test_rejects_incomplete_embedding_indexes(self) -> None:
        client = OpenAICompatibleClient("http://127.0.0.1:8101/v1")
        response = {
            "data": [
                {"index": 0, "embedding": [1, 0]},
                {"index": 0, "embedding": [0, 1]},
            ]
        }

        with patch.object(client, "_post", return_value=response):
            with self.assertRaisesRegex(OpenAICompatibleError, "不完整或重复"):
                client.embed("qwen3-embedding", ["甲", "乙"])


class ClientFactoryTests(unittest.TestCase):
    def test_keeps_ollama_as_local_default(self) -> None:
        settings = Settings(
            chat_backend="ollama",
            embedding_backend="ollama",
        )

        self.assertIsInstance(create_chat_client(settings), OllamaClient)
        self.assertIsInstance(create_embedding_client(settings), OllamaClient)

    def test_openai_aliases_share_hardware_neutral_client(self) -> None:
        for backend in ("openai", "vllm", "vllm-ascend"):
            with self.subTest(backend=backend):
                settings = Settings(
                    chat_backend=backend,
                    embedding_backend=backend,
                    chat_base_url="http://127.0.0.1:8100/v1",
                    embedding_base_url="http://127.0.0.1:8101/v1",
                )
                self.assertIsInstance(
                    create_chat_client(settings), OpenAICompatibleClient
                )
                self.assertIsInstance(
                    create_embedding_client(settings), OpenAICompatibleClient
                )

    def test_rejects_unknown_backend_before_startup(self) -> None:
        settings = Settings(chat_backend="unknown")

        with self.assertRaisesRegex(ValueError, "CHAT_BACKEND"):
            create_chat_client(settings)


class VectorIndexProvenanceTests(unittest.TestCase):
    class FakeEmbeddingClient:
        def embed(self, model: str, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    def test_old_ollama_index_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.json"
            index.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "embedding_model": "legacy-model",
                        "chunks": [],
                    }
                ),
                encoding="utf-8",
            )

            store = VectorStore(
                index,
                "legacy-model",
                self.FakeEmbeddingClient(),
                embedding_backend="ollama",
            )

            self.assertEqual(store.chunk_count, 0)

    def test_new_index_records_embedding_backend_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.json"
            store = VectorStore(
                index,
                "qwen3-embedding",
                self.FakeEmbeddingClient(),
                embedding_backend="openai",
            )

            count = store.build(
                [TextChunk("chunk-1", "sample.md", "第 1 页", "航道资料")]
            )
            payload = json.loads(index.read_text(encoding="utf-8"))

            self.assertEqual(count, 1)
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["embedding_backend"], "openai")
            self.assertEqual(payload["embedding_model"], "qwen3-embedding")

    def test_old_index_is_not_reused_by_openai_embedding_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.json"
            index.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "embedding_model": "same-name",
                        "chunks": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "未记录向量后端"):
                VectorStore(
                    index,
                    "same-name",
                    self.FakeEmbeddingClient(),
                    embedding_backend="openai",
                )


if __name__ == "__main__":
    unittest.main()
