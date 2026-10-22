"""Checkpointed ingestion. Graph state contains references, not source/vector payloads."""

import logging
import sqlite3
import time
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .config import AIConfig, AppError, Settings, compact, digest
from .documents import PARSER_VERSION, make_parents, parse_file, split_parent, token_count
from .providers import Providers, cached_embeddings
from .storage import Jobs, Storage, now

logger = logging.getLogger(__name__)


class IngestionState(TypedDict):
    job_id: str
    version: str


def ingest(job, store, jobs, provider, fail_after=None):
    job_id, config = job["id"], provider.config

    def progress(phase, percent, detail):
        jobs.update(job_id, phase=phase, percent=percent, detail=detail)
        logger.info("ingestion job=%s phase=%s percent=%s", job_id, phase, percent)

    def parse(state):
        provider.pinecone_host()
        progress("validating", 4, "Checking document metadata and readable content")
        docs = [parse_file(file, store) for file in job["files"]]
        store.put_json(f"parsed/{job_id}/{PARSER_VERSION}", docs)
        return state

    def chunk(state):
        documents = store.json(f"parsed/{job_id}/{PARSER_VERSION}")
        for strategy in job["strategies"]:
            records = []
            progress("chunking", 20, f"{strategy}: segmenting documents")
            for doc in documents:
                parents = make_parents(doc["blocks"])
                store.put_json(f"parents/{job_id}/{doc['id']}", parents)
                for parent in parents:
                    prefix = f"{doc['company']} | {doc['ticker']} | {doc['form']} | Period ended {doc['reportDate']}\n{parent['section']}\n"
                    pieces = split_parent(
                        parent,
                        strategy,
                        lambda texts: cached_embeddings(provider, store, texts),
                        prefix,
                    )
                    for i, piece in enumerate(pieces):
                        text = prefix + piece
                        metadata = dict(
                            **{key: doc[key] for key in ('fiscal_year', 'fiscal_quarter', 'period_type') if key in doc},
                            text=text,
                            ticker=doc["ticker"],
                            company=doc["company"],
                            form=doc["form"],
                            report_date=doc["reportDate"],
                            filing_date=doc.get("filingDate", ""),
                            accession=doc.get("accession") or doc["checksum"][:24],
                            source_url=doc["sourceUrl"],
                            section_path=parent["section"],
                            source_anchor=parent.get("anchor", ""),
                            doc_id=doc["id"],
                            corpus_id=job_id,
                            parent_id=f"{doc['id']}:{parent['index']}",
                            chunk_type=parent["kind"],
                            strategy=strategy,
                            parser_version=PARSER_VERSION,
                            embedding_model=config.embeddingModel,
                            embedding_provider=config.provider,
                            embedding_dimensions=config.dimension,
                            embedding_fingerprint=config.fingerprint,
                        )
                        if len(compact(metadata).encode()) > 39000 or token_count(text) > 1600:
                            raise AppError(
                                "A chunk exceeds its size limit; review the document formatting."
                            )
                        records.append(
                            dict(
                                id=digest(
                                    [
                                        config.fingerprint,
                                        doc["checksum"],
                                        strategy,
                                        parent["index"],
                                        i,
                                        PARSER_VERSION,
                                    ]
                                ),
                                metadata=metadata,
                            )
                        )
            if not records:
                raise AppError("No readable chunks were generated.")
            store.put_json(f"chunks/{job_id}/{strategy}", records)
        return state

    def index(state):
        for strategy in job["strategies"]:
            namespace = f"web-{job_id}-{strategy}-{PARSER_VERSION}"
            records = store.json(f"chunks/{job_id}/{strategy}")
            for start in range(0, len(records), 24):
                batch = records[start : start + 24]
                vectors = cached_embeddings(provider, store, [r["metadata"]["text"] for r in batch])
                provider.upsert(
                    namespace, [{**row, "values": vector} for row, vector in zip(batch, vectors)]
                )
                progress(
                    "indexing",
                    45 + round((start + len(batch)) / len(records) * 45),
                    f"{strategy}: indexed {start+len(batch)} of {len(records)} chunks",
                )
            if fail_after == "upsert":
                raise RuntimeError("Injected interruption after upsert")
        return state

    def verify(state):
        progress("verifying", 94, "Waiting for search visibility")
        for strategy in job["strategies"]:
            namespace = f"web-{job_id}-{strategy}-{PARSER_VERSION}"
            count = len(store.json(f"chunks/{job_id}/{strategy}"))
            for attempt in range(40):
                stats = provider.pinecone("describe_index_stats", {})
                if stats.get("namespaces", {}).get(namespace, {}).get("vectorCount", 0) >= count:
                    break
                if attempt == 39:
                    raise AppError(
                        "Pinecone is still making records visible. Retry this job to verify and complete it."
                    )
                time.sleep(3)
        return state

    def publish(state):
        documents = store.json(f"parsed/{job_id}/{PARSER_VERSION}")
        jobs.update(
            job_id,
            status="ready",
            phase="ready",
            percent=100,
            detail="Ready for research",
            error=None,
            completedAt=now(),
            namespaces={
                strategy: f"web-{job_id}-{strategy}-{PARSER_VERSION}"
                for strategy in job["strategies"]
            },
            summaries=[
                dict(strategy=strategy, chunks=len(store.json(f"chunks/{job_id}/{strategy}")))
                for strategy in job["strategies"]
            ],
            documents=[{k: v for k, v in doc.items() if k != "blocks"} for doc in documents],
        )
        return state

    with sqlite3.connect(
        store.directory / "checkpoints.sqlite3", check_same_thread=False
    ) as connection:
        builder = StateGraph(IngestionState)
        stages = [
            ("parse", parse),
            ("chunk", chunk),
            ("index", index),
            ("verify", verify),
            ("publish", publish),
        ]
        for name, fn in stages:
            builder.add_node(name, fn)
        previous = START
        for name, _ in stages:
            builder.add_edge(previous, name)
            previous = name
        builder.add_edge(previous, END)
        graph = builder.compile(checkpointer=SqliteSaver(connection))
        graph_config = {"configurable": {"thread_id": f"{job_id}:{PARSER_VERSION}"}}
        snapshot = graph.get_state(graph_config)
        if snapshot.values and not snapshot.next:
            # Publication happened before a process exit; replay only the idempotent publication.
            publish(snapshot.values)
        else:
            graph.invoke(
                None if snapshot.values else {"job_id": job_id, "version": PARSER_VERSION},
                graph_config,
            )


def worker_main(directory, job, settings_values):
    logging.basicConfig(level=logging.INFO)
    store = Storage(directory)
    jobs = Jobs(store)
    settings = Settings(**settings_values)
    config = (
        AIConfig.model_validate(job["aiConfig"])
        if job.get("aiConfig")
        else settings.provider(job.get("provider", "openai"))
    )
    provider = Providers(settings, config)
    try:
        ingest(job, store, jobs, provider)
    except Exception as error:
        logger.error("ingestion failed job=%s type=%s", job["id"], type(error).__name__)
        jobs.update(
            job["id"],
            status="failed",
            detail="Processing stopped",
            error=(
                str(error)
                if isinstance(error, AppError)
                else "Document processing failed. Check the source format and retry."
            ),
        )
    finally:
        provider.close()
