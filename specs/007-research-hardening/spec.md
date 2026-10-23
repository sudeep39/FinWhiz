# Research correctness, latency and simplicity

## Scope

Address the seven findings from the backend review: numeric identity, complete
requests, unnecessary remote calculation calls, retrieval resilience, evidence
coverage/budgets, reusable tables and comparative sources, and orchestration/API
overhead. Preserve authentication, provider isolation, source IDs and maintenance
locking. Verification remains offline; no vector changes or paid model checks.

## Requirements

1. A parsed financial fact identifies source, company, metric, financial period,
   scale, filing date and row/column. Quarterly and YTD values remain distinct.
2. Numerical claims and arithmetic operands must match their metric/period;
   table presence of an unrelated matching number is insufficient. Cross-period
   operand order and consistent scales are checked. Unknown multi-year layouts
   cannot fall back to whole-document numeric matching.
3. Exact handlers answer only supported, complete requests. Missing companies,
   extra metrics and open-ended clauses use general grounded generation. All
   requested companies/periods must have cited coverage before an answer passes.
4. Complete local table calculations run before embedding, vectors and reranking.
   Consistent comparative columns can support multiple requested years from one
   citation. Conflicting original/comparative values prevent the exact path;
   the application does not silently decide whether a restatement supersedes an
   original disclosure.
5. Embedding/vector failures retain available keyword/successful vector results
   within the request deadline. Responses expose degraded retrieval and coverage.
6. Expand tables before token packing. Reserve coverage per requested filing,
   without the previous 16-source limit. Missing evidence produces an evidence
   limitation; a scope that cannot fit asks the user to narrow it. Generation and
   repair both enforce context budgets.
7. Compile the research graph once, with isolated request dependencies. Reuse
   pre-resolved scope. Register explicit HTTP methods and route directly to area
   handlers. Research inputs are typed while maintaining established 400/404 and
   authentication behavior. Corpus I/O/tokenization does not hold the global
   cache lock.

## Supported layouts and limits

The parser supports flattened pipe tables with English duration/month headers,
year columns, repeated HTML cells, accounting signs, scientific notation and
Unicode decimal digits. Tesla uses its calendar fiscal periods; other issuers
require explicit fiscal metadata. Unsupported layouts or ambiguous units remain
unverified rather than inferred. Complete-request matching is deliberately
conservative; unsupported wording continues through the general pipeline.

This increment does not provide OCR, arbitrary spreadsheet execution, universal
accounting taxonomy or semantic verification of every qualitative claim.

## Acceptance

Offline regressions must demonstrate wrong-period/metric/scale rejection,
complete versus partial requests, zero remote calls for the real Tesla margin
comparison, comparative-source consolidation, conflicting-value refusal,
degraded retrieval, 24-filing coverage, large-table budgeting, request isolation
and existing API/maintenance contracts.
