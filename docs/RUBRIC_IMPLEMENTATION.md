# Current rubric implementation and acceptance

This is the current checklist for the original nine-page **Week 2 Project Handout ( Oct 2026 ).pdf**, Financial Document Intelligence / Project 2. Earlier review documents describe the previous state. The original handout was read locally; linked reference solutions were not copied.

## Implementation map

| Requirement | Concrete implementation | Acceptance evidence |
|---|---|---|
| Named user, question type, corpus, surface | Investment analyst; historical financial/table/risk comparisons; uploaded SEC 10-K/10-Q; JavaScript web app | `docs/SUBMISSION.md` |
| Corpus source/count/format/language/source of truth | Frozen experiment: NVDA + TSLA, eight fiscal endpoints each, English HTML; public SEC URLs/accessions/SHA-256 | `experiment.json`, `data/experiment-filings.json`, download manifest |
| Cleanup, validation and ingestion | Hidden-content removal, table spans, metadata from contents, bounds, background queue, cache/retry, visibility checks | `lib/chunking.mjs`, `lib/ingest.worker.mjs`; all 16 real filings validated |
| Two chunkers | Fixed token windows and embedding-distance semantic boundaries; shared table-aware segmentation | Runtime and unit tests; real fixed preparation generated 2,924 chunks |
| Embedding/model choice and rationale | text-embedding-3-large, shortened to 1,536 dimensions, cosine; provenance-tagged index/cache | `pinecone/index.json`, `lib/providers.mjs`; live index configuration passed |
| Table-aware hybrid for investment analysts | Pinecone dense plus BM25, RRF, fiscal filters, row/header context and table expansion | `lib/retrieval.mjs`; offline integration tests |
| Reranking | Validated LLM ranking of the same candidate pool, on/off controls | UI control and four-condition runner |
| Same-query comparison and impact analysis | 32 source-reviewed questions, 48 evidence labels, identical-corpus checks, four conditions, counterbalanced order, error-inclusive metrics, report generation | `evaluation/benchmark.json`, `lib/benchmark.mjs`, `npm run evaluate` |
| Cited answers and mathematical support | Strict outputs, inline/source IDs, literal numeric checks, deterministic calculations, distinct percentage/fraction scales, charts and source dialog | Adversarial citation/number/scale regression tests and browser tests |
| Ambiguous/unsupported/missing-evidence queries | Explicit needs_clarification state, narrow deterministic policy, model clarification instructions, no-corpus fallback, refusal tests | `lib/query-policy.mjs`, `rubric.test.mjs`; missing-period case included in benchmark |
| Multi-turn research | Prior user question included in retrieval for referential follow-ups; bounded conversation for generation | Policy regression; free-form semantic resolution still needs live evaluation |
| Freshness cadence and SLA | Weekly operator SEC review; new/amended filing detection; authenticated operations endpoint; overdue-data/queue checks; 30-minute worker timeout | `quality-policy.json`, `scripts/check-freshness.mjs`, API and UI indicator |
| Faithfulness, relevance and latency targets | Explicit targets, optional automated claim judge, independent manual review sheet and pass/fail/pending scorer | `quality-policy.json`, `scripts/score-reviews.mjs`; no unmeasured pass claims |
| Nonfunctional resilience/security | Production password, safe source downloads/rendering, bounded request/upload/worker/chat loads, provider retry, immediate quota diagnosis, filesystem snapshots with checksum-verified restore | `SECURITY.md`, recovery and quota tests, prior zero-vulnerability advisory scan |
| Working code/assets and separate GitHub project | Clean standalone exporter, secret-signature scan and source manifest; original code backed up | `npm run export:project`; publication remains a separate requested action |
| Comparison report, overview, prompts, iterations, learnings | Actual report generator plus honest blocked-status report; submission-ready overview and demo script | `docs/COMPARISON_REPORT.md`, `docs/SUBMISSION.md`, `docs/DEMO_SCRIPT.md` |

## What is implemented versus what is demonstrated

All applicable rubric capabilities now have code, tests, or prepared submission material. **Measured rubric acceptance remains pending.** The live run passed Pinecone validation and parsed all 16 filings, but the first embedding request was rejected by OpenAI with `credit_balance_exhausted` / `insufficient_quota`. The user chose to run the benchmark later. No semantic-quality, reranking-improvement, answer-faithfulness or end-to-end latency score has been fabricated.

The 32 gold questions were reviewed against source tables/passages by Codex, not an independent human. Review flags record this explicitly. All 48 labels occur in the real fixed-chunk output. These are partial required-evidence labels, not exhaustive relevance judgments. An analyst should approve them before publishing quality conclusions.

The manual scorer cannot pass an incomplete review. It applies the declared faithfulness, citation, numeric/unit/period, relevance, status, retrieval and p95 latency gates. Scores below targets mean further iteration; implementing a gate does not guarantee passing it.

## External submission/deployment actions

After API quota is restored, follow `docs/RUNBOOK.md` to finish indexing and run the comparison. Fill the generated manual review and retain any regressions. Import `docs/SUBMISSION.md` into your Google Doc and add your personal reflections, the actual results, demo link and repository URL. Record the at-most-five-minute demo. The app cannot truthfully create those personal/external deliverables without your participation.

Hosted filesystem durability must be verified on the actual deployment. Recovery tooling does not turn an ephemeral disk into persistent storage. This version has a shared workspace, not per-tenant authorization. See SECURITY.md for scope.

The handout allows a custom code framework. n8n, graph-RAG, multilingual support, and other projects' specific query/node counts are alternatives, not additional Project 2 requirements.
