"""Network-closed transport injected only into the spawned test worker."""

import json
import httpx


def waiting_worker(directory, job, values):
    import time

    time.sleep(60)


def fixture_worker(directory, job, values):
    import finwhiz.ingestion as ingestion
    from finwhiz.providers import Providers

    namespaces = {}

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("embeddings"):
            return httpx.Response(
                200,
                json=dict(
                    model=body["model"],
                    data=[
                        dict(index=i, embedding=[1.0] + [0.0] * (body["dimensions"] - 1))
                        for i in range(len(body["input"]))
                    ],
                ),
            )
        if "/indexes/" in request.url.path:
            config = job["aiConfig"]
            return httpx.Response(
                200,
                json=dict(
                    dimension=config["dimension"],
                    metric="cosine",
                    vector_type="dense",
                    tags={
                        "embedding-model": config["embeddingModel"],
                        "embedding-provider": config["provider"],
                    },
                    status={"ready": True},
                    host="fixture.pinecone.io",
                ),
            )
        if request.url.path.endswith("vectors/upsert"):
            namespace = namespaces.setdefault(body["namespace"], set())
            namespace.update(r["id"] for r in body["vectors"])
            return httpx.Response(200, json={"upsertedCount": len(body["vectors"])})
        if request.url.path.endswith("describe_index_stats"):
            return httpx.Response(
                200,
                json={
                    "namespaces": {
                        name: {"vectorCount": len(rows)} for name, rows in namespaces.items()
                    }
                },
            )
        raise AssertionError(f"Unexpected test network request: {request.url}")

    ingestion.Providers = lambda settings, config: Providers(
        settings, config, httpx.Client(transport=httpx.MockTransport(handler))
    )
    ingestion.worker_main(directory, job, values)
