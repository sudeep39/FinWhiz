# Backend improvement tracker

Status: Implemented and verified offline on 2026-10-21. Scope: [spec.md](spec.md).

- [x] B01 Add request-scoped deadlines, correlated stage/attempt measurements and explicit timeout classification. P01–P02.
- [x] B02 Add provider pooling, synchronized host discovery and query embedding cache. P03.
- [x] B03 Cache corpus/token statistics and schedule scoped vector searches together. P04.
- [x] B04 Add observable rerank fallback and answer-context budget checks. P05.
- [x] B05 Extract HTTP helpers, workspace/document routes, supervision, transport, generation, vector and validation components; add service contracts. P06.
- [x] B06 Correct queue retry capacity, read transactions and async blocking IO. P07.
- [x] B07 Validate narrative numeric literals against cited sources, preserving one repair and deterministic arithmetic. P08.
- [x] B08 Complete regression/browser checks, cache performance comparison and launch smoke test; record results.

Tests: `backend/tests/test_improvements.py` plus the retained backend and browser suites. Existing dependency lockfile is unchanged; no new packages are required. No paid provider calls are authorized for verification.

## Verification evidence

- `npm.cmd run test:python`: 45 passed.
- `npm.cmd test`: 40 retained JavaScript tests passed.
- `npm.cmd run test:browser`: Python desktop/mobile workflows passed.
- Real Python CLI/Uvicorn startup passed against an isolated runtime with blank credentials.
- Benchmark fallback summary/report probe passed; `pip check` and formatting/diff checks passed.
- Five-run sample: rebuilding token statistics median 768 ms; cached scoring median 102 ms over 2,924 chunks; rankings/scores identical. This is not live chat latency.
- No live provider calls or deployment. Launch/configuration/limitations: [performance guide](../../docs/BACKEND_PERFORMANCE.md).
