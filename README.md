# FinWhiz

**Live app:** [FinWhiz on Replit](https://fin-whiz.replit.app/)

Choose **OpenAI** or **Nebius Token Factory** in the top-bar AI provider selector. Selection applies to ingestion, semantic chunking, embeddings, reranking and research. See [provider setup and index commands](docs/AI_PROVIDERS.md). Each provider uses a separate compatible Pinecone embedding space; existing OpenAI documents are preserved.

Financial Document Intelligence for investment analysts. A JavaScript frontend, Node API and background workers answer questions over uploaded SEC filings using table-aware hybrid retrieval and Pinecone. PostgreSQL is not used. Research starts empty and populates only after ingestion completes.

## Start

```sh
npm ci
npm start
```

Requires Node >=22. Open http://localhost:3100. Copy `.env.example` to `.env` and configure server-side OPENAI_API_KEY and PINECONE_API_KEY. Set PINECONE_INDEX to the compatible dense/cosine/1,536-dimensional index; the default is finwhiz-filings-v1. APP_PASSWORD is required for production/Replit deployment. Never commit keys. Pinecone does not require a separate connection password.

Browser uploads support HTML/HTM, readable PDF and TXT, up to 32 files, 20 MB/file and 100 MB/batch. Metadata is extracted from contents where available; explicit hints fill missing ticker/form/period fields. A saved upload receives HTTP 202; cleanup, chunking, embedding and indexing continue in the backend. The company selector, period filters and chat become available only when ready.

## Rubric and measured status

The current [rubric implementation map](docs/RUBRIC_IMPLEMENTATION.md) supersedes earlier audit assessments. The app has fixed/semantic chunking, dense+BM25/RRF retrieval, optional reranking, cited structured answers, charts/calculations, clarification/refusal, freshness/SLA monitoring, recovery and a four-condition evaluation/report workflow.

The controlled dataset is NVIDIA and Tesla across eight fiscal endpoints each (16 filings). It is an experiment configuration, not a hardcoded UI corpus. The ten-company source archive is preserved. A 10-K contains annual totals, not standalone Q4 results.

Live validation reached 16 parsed filings and 2,924 fixed chunks; all 48 benchmark evidence labels are present. Pinecone configuration/connectivity passed. OpenAI then returned credit_balance_exhausted / insufficient_quota. Quality measurements remain pending; no improvement or faithfulness score is claimed. The user chose to run the benchmark after restoring quota.

```sh
npm run ingest:evaluation -- --apply
npm run evaluate -- --apply --answers
```

Stop the web server before standalone ingestion. These commands use paid providers. The first resumes the known corpus; the second runs the same 32 questions for fixed/semantic with reranking on/off. Optional `--judge` adds automated claim review. Plain `npm run evaluate` validates without provider calls. Results/report/manual review appear under artifacts/evaluation. Follow the [runbook](docs/RUNBOOK.md) for scoring, recovery and freshness.

## Verification and submission

```sh
npm test
npm run test:browser
```

Browser checks require installed Edge; provider calls are mocked. Python preparation/loading tools remain as an optional earlier implementation and have 16 passing tests; the web application does not call Python. JavaScript has 35 passing tests. These checks verify implementation, not real-provider retrieval quality.

* [Submission overview, dataset, prompts, iterations and observations](docs/SUBMISSION.md)
* [Comparison report status](docs/COMPARISON_REPORT.md)
* [Demo script, at most five minutes](docs/DEMO_SCRIPT.md)
* [Verification record](docs/VERIFICATION.md)
* [Security scope](SECURITY.md)
* [Evaluation protocol](evaluation/README.md)

Manual faithfulness review, the real measured report, Google Doc import, personal reflections and recording remain submission actions. See the rubric map for exact boundaries.

## GitHub and hosting

```sh
npm run export:project
```

This creates an independent source root under release with a SHA-256 manifest and common credential-signature checks. It excludes .env, runtime/uploads, caches, dependencies, downloaded HTML and local backups; it does not publish or initialize Git. Follow [GitHub preparation](docs/GITHUB_PREPARATION.md) when publication is requested. Fresh exports can restore SEC HTML with npm run download and an appropriate SEC_USER_AGENT; source checksums must match the frozen experiment.

Replit can import the independent GitHub repository directly. The root .replit config targets a Reserved VM. Configure HTTPS, strong APP_PASSWORD, provider secrets and a verified persistent FINWHIZ_DATA_DIR. Pinecone stores vectors; local files retain queue/source/lexical/cache data. Backup/restore tooling does not guarantee hosted disk durability. This is a trusted shared workspace, not a multi-tenant system.

Source snapshots before both review passes are retained under ignored .backups. No legacy Python source or archived filings were discarded.
