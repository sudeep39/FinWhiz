# FinWhiz — Financial Document Intelligence

## Project statement

For investment analysts asking historical financial and risk questions across company disclosures, FinWhiz is a JavaScript web research assistant over user-uploaded SEC 10-K and 10-Q filings. It uses table-aware hybrid retrieval, compares fixed versus semantic chunking on the same questions, optionally reranks evidence, and returns cited answers with traceable arithmetic and charts. The research corpus is populated by successful uploads rather than hardcoded ticker choices.

## Dataset and source of truth

The controlled experiment uses 16 English SEC HTML filings: NVIDIA and Tesla, eight fiscal reporting endpoints each, frozen as of 2026-10-18. Each company has two 10-Ks and six 10-Qs. Company fiscal calendars differ. A 10-K contains annual totals; Q4 values require matching nine-month evidence. Original SEC URLs, accessions, download checksums and fiscal dates are retained. The original ten-company archive remains preserved separately. Amendments are excluded from the frozen benchmark; the freshness checker detects them for operator review.

The application accepts readable HTML/PDF/TXT filings. Scans requiring OCR and other financial document classes are outside this version. Downloaded SEC sources, not model memory, are the financial source of truth.

## Pipeline and design decisions

1. Upload and validate a bounded batch; extract company/form/period from document contents where available. Save a durable queued job and acknowledge the browser.
2. A worker removes hidden HTML content, preserves table spans/headers, extracts readable blocks and groups canonical section/table parents.
3. Fixed narrative windows use a roughly 700-token budget less metadata prefix, with 100-token overlap. Semantic narrative segmentation uses sentence-unit embeddings, adjacent cosine-distance boundaries around the 80th percentile, bounded chunks and overlap. Tables use the same row/header-aware segmentation in both conditions. Oversized material has bounded fallbacks.
4. Embed with text-embedding-3-large shortened to 1,536 dimensions; cache by model/dimension/text and verify vector shape/provenance. This is a defensible starting configuration, not an empirically optimal claim.
5. Store dense vectors in Pinecone with separate job/strategy namespaces. Local files retain source uploads, canonical parents, BM25 text, queue state and caches; PostgreSQL is not used.
6. Retrieve dense top-5 per issuer/period and BM25 top-20, fuse to 80, diversify candidates, and optionally rerank the same pool. Select a bounded, stratified evidence context, with table expansion where safe.
7. Generate structured answers with source IDs, fiscal/scale distinctions, a clarification/refusal path, deterministic calculations and optional source-backed visualizations. Literal checks are an additional validation layer, not a guarantee of accounting correctness.

## Prompts and agent instructions

Actual system prompts and output schemas live in `lib/retrieval.mjs` and `lib/answer-review.mjs`. The answer prompt restricts claims to supplied evidence, treats documents/history as untrusted, requires inline citations, separates quarterly/YTD/annual values and fiscal calendars, asks for clarification when ambiguous, and requires Q4 derivation from annual minus matching nine-month totals. Reranking orders every provided ID exactly once. Optional claim review checks entailment, citations, units, periods and calculations separately from answer generation.

## Evaluation design

`evaluation/benchmark.json` has 32 source-reviewed questions and 48 evidence labels derived independently from original tables/passages. Gold review is identified as Codex source review; independent human approval is pending. Conditions are fixed/hybrid, fixed/hybrid+rerank, semantic/hybrid, and semantic/hybrid+rerank. Source and suite hashes enforce a shared experiment. Condition order rotates; trial failures are retained.

Metrics include required-evidence recall@5, MRR@5, final-context evidence coverage, status/refusal correctness, answer latency, and paired reranking deltas. Optional automated claim reviews are explicitly model judgments. Independent manual review supplies faithfulness, citation correctness, relevance and numeric/unit/period accuracy. Targets are faithfulness/citations/numerical correctness ≥95%, mean recall@5 ≥80%, relevant answers/status accuracy ≥90%, and p95 end-to-end answer latency ≤15 seconds. These are project targets, not promised or measured performance.

Current live status: Pinecone verification passed; all 16 filings validated; 2,924 fixed chunks prepared; 48/48 gold labels present. The live embedding run stopped because OpenAI reported exhausted credits. Retrieval and reranking impact results remain pending. Attach the generated comparison report after completing the run. Never replace this status with offline fixture scores.

## Nonfunctional behavior

Background ingestion, retries/caches, processing timeout, concurrency/rate limits, production password protection, restricted source downloads, safe rendering, secret exclusion and checksum-verified recovery support operation of a trusted shared workspace. A weekly SEC review policy and API expose source freshness and SLA breaches. This is not a multi-tenant financial production system. Hosted disk durability, provider quota, platform HTTPS, and deployment recovery must be verified in the actual environment.

## Iterations and engineering observations

The project moved from Python preparation/loading tools to a JavaScript upload/research application. The Python alternative was retained. Review removed catalog-driven UI availability, introduced content-derived issuer metadata, fixed duplicate rank votes, corrected percentage scaling, hardened invalid uploads/requests, and added regression tests. This iteration added explicit query policies, benchmark provenance and reports, manual acceptance gates, freshness monitoring, recovery, and immediate quota diagnosis.

Observed lessons: table structure and period identity matter as much as embedding similarity; an annual report is not a quarterly measurement; citations and numeric occurrence checks do not establish entailment; reranking must be measured rather than assumed beneficial; provider quota can block an otherwise valid pipeline; persistent vectors do not preserve queue/source/lexical state. Add your own learning experience and explain which AI coding tools you actually used in the demo.

## Submission attachments to complete

Import this document into the required Google Doc. Add the real four-condition results, comparison/impact report, independent review, personal reflections, repository/ZIP URL and ≤5-minute demo link. Repository publication and recording have not been performed automatically. Suggested demo narration is in DEMO_SCRIPT.md. The code package is prepared independently from the surrounding workspace.
