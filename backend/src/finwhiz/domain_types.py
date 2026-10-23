"""Shared service contracts without a dependency on HTTP or graph execution."""

from typing import Protocol, TypedDict
from .config import AIConfig


class ChunkRecord(TypedDict):
    """Chunk dictionary exchanged by ingestion, CorpusCache and retrieval.

    A TypedDict annotation, not a runtime validator; metadata contains the
    text, filing scope and provenance needed to find the original parent."""
    id: str
    metadata: dict


class Evidence(TypedDict):
    """Research source dictionary with a citation ID such as S1.

    Produced by retrieval and read by agent tools, generation and validation.
    A TypedDict annotation only; research converts text into excerpt for UI."""
    id: str
    chunkId: str
    ticker: str
    report_date: str
    text: str


class RetrievalResult(TypedDict):
    """Core hybrid retrieval result consumed by the research graph.

    Sources feed generation; ranking feeds retrieval-only evaluation.
    Retrieval also attaches operational coverage/degradation metadata."""
    sources: list[Evidence]
    ranking: list[dict]
    candidate_count: int
    rerankFallback: bool
    rerankFallbackReason: str | None


class ResearchProvider(Protocol):
    """Structural interface required by retrieval and research.

    Providers is the production implementation; offline tests supply fakes.
    Implementations expose one immutable AIConfig and the three operations
    below; this protocol itself does not instantiate network clients."""
    config: AIConfig

    def embed(self, texts: list[str], purpose: str = "document") -> list[list[float]]:
        """Return vectors in input order for document or query text.

        Required by retrieval; implemented by Providers and offline provider fakes."""
        ...
    def pinecone(self, path: str, body: dict) -> dict:
        """Execute a vector operation and return its decoded response.

        Required by retrieval; implementations must preserve configured index
        isolation and enforce their transport/deadline policy."""
        ...
    def structured(self, system: str, user: dict, schema, model: str | None = None) -> dict:
        """Return a dict validated against the requested Pydantic schema.

        Required by research/reranking; production delegates to GenerationClient."""
        ...
