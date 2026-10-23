# Bounded agentic hybrid RAG

## Objective

Keep BM25/vector fusion and trusted financial validation, and let a model choose
additional evidence gathering when the first result does not cover the question.
Verification is offline; this change does not deploy or modify vectors.

## Behavior

- Resolve the authorized company/filing scope before research. Attempt complete
  local calculations first; they make no agent, embedding or vector calls.
- Otherwise use the existing hybrid retrieval, then a structured evidence
  assessment. The agent may answer, search a focused query, read a complete table
  identified by current evidence, or declare insufficient evidence.
- Every search retains the original provider, strategy, company/date-pair scope
  and token budget. No model arguments can change those restrictions.
- Permit at most two additional read/search actions, and at most three evidence
  assessments. Stop on a repeated action, no new evidence, invalid arguments,
  exhausted context/time budget or unavailable tools. Preserve already gathered
  evidence for the usual grounded generation and validation fallback.
- Keep source IDs stable across rounds, prioritize newly found evidence when
  repacking, and include only final cited sources in the public answer.
- Scope resolution and arithmetic remain deterministic host services. The agent
  has no arbitrary code, filesystem, web, upload or deletion tool.
- Enforce the existing global deadline and reserve answer time. Agent planning
  has a separate eight-second stage timeout and an input-token budget. Additional
  hybrid searches skip LLM reranking to limit latency and cost.
- All answers still pass financial input, company/period coverage and citation
  checks, with the existing bounded repair. Agent sufficiency is advisory and
  never bypasses these checks.
- Record round/assessment counts, tool names, evidence gains, degradation and
  stop reason; do not expose hidden reasoning or log raw prompts/search queries.

## Configuration and rollback

Production defaults enable `AGENTIC_RAG_ENABLED=true`. Set it to `false` to
restore one-pass hybrid research. `AGENT_MAX_ROUNDS` accepts 0–2;
`AGENT_TIMEOUT_SECONDS=8`, `AGENT_MAX_TOKENS=8000` and
`AGENT_ANSWER_RESERVE_SECONDS=20` are bounded settings. Existing
`CHAT_TIMEOUT_SECONDS=60` still controls the entire request. Retrieval-only
evaluation bypasses agent decisions so previous ranking comparisons remain valid.

## Acceptance

Use scripted offline models to prove targeted multi-step retrieval, complete-table
reads, fixed scope, stable citations, bounded rounds, repeated/no-progress stops,
invalid action/source rejection, planner/tool failure fallback, answer-time
reservation, local calculation bypass, rollback and request isolation. Run the
existing backend and frontend proxy suites; no paid live checks are authorized.
