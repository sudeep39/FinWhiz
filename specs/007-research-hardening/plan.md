# Implementation plan

1. `financial_facts.py` parses source-located facts and loads complete tables.
   `fact_validation.py` binds numeric claims and calculation operands to them.
2. `calculation_handlers.py` is the shared entry point for complete deterministic
   handlers. Automotive margin arithmetic consumes generic facts; the existing
   cash-flow handler has an additional complete-request gate.
3. Retrieval first attempts local tables, then hybrid search with partial failure
   reporting. `evidence_selection.py` expands and packs evidence with coverage.
4. A module-owned compiled graph receives a per-request `ResearchRuntime`.
   Validation checks numeric identity and cited request coverage; repair loses
   the trusted deterministic flag and is independently validated and budgeted.
5. `ResearchEndpoints` owns chat admission and running tasks; `ResearchRequest`
   validates input. Explicit routers dispatch to the responsible area directly.
6. Run offline regression, HTTP contract, maintenance and proxy checks. Deploy
   from updated source; previously generated release archives are not refreshed
   by this increment.
