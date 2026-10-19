# Chunking comparison and reranking impact: current evidence

Status: **live measurement blocked; not a passing quality report**.

The real Pinecone connection and the configured dense/cosine/1,536-dimensional index were verified. All 16 NVIDIA/Tesla filings passed readable-content and metadata validation. Fixed preparation produced 2,924 chunks. All 48 source-derived gold labels are present in those chunks. These checks establish data preparation and label availability, not retrieval quality.

OpenAI rejected the first embedding batch with HTTP 429. A minimal diagnostic request returned error code `credit_balance_exhausted` and error type `insufficient_quota`. The user elected to run the benchmark later. No successful embedding, retrieval, reranking, generation or judge measurement is claimed for this attempted run.

| Condition | Recall@5 | MRR@5 | End-to-end p95 | Faithfulness |
|---|---|---|---|---|
| Fixed + hybrid | pending | pending | pending | pending |
| Fixed + hybrid + rerank | pending | pending | pending | pending |
| Semantic + hybrid | pending | pending | pending | pending |
| Semantic + hybrid + rerank | pending | pending | pending | pending |

Reranking deltas are **unknown**, not zero. Both strategies share table splitting; this is deliberate so table preservation does not confound narrative-boundary comparison. The 32-question suite covers tables, two narrative risk cases, comparisons, calculations, trends, ambiguity and unsupported requests. It is table-heavy and should not be generalized to arbitrary corpora.

`npm run evaluate -- --apply --answers` generates the actual comparison and reranking report plus raw evidence and failures. Use the independent manual review workflow before presenting faithfulness or acceptance. See RUNBOOK.md. If reranking regresses or p95 exceeds the target, report that result and iterate; the application does not substitute a desired outcome for measurement.
