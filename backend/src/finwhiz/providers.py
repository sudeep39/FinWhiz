"""LangChain generation, validated embeddings and Pinecone transport."""

import math
import ssl
import time
import threading
from collections import OrderedDict

import httpx
import truststore
from langchain_core.embeddings import Embeddings

from .config import AppError, compact, digest
from .documents import token_count
from .transport import JsonTransport
from .generation import GenerationClient
from .vector_store import PineconeClient
from .telemetry import span


class Providers(Embeddings):
    def __init__(self, settings, config, client=None):
        self.settings, self.config = settings, config
        self.client = client or httpx.Client(
            timeout=settings.generation_timeout_seconds,
            follow_redirects=False,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        self.transport = JsonTransport(self.client, settings)
        self.generation = GenerationClient(settings, config, self.client, self.base)
        self.index = PineconeClient(config, settings.pinecone_api_key, self.request)
        self.query_cache = OrderedDict()
        self.cache_lock = threading.Lock()

    def close(self):
        self.client.close()

    def embed_documents(self, texts):
        """LangChain Embeddings interface retains the validated document transport."""
        return self.embed(texts, purpose="document")

    def embed_query(self, text):
        return self.embed([text], purpose="query")[0]

    def request(self, url, body=None, headers=None, method="POST", attempts=None):
        return self.transport.request(url, body, headers, method, attempts)

    def vector(self, vector):
        if (
            not isinstance(vector, list)
            or len(vector) != self.config.dimension
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)
            or not any(vector)
        ):
            raise AppError("Embedding dimensions or values are invalid.")
        return vector

    def embed(self, texts, purpose="document"):
        if not texts:
            return []
        cache_key = texts[0] if purpose == "query" and len(texts) == 1 else None
        if cache_key is not None:
            with self.cache_lock:
                cached = self.query_cache.get(cache_key)
                if cached and time.monotonic() - cached[0] < 300:
                    self.query_cache.move_to_end(cache_key)
                    with span("query_embedding_cache_hit"):
                        return [list(cached[1])]
        key = self.settings.key(self.config)
        if not key:
            raise AppError(
                f"Configure {'NEBIUS_API_KEY' if self.config.provider == 'nebius' else 'OPENAI_API_KEY'} in server secrets to use {self.config.label}."
            )
        inputs = (
            [
                f"Instruct: Retrieve SEC filing evidence for the financial research question.\nQuery: {text}"
                for text in texts
            ]
            if self.config.provider == "nebius" and purpose == "query"
            else texts
        )
        result = self.request(
            self.base + "/embeddings",
            {
                "model": self.config.embeddingModel,
                "dimensions": self.config.dimension,
                "encoding_format": "float",
                "input": inputs,
            },
            {"Authorization": f"Bearer {key}"},
        )
        if result.get("model") != self.config.embeddingModel or len(result.get("data", [])) != len(
            texts
        ):
            raise AppError("Unexpected embedding model or response size.")
        rows = sorted(result["data"], key=lambda r: r["index"])
        if [r["index"] for r in rows] != list(range(len(texts))):
            raise AppError("Unexpected embedding response order.")
        vectors = [self.vector(row["embedding"]) for row in rows]
        if cache_key is not None:
            with self.cache_lock:
                self.query_cache[cache_key] = (time.monotonic(), tuple(vectors[0]))
                self.query_cache.move_to_end(cache_key)
                while len(self.query_cache) > 128:
                    self.query_cache.popitem(last=False)
        return vectors

    @property
    def base(self):
        return (
            "https://api.tokenfactory.nebius.com/v1"
            if self.config.provider == "nebius"
            else "https://api.openai.com/v1"
        )

    def structured(self, system, user, schema, model=None):
        return self.generation.structured(system, user, schema, model)

    def pinecone_host(self):
        return self.index.pinecone_host()

    def pinecone(self, path, body):
        return self.index.pinecone(path, body)

    def upsert(self, namespace, records):
        return self.index.upsert(namespace, records)


class ProviderPool:
    """Application-owned clients, isolated by the complete nonsecret configuration."""

    def __init__(self, settings, factory=Providers):
        self.settings, self.factory = settings, factory
        self.providers = {}
        self.lock = threading.Lock()

    def get(self, config):
        key = compact(config.model_dump())
        with self.lock:
            if key not in self.providers:
                self.providers[key] = self.factory(self.settings, config)
            return self.providers[key]

    def close(self):
        with self.lock:
            providers = list(self.providers.values())
            self.providers.clear()
        for provider in providers:
            provider.close()


def cached_embeddings(provider, store, texts):
    config = provider.config

    def key(text):
        return "embedding/" + digest(
            [config.fingerprint, config.embeddingModel, config.dimension, text]
        )

    found = {text: store.json(key(text)) for text in dict.fromkeys(texts)}
    missing = [text for text, vector in found.items() if vector is None]
    cursor = 0
    while cursor < len(missing):
        batch, count = [], 0
        while cursor < len(missing) and len(batch) < 32:
            text = missing[cursor]
            n = token_count(text)
            if n > 8191:
                raise AppError("An embedding input exceeds the token limit.")
            if batch and count + n > 12000:
                break
            batch.append(text)
            count += n
            cursor += 1
        vectors = provider.embed(batch)
        for text, vector in zip(batch, vectors):
            store.put_json(key(text), vector)
            found[text] = vector
    return [provider.vector(found[text]) for text in texts]
