# Query scope increments

- [x] Deterministic company, fiscal period and date resolution.
- [x] Missing-year clarification, UI conflicts, unavailable coverage and follow-ups.
- [x] Fiscal DEI extraction for new files and cached in-memory enrichment for legacy HTML.
- [x] Shared company/date-pair filters for vector and keyword retrieval.
- [x] Resolved context for reranking, generation and repair.
- [x] Authenticated scope preflight and frontend effective-scope display.
- [x] Review-only fiscal metadata backfill plan; no re-embedding or remote writes.
- [x] Offline resolver, retrieval, API, ingestion and browser regression checks.
- [x] Regulatory-credit comparison regression: reserve complete automotive revenue/cost/credit tables for both requested quarters, preserve them through reranking, and calculate cited margins from quarterly columns with costs unchanged. Fall back to grounded generation for missing, conflicting or unsupported table inputs.
- [ ] Review/apply metadata updates to the selected authoritative deployed runtime.
- [ ] Deploy updated backend and frontend; verify the chosen shared-backend target.

Offline evidence: both supplied Tesla questions resolved against the real local June 30, 2026 filing without provider calls. Local backfill proposal contains 308 updates; remote state was not inspected or modified by this increment.

Verification: 73 Python tests and 2 proxy tests passed. Desktop/mobile browser checks passed, including the effective-scope label. JavaScript syntax and diff whitespace checks passed. No paid verification calls or deployment were performed.
