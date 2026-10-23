# Implementation tracking

- [x] Parse reusable metric/company/period/unit/source-cell facts.
- [x] Validate financial claims and operands against metric and period identity.
- [x] Reject wrong-year, wrong-row, YTD/quarter, scale and operand-order errors.
- [x] Gate deterministic handlers on complete requests and cited coverage.
- [x] Calculate from local tables before any remote retrieval/model call.
- [x] Consolidate consistent comparative evidence; refuse conflicting values.
- [x] Preserve keyword/successful vector evidence on provider failures.
- [x] Pack complete tables by token budget and preserve requested filing coverage.
- [x] Replace the 16-source ceiling; report unfittable/missing coverage explicitly.
- [x] Compile the graph once and isolate request dependencies.
- [x] Reuse pre-resolved scope and avoid global cache locks during corpus reads.
- [x] Extract research endpoints, type input and register actual HTTP methods.
- [x] Complete final offline suite and proxy verification: 123 backend tests and 3 frontend proxy tests passed; diff whitespace checks passed. No paid/provider calls or vector writes.
- [ ] Redeploy the updated Python source to the shared Replit backend.
- [ ] Live verification remains deferred under the user's offline-only preference.
