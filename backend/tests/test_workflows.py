import json
import math

import httpx
import pytest

from finwhiz.config import AppError
from finwhiz.finance import cash_flow_answer, empty_chart
from finwhiz.ingestion import ingest
from finwhiz.providers import Providers
from finwhiz.research import research
from finwhiz.schemas import Answer, Ranking, Repair
from finwhiz.storage import Jobs, Storage, now


class FixtureProvider:
    def __init__(self, config):
        self.config = config
        self.records = {}
        self.embedding_calls = 0
        self.generated = []
        self.invalid = False
        self.invalid_chart = False
        self.repair_calls = 0

    def pinecone_host(self):
        return "fixture.pinecone.io"

    def vector(self, vector):
        return vector

    def embed(self, texts, purpose="document"):
        self.embedding_calls += len(texts)
        return [[1.0] + [0.0] * (self.config.dimension - 1) for _ in texts]

    def upsert(self, namespace, records):
        self.records.setdefault(namespace, {}).update({r["id"]: r for r in records})

    def pinecone(self, path, body):
        if path == "describe_index_stats":
            return {
                "namespaces": {
                    key: {"vectorCount": len(rows)} for key, rows in self.records.items()
                }
            }
        assert path == "query"
        rows = self.records.get(body["namespace"], {}).values()
        return {
            "matches": [
                {"id": r["id"], "score": 0.9}
                for r in rows
                if all(r["metadata"][key] == value["$eq"] for key, value in body["filter"].items())
            ]
        }

    def structured(self, system, user, schema, model=None):
        self.generated.append(schema.__name__)
        if schema is Ranking:
            return {"ids": [r["id"] for r in user["candidates"]]}
        if schema is Repair:
            self.repair_calls += 1
            return dict(
                sentences=[dict(text="Revenue was 125 million.", source_ids=["S1"])],
                calculations=[],
            )
        assert schema is Answer
        answer = dict(
            status="answered",
            answer=(
                "Revenue was 125 million [S99]."
                if self.invalid
                else "Revenue was 125 million [S1]."
            ),
            citation_ids=["S99"] if self.invalid else ["S1"],
            chart=empty_chart(),
            calculations=[],
        )
        if self.invalid_chart:
            answer["chart"] = dict(
                type="bar",
                title="Revenue",
                unit="USD millions",
                points=[dict(label="Revenue", value=999, source_ids=["S1"])],
            )
        return answer

    def close(self):
        pass


def setup_job(settings):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    html = "<html><body><p>Ticker: ACME 10-Q Fiscal period end: 2025-03-31. Revenue was 125 million and cost was 100 million. This financial context is readable and relates to the reported fiscal quarter.</p></body></html>"
    store.put("upload/file", html)
    job = dict(
        id="job",
        status="processing",
        createdAt=now(),
        aiConfig=settings.provider().model_dump(),
        strategies=["fixed", "semantic"],
        files=[
            dict(
                id="file",
                key="upload/file",
                name="filing.html",
                size=len(html),
                details=dict(ticker="ACME", reportDate="2025-03-31", form="10-Q"),
            )
        ],
    )
    jobs.add(job)
    return store, jobs, job, FixtureProvider(settings.provider())


@pytest.mark.parametrize("provider_id", ["openai", "nebius"])
@pytest.mark.parametrize("rerank", [False, True])
def test_ingestion_research_and_checkpoint_resume(settings, provider_id, rerank):
    settings = settings.model_copy(update={"ai_provider": provider_id})
    store, jobs, job, provider = setup_job(settings)
    with pytest.raises(RuntimeError, match="Injected interruption"):
        ingest(job, store, jobs, provider, fail_after="upsert")
    assert jobs.all()[0]["status"] == "processing"
    before = provider.embedding_calls
    ingest(job, store, jobs, provider)
    ready = jobs.all()[0]
    assert ready["status"] == "ready"
    assert provider.embedding_calls == before  # identical text shared by both strategies
    assert ready["namespaces"]["fixed"].endswith("py-v1")
    for strategy in ["fixed", "semantic"]:
        answer = research(
            store,
            jobs.all(),
            provider,
            "What revenue was reported?",
            ["ACME"],
            strategy=strategy,
            rerank=rerank,
            trace=True,
        )
        assert answer["status"] == "answered"
        assert answer["sources"][0]["excerpt"].find("125 million") >= 0
        assert answer["ranking"] and answer["metrics"]["aiProvider"] == provider_id
    # A replay of a completed graph republishes metadata without paid work.
    before = provider.embedding_calls
    ingest(job, store, jobs, provider)
    assert provider.embedding_calls == before


def test_policy_repair_and_invalid_chart(settings):
    store, jobs, job, provider = setup_job(settings)
    answer = research(store, [], provider, "What is profit?", ["ACME"])
    assert answer["status"] == "needs_clarification" and not provider.embedding_calls
    ingest(job, store, jobs, provider)
    provider.invalid = True
    result = research(store, jobs.all(), provider, "What revenue was reported?", ["ACME"])
    assert result["citation_ids"] == ["S1"] and provider.repair_calls == 1
    provider.invalid, provider.invalid_chart = False, True
    repaired = research(store, jobs.all(), provider, "What revenue was reported?", ["ACME"])
    assert repaired['status'] == 'answered' and repaired['chart']['points'] == []
    assert provider.repair_calls == 2


