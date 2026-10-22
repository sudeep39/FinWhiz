# Backend clarity and research latency improvements

Status: Implemented and verified offline 2026-10-21. See [tracker](tasks.md) for evidence.

The Python migration preserved long retry windows, repeated retrieval work and broad service boundaries. Research responses could wait for multiple remote stages without attribution. This increment makes waits bounded and diagnosable, reuses expensive resources and corrects the reproduced queue/evidence-validation defects. It does not claim measured live-provider speed or full semantic faithfulness.

| ID | Requirement | Acceptance |
| --- | --- | --- |
| P01 | Enforce a configurable chat response deadline and shorter stage budgets with bounded retries. | Offline expired-deadline, HTTP/API timeout and SDK-wrapped timeout tests; HTTP 504 with request ID. |
| P02 | Correlate graph stages, remote attempts, provider IDs and available model token usage. | Successful responses expose additive timing metrics; logs also capture failed stages. No question/evidence/credentials in events. |
| P03 | Reuse isolated provider clients, synchronize expiring host discovery and cache repeated query embeddings. | Four initial searches perform one discovery; copied cached vectors cannot be mutated by callers; pool closes clients once. |
| P04 | Cache parsed chunks and lexical statistics with invalidation; schedule searches across namespaces with four workers. | Golden BM25 scores preserved; cache invalidates on replacement; filters and coverage retained. |
| P05 | Bound reranking/answer context without silently dropping evidence coverage. | Oversized reranking keeps hybrid candidates and records fallback; oversized answers fail with an actionable scope error; timeouts fall back only while the overall budget remains. |
| P06 | Separate routes, multipart compatibility, worker supervision, transports, generation, vector access and answer validation. | Existing API/browser/worker tests pass through the new components; shared service contracts have explicit types. |
| P07 | Prevent retry queue overflow, release writer reservations for reads and offload blocking repository work from async handlers. | Three-job capacity enforced transactionally during retry; concurrent writer proceeds during a read transaction. |
| P08 | Reject unsupported narrative numbers and retain the one-repair limit and trusted deterministic financial paths. | A valid citation cannot authorize 999 when its evidence says 125; successful repair is verified. Literal checks do not prove metric/period/unit entailment. |

## Defaults and compatibility

Chat deadline 60 seconds; vector operation budget 8 seconds; embedding operation budget 15 seconds; generation stage budget 30 seconds; reranking stage budget 10 seconds; at most two attempts. Existing ingestion has its own 30-minute process deadline. Configuration lives in `.env.example` and validated settings.

Provider clients live for the application lifespan. Host/query caches expire after five minutes; query cache holds 128 vectors per configuration. Corpus cache holds eight file versions and uses path, modification time and size. Scope-specific lexical averages/document frequencies remain unchanged.

Rerank input budget defaults to 12,000 text tokens, answer input to 20,000 prompt/payload tokens using the existing tokenizer proxy. Fallback reason is observable and counted in benchmark summaries; results must not be presented as a successfully reranked condition when reranking was skipped.

The API returns at its deadline. Synchronous HTTP work already in progress is cooperatively cancelled between operations; its concurrency slot remains occupied until it exits. This avoids admitting replacement work while old requests continue. This is not a guarantee that Python can forcibly terminate an arbitrary thread.

The existing UI stays intact apart from accurately labeling limited reranking. Live provider performance, independent faithfulness review and hosted deployment remain outside this offline verification increment.
