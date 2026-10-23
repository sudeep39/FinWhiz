"""Validated Pinecone access with synchronized, expiring host discovery."""

import re
import threading
import time
from .config import AppError, compact
from .telemetry import span


class PineconeClient:
    """Validated vector-index adapter owned by Providers.index.

    Providers delegates host discovery, queries and upserts here. Ingestion
    writes vectors; retrieval queries them; maintenance uses explicit vector
    operations through the same facade."""
    def __init__(self, config, api_key, request):
        """Bind the index snapshot, credential and injected JSON request function.

        Called by Providers.__init__; sets up synchronized lazy host discovery
        without contacting Pinecone."""
        self.config, self.api_key, self.request = config, api_key, request
        self.host = None
        self.host_checked = 0
        self.lock = threading.Lock()

    def pinecone_host(self):
        """Return the validated data-plane host, refreshing after five minutes.

        Called by pinecone. A lock prevents simultaneous discovery calls; stale
        entries are cleared before _resolve_host rechecks the index."""
        with self.lock:
            if self.host and time.monotonic() - self.host_checked < 300:
                return self.host
            self.host = None
            host = self._resolve_host()
            self.host_checked = time.monotonic()
            return host

    def _resolve_host(self):
        """Discover and validate the configured index before storing its host.

        Internal helper called under pinecone_host's lock. Checks dimensions,
        cosine/dense type, embedding provenance, readiness and hostname; raises
        AppError rather than using an incompatible or unexpected endpoint."""
        if self.host:
            return self.host
        if not self.api_key:
            raise AppError("Add PINECONE_API_KEY to the server environment.")
        result = self.request(
            f"https://api.pinecone.io/indexes/{self.config.indexName}",
            headers={
                "Api-Key": self.api_key,
                "X-Pinecone-Api-Version": "2025-10",
            },
            method="GET",
        )
        if (
            result.get("dimension") != self.config.dimension
            or result.get("metric") != "cosine"
            or result.get("vector_type") != "dense"
        ):
            raise AppError(
                f"Pinecone index {self.config.indexName} must be dense, cosine, and {self.config.dimension} dimensions."
            )
        tags = result.get("tags", {})
        if (
            tags.get("embedding-model") != self.config.embeddingModel
            or tags.get("embedding-provider", self.config.provider) != self.config.provider
        ):
            raise AppError(
                "Pinecone index embedding provenance differs from the selected AI provider. Use its separate index."
            )
        if not result.get("status", {}).get("ready"):
            raise AppError("Pinecone index is still initializing.")
        if not re.fullmatch(r"[a-zA-Z0-9.-]+\.pinecone\.io", result.get("host", "")):
            raise AppError("Unexpected Pinecone data-plane host.")
        self.host = result["host"]
        return self.host

    def pinecone(self, path, body):
        """POST an operation body to the validated index host and return JSON.

        Called through Providers.pinecone. path selects query/fetch/stats or a
        write operation; credentials and API version headers are added here."""
        return self.request(
            f"https://{self.pinecone_host()}/{path}",
            body,
            {"Api-Key": self.api_key, "X-Pinecone-Api-Version": "2025-10"},
        )

    def upsert(self, namespace, records):
        """Write namespace records in bounded-size batches and verify counts.

        Called through Providers.upsert by ingestion. Rejects an oversized record
        or incomplete acknowledgement with AppError; partial earlier batches may
        already exist, so ingestion checkpoints/retries use stable vector IDs."""
        batch, size = [], 0

        def flush():
            if self.pinecone("vectors/upsert", {"namespace": namespace, "vectors": batch}).get(
                "upsertedCount"
            ) != len(batch):
                raise AppError("Pinecone did not acknowledge the full batch. Retry this job.")

        for row in records:
            length = len(compact(row).encode()) + 1
            if length > 1_700_000:
                raise AppError("A vector record exceeds the safe Pinecone request size.")
            if batch and size + length > 1_700_000:
                flush()
                batch, size = [], 0
            batch.append(row)
            size += length
        if batch:
            flush()
