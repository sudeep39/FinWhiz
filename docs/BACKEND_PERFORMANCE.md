# Launch and diagnose the Python backend

From PowerShell, using the existing project environment:

```powershell
cd D:\Data\Sudeep-C\code\ai\finwhiz
.\.venv\Scripts\python.exe run.py serve
```

Open **http://localhost:3100**. Stop with **Ctrl+C**. This command loads `.env`, uses the configured `.runtime-python` directory, and starts the normal ingestion worker. `npm.cmd start` is an equivalent convenience command that selects `.venv` automatically. No environment activation or dependency reinstall is needed in the current workspace.

For a new checkout, create `.venv` and install `backend/requirements.lock` first; see [the migration guide](PYTHON_MIGRATION.md). Keep provider keys server-side. The hosted deployment remains a separate release gate.

## What changed

- `api.py` owns authentication and application lifecycle; workspace/document handlers, HTTP compatibility helpers and worker supervision are separate modules.
- `Providers` is a compatibility facade over `JsonTransport`, `GenerationClient` and `PineconeClient`. `ProviderPool` owns application-lifetime clients isolated by full AI configuration.
- Index discovery is synchronized and cached for five minutes. Query embeddings are cached for five minutes, up to 128 questions per configuration. Neither cache crosses embedding spaces.
- `CorpusCache` retains eight parsed chunk/statistic entries, invalidated by file path, modification time and size. BM25 still computes scope-specific averages and document frequencies; cached data does not change rankings or filters.
- Vector searches across namespaces use four concurrent workers per chat. Responses omit Pinecone metadata already held locally, and parent tables are read once per document per request.
- Read transactions no longer reserve the SQLite writer slot. Blocking queue/file operations are offloaded from async handlers. Retrying a failed job respects the three-batch queue limit.
- Narrative numbers must appear in their cited evidence; unsupported numbers enter the single repair path. Server-computed financial responses retain deterministic handling. This check does not prove that every metric, fiscal period, unit or qualitative claim is semantically correct; independent quality review remains required.

## Latency controls

Defaults are applied automatically. Optional overrides are listed in `.env.example`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `CHAT_TIMEOUT_SECONDS` | 60 | Maximum time the API waits for research |
| `VECTOR_TIMEOUT_SECONDS` | 8 | Total budget for one vector/control-plane operation, including retries |
| `EMBEDDING_TIMEOUT_SECONDS` | 15 | Total budget for one embedding operation |
| `GENERATION_TIMEOUT_SECONDS` | 30 | Budget for answer generation or repair |
| `RERANK_TIMEOUT_SECONDS` | 10 | Reranking stage budget |
| `PROVIDER_ATTEMPTS` | 2 | Maximum attempts within the applicable budget |
| `RERANK_MAX_TOKENS` | 12000 | Candidate-text budget; oversized inputs retain hybrid ranking |
| `ANSWER_MAX_TOKENS` | 20000 | Prompt/payload budget; oversized evidence produces an actionable scope error |

Short connect/pool limits also apply. Stage budgets cap retry admission and socket timeouts, and late results are rejected. Because synchronous socket phases can accumulate, they are not independent hard wall-clock timers; the API's outer research timer bounds how long the caller waits. Model SDK connection/timeout exceptions are classified explicitly. Exhausted quota is never retried. A rerank timeout falls back only when the overall research budget remains; provenance records the fallback. Authentication, schema and index-provenance failures are not silently ignored.

The API returns HTTP 504 when its overall budget expires. Already-running synchronous HTTP work cannot be forcibly killed safely in a Python thread: cancellation is checked between operations, and its concurrency slot remains reserved until the work exits. This prevents a timed-out request from admitting uncontrolled replacement work. The deadline is a maximum wait, not a claim that answers will normally take 60 seconds; the original 15-second performance target remains unmeasured with live providers.

## Read the timing information

Every response includes `X-Request-ID`. Successful research responses add:

```json
{
  "metrics": {
    "requestId": "...",
    "stageTimingsMs": {"query_embedding": 0, "dense_search": 0, "bm25": 0},
    "operations": [],
    "rerankFallback": false,
    "rerankFallbackReason": null
  }
}
```

These are illustrative field names, not measured timings. Inspect the `/api/chat` response in browser developer tools, then search server logs for its request ID. Operations include remote attempts, response status/provider request ID when supplied, graph stages, cache hits and available model token usage. Failure-stage timings are logged even when no successful answer is returned. Events do not contain questions, source text or credentials.

Some timings are nested: `retrieve` contains embedding/search/BM25 and `generate` contains model generation. Do not add all stage values together. Parallel search operation durations can also overlap.

The UI labels limited reranking accurately. Benchmarks record per-trial metrics and count rerank fallbacks in their summaries/reports. Inspect those counts before treating a requested rerank condition as fully reranked.

## Offline verification on 2026-10-21

- Backend regression suite: 45 tests passed, including timeout classification, API deadline, pooling/host synchronization, cache invalidation, queue/read-lock fixes, numeric repair and budget fallback.
- Retained JavaScript suite: 40 tests passed.
- Browser workflows passed against Python. A real `run.py serve` / Uvicorn launch smoke test passed with blank credentials and a separate temporary runtime.
- Five-run local sample over 2,924 chunks: rebuilding lexical statistics took a median 768 ms; cached scoring took 102 ms, with identical rankings and scores. This measured local lexical work, not end-to-end chat or provider latency.

No paid provider calls or hosted deployment were performed. The new timings will identify the cause of future real slow requests; they do not retroactively establish the cause of the previously reported 3–4 minute wait.
