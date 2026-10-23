# Implementation

`policy → retrieve → assess_evidence → agent_tool → assess_evidence`

The assessment routes to `generate → validate → repair → validate` when evidence
is sufficient or an agent boundary is reached. Local deterministic answers skip
model assessment. Explicit evidence insufficiency produces an evidence limitation
instead of a generated answer. Retrieval-only evaluation exits after retrieval.

- `agentic.py`: strict decision boundary, read-only table tool, stable-ID evidence
  merge and token packing. Search execution delegates to scoped hybrid retrieval.
- `research.py`: compiled LangGraph nodes, action/round state, validation and
  public operational metrics.
- `schemas.py`: constrained `AgentDecision` structured output.
- `generation.py` / `telemetry.py`: stage timeouts and nested operation budgets
  that leave the final answer reserve available.
- `config.py` / `.env.example`: defaults and immediate rollback switch.
- `test_agentic.py`: scripted graph/tool regressions without external calls.

Agentic retrieval is intended for incomplete evidence, not a promise of faster
answers. It can add up to three assessment model calls and two retrieval actions;
local calculations still avoid all of them. Live quality/cost/latency comparison
remains a separate, currently deferred verification step.
