from __future__ import annotations

from typing import Protocol, Sequence


class ModelServiceError(RuntimeError):
    """Raised when an external model service cannot complete a request."""


class EmbeddingClient(Protocol):
    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector for every input string."""


class ChatClient(Protocol):
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
        """Generate one non-streaming assistant response."""
