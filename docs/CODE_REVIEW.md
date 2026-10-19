> Historical pre-implementation review. For the current status see [RUBRIC_IMPLEMENTATION.md](RUBRIC_IMPLEMENTATION.md) and [RUNBOOK.md](RUNBOOK.md).

# FinWhiz code and functionality review

Review date: October 18, 2026 (local). Scope: frontend, HTTP API, worker ingestion, chunking, providers, retrieval, filesystem storage, Python preparation/loading alternative, SEC collection, tests, deployment configuration and handout alignment. This is a source review plus offline verification, not an external penetration test or live provider certification.

## Preserved source

Before edits, 38 source files were backed up in `.backups/20261019T005620Z/source-before-review.zip` with a SHA-256 manifest. Secrets, uploaded data, caches, environments and dependency trees were excluded. No legacy Python tooling or archived company corpus was deleted. Both remain intentional project assets. Backups are local and excluded from the source export/Git.

## Fixed findings

| Severity | Finding | Resolution and verification |
|---|---|---|
| High | Production mode outside the Replit marker could expose an unauthenticated shared workspace | Require APP_PASSWORD for NODE_ENV=production as well; regression verifies 503 when absent |
| Medium | Malformed JSON roots and upload metadata caused errors or accepted arbitrary structured fields | Require JSON object, bounded scalar hints, allowed fields, real calendar dates, unique upload indexes; invalid-input tests |
| Medium | Rejected upload batches left orphan source files | Delete persisted batch sources on validation/enqueue failure; filesystem regression tests |
| Medium | Repeated chunks in a ranking inflated reciprocal-rank fusion | One vote per unique document ID per ranking; duplicate-invariance test |
| Medium | Ratio values could be labelled percent at the wrong scale | Separate percentage operation and reject percent-labelled fraction operation; deterministic arithmetic tests |
| Medium | Worker duration/memory unbounded | 768 MB V8 old-generation limit and 30-minute job timeout; not a total process RSS guarantee |
| Medium | Authentication guessing and paid-chat concurrency insufficiently bounded | 20 failed password attempts/minute per socket address; maximum two concurrent chats; retain 12 chats/minute per address |
| Medium | State persistence chain stayed rejected after a write failure | Subsequent saves can recover; current failed save still surfaces an error |
| Low | Generic upload depended on archived SEC manifest | Missing optional manifest no longer stops ingestion |
| Low | Reserved tokenizer strings in documents could fail encoding | Treat reserved strings literally; Unicode and special-token round-trip regression |
| Low | Source context lacked section metadata | Include parent section in newly ingested source metadata; old jobs require reingestion to gain it |
| Low | Browser test required archived filings in fresh checkout | Synthetic, explicitly test-only upload fixture; app still starts empty |

## Remaining release limitations

* **Quality evidence missing:** no real-provider retrieval comparison, reranking delta, faithfulness audit or load-test SLA. See RUBRIC_REVIEW.md. Offline provider fixtures verify orchestration, not semantic quality.
* **Durability:** source files, lexical chunks, canonical tables, queue and embedding cache live on filesystem. Pinecone retains vectors but cannot reconstruct all app state after disk loss. Reserved VM alone is not proof of disk durability across publishing/redeployment. Durable storage and recovery drill remain necessary.
* **Single workspace:** a shared bearer password, no individual accounts, tenant isolation or document ACLs. Limit deployment to trusted analysts. Rate limits use socket addresses, which may represent a shared reverse proxy, and are in memory.
* **Resource bounds:** uploads buffer up to 100 MB/batch; HTML parsing/tokenization can expand memory. Timeout/retry is available, but no distributed queue, transactional source/job write, dead-letter queue or provider cancellation on client disconnect. Aborted-transfer cleanup and crash recovery deserve additional fault-injection tests.
* **Retrieval:** lexical ranking runs in memory; namespace queries scale with jobs and periods. Maximum 16 evidence chunks can constrain broad comparisons. Re-uploading identical files still duplicates storage/provider calls, despite rank deduplication. Long-history/follow-up retrieval needs further design.
* **Financial semantics:** PDF extraction is text-first; complex table row/column associations, units, amended filings, restatements, fiscal calendars and non-GAAP normalization need source review. Numeric occurrence checks alone do not establish fact correctness. Unsupported forecasts should be refused; this is uncalibrated.
* **Security assurance:** no external penetration test, no malware scanner/OCR sandbox, no encryption-at-rest configuration in this project, no formal retention/deletion workflow, and no comprehensive adversarial prompt-injection benchmark. HTTP deployment must use platform HTTPS. Provider credentials remain server-side.
* **Maintainability:** several modules use dense one-line statements. A later formatting/module-boundary pass would improve reviewability; no mass rewrite was mixed into this audit.

## Verification evidence

Commands and final results are recorded in `docs/VERIFICATION.md`. Production npm audit reported **zero known vulnerabilities** (31 production dependency entries reported). This point-in-time advisory scan does not prove absence of vulnerabilities. Export checks reject common credential signatures and exclude runtime/secrets by allowlist; human review remains necessary.

The source exporter prepares a separate root with package files, Replit config, tests and small SEC metadata. It omits dependencies, runtime, secrets, backups and downloaded filing HTML. It does not initialize Git or publish. Archived filings remain in the original workspace and may be uploaded or re-downloaded later.