def test_cash_flow_signed_adjustment():
    text = "(Dollars in millions)\nSix Months Ended June 30\nMetric | 2025 | 2024\nCash Flows from Operating Activities | |\nAdjustments to reconcile net income | |\nUnrealized gain on equity securities | (200) | (100)\nNet cash provided by operating activities | 500 | 400"
    result = cash_flow_answer(
        "What unrealized gain on equity securities adjustment affected operating cash flow in the first six months of 2025?",
        [dict(id="S1", ticker="ACME", text=text)],
    )
    assert "deducted" in result["answer"] and "−$200" in result["answer"]


def test_quota_and_embedding_validation(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": "credit_balance_exhausted"}})

    provider = Providers(
        settings, settings.provider(), httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(AppError, match="quota"):
        provider.request("https://fixture.example/test")
    assert len(calls) == 1
    with pytest.raises(AppError):
        provider.vector([math.nan] * provider.config.dimension)


def test_nebius_query_instruction_and_provenance(settings):
    settings = settings.model_copy(
        update={"nebius_api_key": "offline", "pinecone_api_key": "offline"}
    )
    config = settings.provider("nebius")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("embeddings"):
            return httpx.Response(
                200,
                json=dict(
                    model=config.embeddingModel,
                    data=[dict(index=0, embedding=[1] + [0] * (config.dimension - 1))],
                ),
            )
        return httpx.Response(
            200,
            json=dict(
                dimension=config.dimension,
                metric="cosine",
                vector_type="dense",
                tags={"embedding-model": "wrong"},
                status={"ready": True},
                host="fixture.pinecone.io",
            ),
        )

    provider = Providers(settings, config, httpx.Client(transport=httpx.MockTransport(handler)))
    provider.embed(["Revenue?"], purpose="query")
    assert json.loads(requests[0].content)["input"][0].startswith("Instruct:")
    with pytest.raises(AppError, match="provenance"):
        provider.pinecone_host()


@pytest.mark.parametrize("provider_id", ["openai", "nebius"])
def test_langchain_wire_schema_and_local_validation(settings, provider_id):
    settings = settings.model_copy(
        update={"openai_api_key": "offline", "nebius_api_key": "offline"}
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=dict(
                id="test",
                object="chat.completion",
                created=1,
                model=settings.provider(provider_id).chatModel,
                choices=[
                    dict(
                        index=0,
                        finish_reason="stop",
                        message=dict(role="assistant", content='{"ids":["a","b"]}'),
                    )
                ],
            ),
        )

    provider = Providers(
        settings,
        settings.provider(provider_id),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.structured("Rank evidence.", {"candidates": []}, Ranking) == {"ids": ["a", "b"]}
    assert requests[0]["response_format"]["json_schema"]["strict"] is True


def test_invalid_model_schema_is_rejected_locally(settings):
    settings = settings.model_copy(update={"openai_api_key": "offline"})

    def handler(request):
        return httpx.Response(
            200,
            json=dict(
                id="test",
                object="chat.completion",
                created=1,
                model=settings.provider().chatModel,
                choices=[
                    dict(
                        index=0,
                        finish_reason="stop",
                        message=dict(
                            role="assistant", content='{"ids":["a"],"invented_field":true}'
                        ),
                    )
                ],
            ),
        )

    provider = Providers(
        settings, settings.provider(), httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(AppError, match="schema validation"):
        provider.structured("Rank evidence", {}, Ranking)


def test_invalid_repair_cannot_loop(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    provider.invalid = True
    original = provider.structured

    def malformed(system, user, schema, model=None):
        if schema is Repair:
            provider.repair_calls += 1
            return dict(sentences=[dict(text="Unsupported", source_ids=["S99"])], calculations=[])
        return original(system, user, schema, model)

    provider.structured = malformed
    result = research(store, jobs.all(), provider, "What revenue was reported?", ["ACME"])
    assert result['status'] == 'insufficient_evidence' and not result['citation_ids']
    assert provider.repair_calls == 1


def test_visibility_failure_never_publishes_ready_and_resumes(settings, monkeypatch):
    store, jobs, job, provider = setup_job(settings)
    original = provider.pinecone
    visible = False

    def delayed(path, body):
        if path == "describe_index_stats" and not visible:
            return {"namespaces": {}}
        return original(path, body)

    provider.pinecone = delayed
    monkeypatch.setattr("finwhiz.ingestion.time.sleep", lambda _: None)
    with pytest.raises(AppError, match="making records visible"):
        ingest(job, store, jobs, provider)
    assert jobs.all()[0]["status"] != "ready"
    before = provider.embedding_calls
    visible = True
    ingest(job, store, jobs, provider)
    assert jobs.all()[0]["status"] == "ready"
    assert provider.embedding_calls == before
