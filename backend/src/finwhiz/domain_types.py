"""Shared service contracts without a dependency on HTTP or graph execution."""

from typing import Protocol, TypedDict
from .config import AIConfig


class ChunkRecord(TypedDict):
    id: str
    metadata: dict


class Evidence(TypedDict):
    id: str
    chunkId: str
    ticker: str
    report_date: str
    text: str


class RetrievalResult(TypedDict):
    sources: list[Evidence]
    ranking: list[dict]
    candidate_count: int
    rerankFallback: bool
    rerankFallbackReason: str | None


class ResearchProvider(Protocol):
    config: AIConfig

    def embed(self, texts: list[str], purpose: str = "document") -> list[list[float]]: ...
    def pinecone(self, path: str, body: dict) -> dict: ...
    def structured(self, system: str, user: dict, schema, model: str | None = None) -> dict: ...
