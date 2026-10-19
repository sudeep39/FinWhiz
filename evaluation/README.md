# Financial RAG evaluation

The canonical suite is `benchmark.json`: 32 questions, including 28 answerable and four clarification/refusal cases, with 48 source-derived evidence labels. `queries.json` retains the earlier 24-question draft. Benchmark labels were reviewed against original SEC tables/passages by Codex and explicitly record that independent human review is pending. They are independent of retrieval output and identify issuer, fiscal report date, accession, source hash, metric/phrase and value. Narrative labels are partial required-evidence judgments, not exhaustive relevance judgments.

`scripts/build-benchmark.mjs` can regenerate candidate labels from checksum-verified source HTML. Its default is read-only; `--write` creates unreviewed candidates and refuses to replace an existing suite without `--replace`. Preserve reviewed labels before regeneration. Never mark newly generated candidates reviewed without reading the source quotes and original headers/rows.

## Four-condition experiment

```sh
npm run evaluate
npm run ingest:evaluation -- --apply
npm run evaluate -- --apply --answers
```

Validation makes no provider calls. Ingestion and the measured run make paid requests. Stop the server before standalone ingestion. The run compares fixed/hybrid, fixed/hybrid+rerank, semantic/hybrid and semantic/hybrid+rerank over identical corpus and question hashes. The dense/BM25/fusion budgets and final evidence policy stay constant. Condition order rotates by question/repetition; `--repetitions=2` adds repeat trials. No automatic optimization is performed on the test set.

Use a separately edited `--queries=evaluation/YOUR_SUITE.json` for other corpora. Every answerable query needs reviewed source labels; refusals/clarifications have explicit expected statuses and no fake relevance labels. Both strategies must cover the identical source documents. Questions with missing referenced source hashes are rejected.

## Metrics and reports

Recall@5 is gold-label coverage in the top five candidate chunks; MRR@5 is reciprocal rank of the first matching candidate. Matching requires issuer, report date and all label phrases. Repeated hits earn no extra coverage. Ranking is measured before final answer-context stratification or table expansion. `contextEvidenceRecall` measures label coverage in the final source excerpts. Eight-label trends may have low recall@5 even when all eight labels occur in the final context. The diagnostic unique-evidence DCG is not nDCG.

Raw JSONL is written as each trial finishes, including provider/validation failures. Summary accuracy/recall denominators retain failed trials. Answer latency includes query embedding, retrieval, reranking and generation; it excludes ingestion and any optional judge. Retrieval-only runs cannot establish end-to-end p95. JSON records model/index configuration, source and suite hashes, condition order and acceptance policy.

The runner generates `COMPARISON_REPORT.md`, results/provenance JSON and a manual review template under ignored artifacts/evaluation. Reranking deltas can be negative. The shared table splitter means this design primarily measures narrative-boundary changes. Inspect query-level regressions and add a held-out evaluation set before generalizing beyond this small table-heavy corpus.

## Answer quality

Optional `--judge` adds model-based atomic-claim support/citation review plus numeric, unit and period checks. A same-provider/model judge can be biased. Its scores are labelled automated and are not independent human certification.

Read each generated answer and its cited source. Fill manual-review.json with reviewer identity, atomic claims and support/citation judgments, numeric/unit/period correctness and answer relevance. Then run:

```sh
npm run score:reviews -- --run=artifacts/evaluation/YOUR_RUN_DIRECTORY
```

Incomplete reviews remain pending. Complete reviews pass only if the declared thresholds are met and no request failures occurred. Failed answerable trials must not receive invented supported claims. Report faithfulness, citation correctness, numeric accuracy, refusal/status accuracy, latency and retrieval coverage separately.

The current real run is blocked on OpenAI quota and has no measured quality results. See docs/COMPARISON_REPORT.md and docs/RUNBOOK.md. Offline fixture tests demonstrate orchestration and arithmetic, not financial retrieval effectiveness.
