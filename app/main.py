from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.client_factory import create_chat_client, create_embedding_client
from app.config import settings
from app.model_protocols import ModelServiceError
from app.rag_service import RAGService
from app.vector_store import VectorStore


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: Optional[int] = Field(default=None, ge=1, le=10)


class Source(BaseModel):
    id: str
    chunk_id: str
    source: str
    locator: str
    score: float
    excerpt: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[Source]
    blocked_realtime: bool


chat_client = create_chat_client(settings)
embedding_client = create_embedding_client(settings)
store = VectorStore(
    settings.index_path,
    settings.embedding_model,
    embedding_client,
    embedding_backend=settings.embedding_backend,
)
service = RAGService(settings, chat_client, store)

app = FastAPI(
    title="航道对外服务智能体 API",
    description="基于本地千问和可追溯知识库的最小 RAG 服务。",
    version="0.1.0",
)


@app.get("/api/health")
def health() -> dict:
    store.reload_if_changed()
    return {
        "status": "ok",
        "chat_backend": settings.chat_backend,
        "chat_model": settings.chat_model,
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "indexed_chunks": store.chunk_count,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> dict:
    try:
        return await run_in_threadpool(service.answer, request.question.strip(), request.top_k)
    except ModelServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
