"""Regression cases for latency controls, pooling, caching and evidence checks."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from finwhiz.api import create_app
from finwhiz.config import AppError
from finwhiz.ingestion import ingest
from finwhiz.lexical import CorpusCache, LexicalIndex
from finwhiz.providers import Providers, ProviderPool
from finwhiz.research import research, validation_issues
from finwhiz.schemas import Answer, Ranking
from finwhiz.storage import Jobs, Storage
from finwhiz.telemetry import ResearchTrace, current_trace
from test_workflows import setup_job


def test_retry_respects_queue_capacity(settings):
    jobs = Jobs(Storage(settings.finwhiz_data_dir))
    jobs.add(dict(id="failed", status="failed", files=[]))
    for i in range(3):
        jobs.add(dict(id=str(i), status="queued", files=[]))
    with pytest.raises(AppError) as error:
        jobs.retry("failed")
    assert error.value.status == 429
    assert sum(j["status"] == "queued" for j in jobs.all()) == 3


def test_read_transaction_allows_writer(settings):
    jobs = Jobs(Storage(settings.finwhiz_data_dir))
    with jobs.connection(write=False) as reader:
        reader.execute("SELECT data FROM jobs").fetchall()
        with sqlite3.connect(jobs.path, timeout=0.1) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.rollback()


def test_pool_isolates_configs_and_closes_once(settings):
    created = []

    class Fake:
        closed = 0

        def __init__(self, settings, config):
            self.config = config
            created.append(self)

        def close(self):
            self.closed += 1

    pool = ProviderPool(settings, Fake)
    first = pool.get(settings.provider("openai"))
    assert pool.get(settings.provider("openai")) is first
    assert pool.get(settings.provider("nebius")) is not first
    pool.close()
    assert len(created) == 2 and all(p.closed == 1 for p in created)


def test_synchronized_host_discovery_and_query_cache(settings):
    settings = settings.model_copy(
        update={"openai_api_key": "offline", "pinecone_api_key": "offline"}
    )
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "/indexes/" in request.url.path:
            time.sleep(0.02)
            return httpx.Response(
                200,
                json=dict(
                    dimension=1536,
                    metric="cosine",
                    vector_type="dense",
                    tags={"embedding-model": "text-embedding-3-large"},
                    status={"ready": True},
                    host="fixture.pinecone.io",
                ),
            )
        if request.url.path.endswith("embeddings"):
            return httpx.Response(
                200,
                json=dict(
                    model="text-embedding-3-large", data=[dict(index=0, embedding=[1] + [0] * 1535)]
                ),
            )
        return httpx.Response(200, json={"matches": []})

    provider = Providers(
        settings, settings.provider(), httpx.Client(transport=httpx.MockTransport(handler))
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: provider.pinecone("query", {}), range(4)))
    assert sum("/indexes/" in call for call in calls) == 1
    first = provider.embed(["Revenue?"], purpose="query")
    first[0][0] = -99
    assert provider.embed(["Revenue?"], purpose="query")[0][0] == 1
    assert sum(call.endswith("embeddings") for call in calls) == 1
    provider.close()


def test_corpus_cache_invalidation_preserves_scoped_scores(settings):
    store = Storage(settings.finwhiz_data_dir)
    cache = CorpusCache()
    rows = [
        dict(id="a", metadata={"text": "revenue revenue margin"}),
        dict(id="b", metadata={"text": "cash flow"}),
    ]
    store.put_json("chunks/example", rows)
    first, stats = cache.load(store, "chunks/example")
    assert cache.load(store, "chunks/example")[0] is first
    assert LexicalIndex(first, stats).score("revenue") == LexicalIndex(rows).score("revenue")
    store.put_json("chunks/example", rows + [dict(id="c", metadata={"text": "revenue"})])
    assert len(cache.load(store, "chunks/example")[0]) == 3


def test_missing_narrative_value_is_rejected(settings):
    sources = [
        dict(id="S1", text="Revenue was 125 million."),
        dict(id="S2", text="Cash flow was 999 million."),
    ]
    answer = dict(
        status="answered",
        answer="Revenue was 999 million [S1].",
        citation_ids=["S1"],
        chart=dict(type="none", title="", unit="", points=[]),
        calculations=[],
    )
    assert validation_issues(answer, sources)
    answer["answer"] = "Revenue was 125 million [S1]."
    assert validation_issues(answer, sources) == []


def test_deadline_stops_outbound_calls(settings):
    calls = []
    provider = Providers(
        settings,
        settings.provider(),
        httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))),
    )
    context = ResearchTrace("expired", 0.01, started=time.monotonic() - 1)
    token = current_trace.set(context)
    try:
        with pytest.raises(AppError) as error:
            provider.request("https://offline.example/query")
        assert error.value.status == 504 and not calls
    finally:
        current_trace.reset(token)
        provider.close()


def test_stage_timings_and_context_budget_fallback(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    provider.settings = settings.model_copy(update={"rerank_max_tokens": 1})
    result = research(store, jobs.all(), provider, "What revenue was reported?", ["ACME"])
    assert result["metrics"]["rerankFallbackReason"] == "context_budget"
    assert "Ranking" not in provider.generated
    assert result["metrics"]["stageTimingsMs"]["bm25"] >= 0
    assert result["metrics"]["requestId"]


def test_api_timeout_returns_request_id_and_remains_responsive(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    original = provider.structured

    def slow(system, user, schema, model=None):
        if schema is Answer:
            time.sleep(0.2)
        return original(system, user, schema, model)

    provider.structured = slow
    limited = settings.model_copy(update={"chat_timeout_seconds": 0.05})
    with TestClient(
        create_app(limited, disable_workers=True, provider_factory=lambda *_: provider)
    ) as client:
        result = client.post(
            "/api/chat",
            headers={"Authorization": "Bearer baseline-password"},
            json=dict(question="What revenue was reported?", tickers=["ACME"]),
        )
        assert result.status_code == 504 and result.headers["x-request-id"]
        assert client.get("/api/health").status_code == 200


def test_sdk_timeouts_are_classified_and_retried(settings, monkeypatch):
    settings = settings.model_copy(update={"openai_api_key": "offline"})
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic", request=request)

    monkeypatch.setattr("finwhiz.generation.time.sleep", lambda _: None)
    provider = Providers(
        settings, settings.provider(), httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(AppError) as error:
        provider.structured("Rank", {}, Ranking)
    assert error.value.status == 504 and len(calls) == 2


def test_unsupported_number_triggers_one_verified_repair(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    original = provider.structured

    def unsupported(system, user, schema, model=None):
        result = original(system, user, schema, model)
        if schema is Answer:
            result["answer"] = "Revenue was 999 million [S1]."
        return result

    provider.structured = unsupported
    result = research(store, jobs.all(), provider, "What revenue was reported?", ["ACME"])
    assert result["status"] == "answered" and "999" not in result["answer"]
    assert provider.repair_calls == 1


def test_rerank_timeout_preserves_hybrid_evidence(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    original = provider.structured

    def timeout(system, user, schema, model=None):
        if schema is Ranking:
            raise AppError("Synthetic timeout", 504)
        return original(system, user, schema, model)

    provider.structured = timeout
    result = research(store, jobs.all(), provider, "What revenue was reported?", ["ACME"])
    assert result["status"] == "answered" and result["sources"]
    assert result["metrics"]["rerankFallbackReason"] == "timeout"


def test_answer_budget_fails_before_generation(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    provider.settings = settings.model_copy(update={"answer_max_tokens": 1})
    with pytest.raises(AppError) as error:
        research(store, jobs.all(), provider, "What revenue was reported?", ["ACME"], rerank=False)
    assert error.value.status == 413 and "Answer" not in provider.generated


def test_late_success_does_not_bypass_operation_budget(settings):
    limited = settings.model_copy(update={"vector_timeout_seconds": 0.01})

    def late(request):
        time.sleep(0.02)
        return httpx.Response(200, json={"ok": True})

    provider = Providers(
        limited, limited.provider(), httpx.Client(transport=httpx.MockTransport(late))
    )
    with pytest.raises(AppError) as error:
        provider.request("https://offline.example/query")
    assert error.value.status == 504
