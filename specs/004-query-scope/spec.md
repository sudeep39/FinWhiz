# Company and fiscal-period query scope

## Behavior

Resolve a question, UI selections, catalog and short conversation history into one explicit search scope before embedding, Pinecone search, keyword retrieval or generation. Use deterministic matching only; no LLM inference or paid verification is required.

- Recognize catalog company names/tickers plus curated Tesla and NVIDIA aliases. A question may narrow a multi-company UI selection. A contradictory company selection requires clarification.
- Resolve explicit quarters, ordinal quarter names, first/second half, fiscal years and exact filing dates against verified SEC DEI fiscal labels. Never map fiscal quarter numbers to calendar dates by assumption.
- A missing year is resolved only when matching catalog/UI evidence selects one fiscal year. Relative periods and unsupported ambiguous combinations require clarification.
- Keep requested financial periods separate from searched filing dates. Q1 may use Q2 first-half minus quarterly data. Q4 requires annual and nine-month filings; H2 requires annual and first-half filings. Availability of those documents does not guarantee every requested metric is in retrieved evidence.
- Preserve company/date pairs in both Pinecone and BM25 scoping, avoiding cross-company date combinations.
- Share scope with reranking, answer generation and repair. Show the resolved filing scope while searching and with the resulting answer. Preflight clarification avoids a chat/model call.
- Follow-ups can reuse the prior question and validated resolved scope, checking the current catalog/UI for conflicts. The backend independently resolves chat scope even after frontend preflight.
- New ingestion writes verified fiscal metadata; historical HTML filings are enriched in memory from their original SEC DEI facts. Unknown fiscal metadata remains unknown.

## Contract

`POST /api/scope` accepts the chat question/tickers/periods/strategy/history shape and returns `status`, `message`, `tickers`, `filingDates`, `filings` (ticker/date pairs), `requestedPeriods`, and `label`. Authentication/origin checks remain in force. No providers are instantiated by this endpoint. `/api/chat` returns the same effective `scope` alongside its answer; clarification and unavailable coverage return normal structured statuses.

## Compatibility and limits

Legacy namespaces and vector IDs are unchanged. Existing embeddings need no regeneration. `run.py fiscal-metadata-plan` produces local metadata-only update proposals and never applies them. Missing sources/unverified fiscal labels require explicit filing dates or additional verified metadata. Company recognition is catalog/alias based, not general named-entity recognition. Annual versus quarterly table column selection and factual validation remain separate from routing. Controlled retrieval-only evaluations retain their explicitly configured scope.
