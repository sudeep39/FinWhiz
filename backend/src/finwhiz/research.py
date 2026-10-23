"""Bounded research graph: policy, retrieval, generation, validation and repair."""

import re
import time
import uuid
from pathlib import Path
from typing import TypedDict
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph

from .config import AppError, job_matches, compact
from .finance import empty_chart
from .retrieval import retrieve
from .schemas import Answer, Repair
from .answer_validation import validation_issues, finalize, referenced_sources, coverage_issues
from .telemetry import ResearchTrace, current_trace, span, operation_budget
from .documents import token_count
from .fiscal import catalog_documents
from .scope import resolve_scope
from .calculation_handlers import try_calculation
from .financial_facts import facts_payload
from .domain_types import ResearchProvider
from .storage import Storage
from . import agentic

PROMPTS = Path(__file__).parent / "prompts"


def plan_question(question, history):
    if re.fullmatch(
        r"(?:what(?:'s| is)|show(?: me)?|tell me about)\s+(?:the\s+)?(?:profit|performance|growth)\s*[?.!]*",
        question,
        re.I,
    ):
        return dict(
            status="needs_clarification",
            answer="Which metric and reporting period should I use? For example, net income, operating income, revenue growth, or gross margin for a particular quarter.",
        )
    if re.search(
        r"\b(?:predict|forecast)\b.*\b(?:stock|share) price\b|\b(?:stock|share) price\b.*\b(?:next|tomorrow|will)\b|\b(?:buy|sell) (?:the )?(?:stock|shares)\b",
        question,
        re.I,
    ):
        return dict(
            status="insufficient_evidence",
            answer="Historical SEC filings cannot establish future stock prices or a trading recommendation. I can compare the disclosed financial performance and risks instead.",
        )
    if re.search(r"\b(?:earnings call|transcript)\b", question, re.I) and not re.search(
        r"\b(?:filing|10-[kq])\b", question, re.I
    ):
        return dict(
            status="insufficient_evidence",
            answer="This corpus contains SEC filings, not earnings-call transcripts. Uploading transcripts is outside the current 10-K/10-Q ingestion scope; ask about disclosures in the selected filings instead.",
        )
    previous = next(
        (
            m
            for m in reversed(history)
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ),
        None,
    )
    followup = re.search(
        r"^(?:and\b|what about\b|how about\b|why\b|compare (?:it|that|those)\b)|\b(?:that metric|those figures|the same metric|that period)\b",
        question,
        re.I,
    )
    return dict(
        retrievalQuestion=(
            f"{previous['content'][:1500]}\nFollow-up: {question}"
            if followup and previous
            else question
        )
    )


def empty_answer(status, answer):
    return dict(
        status=status,
        answer=answer,
        citation_ids=[],
        sources=[],
        chart=empty_chart(),
        calculations=[],
    )


class ResearchState(TypedDict, total=False):
    """Per-invocation LangGraph state for research and agent tool rounds.

    research() initializes it; build_graph node functions return partial
    updates. RESEARCH_GRAPH is shared, but this state and runtime are not."""
    runtime: 'ResearchRuntime'
    question: str
    tickers: list[str]
    periods: list[str]
    strategy: str
    rerank: bool
    history: list[dict]
    retrieval_only: bool
    trace: bool
    plan: dict
    evidence: dict
    answer: dict
    issues: list[str]
    repaired: bool
    deterministic: bool
    scope: dict
    deterministic_method: str
    agent_rounds: int
    agent_decision: dict
    agent_observations: list[dict]
    agent_attempts: list[str]
    agent_stop_reason: str
    agent_assessments: int
    evidence_budget: int
    source_catalog: dict[str, str]


@dataclass(frozen=True)
class ResearchRuntime:
    """Request dependencies supplied to the shared RESEARCH_GRAPH.

    research() constructs it from the blob store, a job snapshot and the
    selected provider. Nodes read these dependencies from state.runtime."""
    store: Storage
    jobs: list[dict]
    provider: ResearchProvider


def build_graph():
    """Compile research/agent nodes and bounded repair routing once at import.

    RESEARCH_GRAPH is reused by research(); each invocation supplies its own
    ResearchState and ResearchRuntime. Nested functions below are LangGraph
    callbacks, not independent HTTP endpoints.
    """
    def policy(state):
        """Graph entry: enforce question policy, resolve scope and check ready jobs."""
        runtime = state['runtime']
        store, jobs, provider = runtime.store, runtime.jobs, runtime.provider
        plan = plan_question(state["question"], state["history"])
        scope = state.get('scope')
        if not state['retrieval_only'] and not plan.get('status'):
            if scope is None:
                documents = catalog_documents([job for job in jobs if job_matches(job, provider.config) and job.get('namespaces', {}).get(state['strategy'])], store)
                if documents:
                    scope = resolve_scope(state['question'], documents, state['tickers'], state['periods'], state['history'])
            if scope:
                if scope['status'] != 'resolved':
                    return {'plan': plan, 'scope': scope, 'answer': empty_answer(scope['status'], scope['message'])}
        if (
            not plan.get("status")
            and not state["retrieval_only"]
            and not any(
                j["status"] == "ready"
                and j.get("namespaces", {}).get(state["strategy"])
                and job_matches(j, provider.config)
                for j in jobs
            )
        ):
            return {
                "plan": plan,
                "answer": empty_answer(
                    "insufficient_evidence",
                    "No indexed filings are available for this chunking strategy.",
                ),
            }
        return {
            "plan": plan,
            **({'scope': scope, 'tickers': scope['tickers'], 'periods': scope['filingDates']} if scope else {}),
            **(
                {"answer": empty_answer(plan["status"], plan["answer"])}
                if plan.get("status") and not state["retrieval_only"]
                else {}
            ),
        }

    def retrieval(state):
        """Graph/tool callback: run scoped hybrid retrieval under the answer budget."""
        runtime = state['runtime']
        store, jobs, provider = runtime.store, runtime.jobs, runtime.provider
        settings = getattr(provider, 'settings', None)
        budget = settings.answer_max_tokens if settings else 20000
        overhead = token_count((PROMPTS / 'answer.txt').read_text(encoding='utf-8')) + token_count(compact({
            'question': state['question'], 'tickers': state['tickers'], 'requestedPeriods': state['periods'],
            'resolvedScope': state.get('scope'), 'history': state['history'][-4:]})) + 1000
        result = retrieve(
            store,
            jobs,
            provider,
            state["plan"].get("retrievalQuestion", state["question"]),
            state["tickers"],
            state["periods"],
            state["strategy"],
            state["rerank"] if not state.get('agent_rounds') else False,
            filing_scope={(f['ticker'], f['reportDate']) for f in state['scope']['filings']} if state.get('scope') else None,
            resolved_scope=state.get('scope'),
            evidence_budget=max(0, budget - overhead),
            local_first=not state['retrieval_only'] and not state.get('agent_rounds'),
        )
        return {'evidence': result, 'evidence_budget': max(0, budget - overhead),
                'source_catalog': {s['chunkId']: s['id'] for s in result.get('sources', []) if s.get('chunkId')}}

    def assess_evidence(state):
        """Graph callback: ask the bounded agent for its next action, or skip it."""
        provider = state['runtime'].provider
        evidence = state['evidence']
        if not agentic.enabled(provider) or evidence.get('localAnswer') or state.get('agent_stop_reason'):
            return {'agent_decision': {'action': 'answer'}}
        if evidence.get('coverageReason') == 'evidence_budget':
            return {'agent_stop_reason': 'evidence_budget', 'agent_decision': {'action': 'answer'}}
        decision, reason = agentic.assess(provider, state['plan'].get('retrievalQuestion', state['question']), state.get('scope'),
                                          evidence, state.get('agent_observations', []))
        assessments = state.get('agent_assessments', 0) + 1
        if reason:
            return {'agent_stop_reason': reason, 'agent_decision': {'action': 'answer'},
                    'agent_assessments': assessments}
        if decision['action'] in ('search', 'read_table'):
            if state.get('agent_rounds', 0) >= provider.settings.agent_max_rounds:
                reason = 'round_limit'
            elif agentic.action_key(decision) in state.get('agent_attempts', []):
                reason = 'repeated_action'
        else:
            reason = 'evidence_sufficient' if decision['action'] == 'answer' else 'insufficient_evidence'
        return {'agent_decision': decision, 'agent_assessments': assessments,
                **({'agent_stop_reason': reason} if reason else {})}

    def agent_tool(state):
        """Graph callback: execute an authorized search/table read and merge evidence."""
        runtime, decision = state['runtime'], state['agent_decision']
        trace = current_trace.get()
        rounds = state.get('agent_rounds', 0) + 1
        attempts = state.get('agent_attempts', []) + [agentic.action_key(decision)]
        seconds = trace.remaining() - runtime.provider.settings.agent_answer_reserve_seconds if trace else 15
        if seconds <= 1:
            return {'agent_stop_reason': 'answer_time_reserved', 'agent_decision': {'action': 'answer'}}
        previous = state['evidence']
        try:
            with operation_budget(seconds):
                if decision['action'] == 'search':
                    incoming = retrieval({**state, 'agent_rounds': rounds,
                                          'plan': {**state['plan'], 'retrievalQuestion': decision['query']}})['evidence']
                else:
                    incoming = agentic.read_tables(runtime.store, previous.get('sources', []), decision['source_ids'])
            merged, catalog = agentic.merge_evidence(runtime.store, previous, incoming,
                                                     state.get('scope'), state['evidence_budget'], state['source_catalog'])
        except AppError:
            if trace:
                trace.remaining()
            return {'agent_rounds': rounds, 'agent_attempts': attempts, 'agent_stop_reason': 'tool_unavailable'}
        old = {s['chunkId']: s['text'] for s in previous.get('sources', [])}
        gained = sum(old.get(s['chunkId']) != s['text'] for s in merged.get('sources', []))
        observation = dict(tool=decision['action'], newEvidence=gained,
                           evidenceCount=len(merged.get('sources', [])), missingCoverage=merged.get('missingCoverage', []))
        if trace:
            trace.record('agent_tool_result', 0, round=rounds, **observation)
        return {'evidence': merged, 'source_catalog': catalog, 'agent_rounds': rounds, 'agent_attempts': attempts,
                'agent_observations': state.get('agent_observations', []) + [observation],
                **({'agent_stop_reason': 'no_new_evidence'} if not gained else {})}

    def generate(state):
        """Graph callback: use a verified local calculation or request an Answer."""
        provider = state['runtime'].provider
        if state.get('agent_stop_reason') == 'insufficient_evidence':
            return {'answer': empty_answer('insufficient_evidence',
                'The selected filings do not provide sufficient evidence for every part of this question. Try a narrower question or add the missing filings.')}
        if state['evidence'].get('missingCoverage'):
            if state['evidence'].get('coverageReason') == 'no_supporting_evidence':
                return {'answer': empty_answer('insufficient_evidence',
                    'I could not find supporting evidence for every requested company and period in the selected filings.')}
            return {'answer': empty_answer('needs_clarification',
                'I cannot fit supporting evidence for every requested company and period. Select fewer periods or ask a narrower question.')}
        if state['evidence'].get('localAnswer'):
            return {'answer': state['evidence']['localAnswer'], 'deterministic': True,
                    'deterministic_method': state['evidence']['localMethod']}
        sources = state["evidence"]["sources"]
        if not sources:
            return {
                "answer": empty_answer(
                    "insufficient_evidence",
                    "I could not find supporting evidence in the selected filings. Try a different period or a more specific financial metric.",
                )
            }
        deterministic, method = try_calculation(state['question'], sources, state.get('scope'))
        if deterministic:
            return {"answer": deterministic, "deterministic": True, 'deterministic_method': method}
        settings = getattr(provider, "settings", None)
        budget = settings.answer_max_tokens if settings else 20000
        system = (PROMPTS / "answer.txt").read_text(encoding="utf-8")
        payload = dict(
            question=state["question"],
            tickers=state["tickers"],
            requestedPeriods=state["periods"],
            resolvedScope=state.get('scope'),
            history=state["history"][-4:],
            retrievedCoverage=list(
                dict.fromkeys(f"{s['ticker']}:{s['report_date']}" for s in sources)
            ),
            evidence=sources,
            financialFacts=facts_payload(sources),
            evidenceAssessment=dict(action=state.get('agent_decision', {}).get('action'),
                                    missingEvidence=state.get('agent_decision', {}).get('missing_evidence', []),
                                    stopReason=state.get('agent_stop_reason')),
        )
        input_tokens = token_count(system) + token_count(compact(payload))
        measurement = current_trace.get()
        if measurement:
            measurement.record(
                "answer_input", 0, inputTokens=input_tokens, evidenceCount=len(sources)
            )
        if input_tokens > budget:
            return {'answer': empty_answer('needs_clarification',
                'The complete evidence exceeds the answer budget. Select fewer periods or ask a narrower question.')}
        answer = provider.structured(system, payload, Answer)
        return {"answer": answer}

    def validate(state):
        """Graph callback: check facts/citations/coverage, finalize, or route to repair."""
        answer = state["answer"]
        issues = validation_issues(
            answer, state["evidence"]["sources"], not state.get("deterministic", False)
        )
        issues += coverage_issues(answer, state['evidence']['sources'], state.get('scope'))
        if issues and state.get("repaired"):
            measurement = current_trace.get()
            if measurement:
                measurement.record('evidence_validation', 0, issueCount=len(issues), repaired=True, outcome='insufficient_evidence')
            return {'issues': [], 'answer': empty_answer(
                'insufficient_evidence',
                'I found relevant passages, but could not verify a complete answer against the cited evidence. Try narrowing the comparison period or asking for the disclosed revenue, cost of revenue, and gross margin separately.'
            )}
        measurement = current_trace.get()
        if issues and measurement:
            measurement.record('evidence_validation', 0, issueCount=len(issues), repaired=False, outcome='repair_required')
        return {
            "issues": issues,
            "answer": answer if issues else finalize(answer, state["evidence"]["sources"]),
        }

    def repair(state):
        """Graph callback: request one Repair; the next validation is still mandatory."""
        provider = state['runtime'].provider
        system = (PROMPTS / 'repair.txt').read_text(encoding='utf-8')
        payload = dict(
                question=state["question"],
                tickers=state["tickers"],
                requestedPeriods=state["periods"],
                resolvedScope=state.get('scope'),
                evidence=state["evidence"]["sources"],
                financialFacts=facts_payload(state['evidence']['sources']),
                validationErrors=state["issues"],
                previousAnswer=state['answer'],
            )
        settings = getattr(provider, 'settings', None)
        budget = settings.answer_max_tokens if settings else 20000
        if token_count(system) + token_count(compact(payload)) > budget:
            return {'repaired': True, 'deterministic': False, 'answer': empty_answer('needs_clarification',
                'The evidence and validation details exceed the answer budget. Select fewer periods or ask a narrower question.')}
        result = provider.structured(system, payload, Repair)
        allowed = {s["id"] for s in state["evidence"]["sources"]}
        if any(
            not s["source_ids"] or any(i not in allowed for i in s["source_ids"])
            for s in result["sentences"]
        ):
            return {'repaired': True, 'deterministic': False, 'answer': empty_answer(
                'insufficient_evidence',
                'The retrieved passages did not support a verifiable answer. Ask for a specific metric and comparison period.'
            )}
        return {
            "repaired": True,
            "deterministic": False,
            "answer": {
                **state["answer"],
                "answer": " ".join(
                    s["text"] + " " + " ".join(f"[{i}]" for i in s["source_ids"])
                    for s in result["sentences"]
                ),
                "citation_ids": list(
                    dict.fromkeys(i for s in result["sentences"] for i in s["source_ids"])
                ),
                "calculations": result["calculations"],
                # Repair does not regenerate charts. Never carry an invalid chart forward.
                "chart": empty_chart(),
            },
        }

    graph = StateGraph(ResearchState)
    for name, fn in [
        ("policy", policy),
        ("retrieve", retrieval),
        ('assess_evidence', assess_evidence),
        ('agent_tool', agent_tool),
        ("generate", generate),
        ("validate", validate),
        ("repair", repair),
    ]:

        def timed_node(function, stage):
            def run(state):
                with span(stage):
                    return function(state)

            return run

        graph.add_node(name, timed_node(fn, name))
    graph.add_edge(START, "policy")
    graph.add_conditional_edges("policy", lambda s: END if s.get("answer") else "retrieve")
    graph.add_conditional_edges("retrieve", lambda s: END if s["retrieval_only"] else 'assess_evidence')
    graph.add_conditional_edges('assess_evidence', lambda s: 'agent_tool' if (
        not s.get('agent_stop_reason') and s.get('agent_decision', {}).get('action') in ('search', 'read_table')) else 'generate')
    graph.add_edge('agent_tool', 'assess_evidence')
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges("validate", lambda s: "repair" if s["issues"] else END)
    graph.add_edge("repair", "validate")
    return graph.compile()


RESEARCH_GRAPH = build_graph()


def research(
    store,
    jobs,
    provider,
    question,
    tickers,
    periods=None,
    strategy="fixed",
    rerank=True,
    history=None,
    retrieval_only=False,
    trace=False,
    context=None,
    scope_result=None,
):
    """Execute one isolated research request on the shared compiled graph.

    Called by ResearchEndpoints, CLI evaluation and offline tests. jobs is a
    registry snapshot; tickers/periods bound retrieval; scope_result may supply
    an already-resolved scope. Returns the validated answer, cited excerpts
    and metrics, or ranking/metrics only when retrieval_only is true. context
    carries deadline/cancellation; otherwise a ResearchTrace is created here.
    """
    started = time.monotonic()
    context = context or ResearchTrace(
        str(uuid.uuid4()), getattr(getattr(provider, "settings", None), "chat_timeout_seconds", 60)
    )
    token = current_trace.set(context)
    try:
        state = RESEARCH_GRAPH.invoke(
            dict(
                runtime=ResearchRuntime(store, jobs, provider),
                question=question,
                tickers=tickers,
                periods=periods or [],
                strategy=strategy,
                rerank=rerank,
                history=history or [],
                retrieval_only=retrieval_only,
                trace=trace,
                **({'scope': scope_result} if scope_result else {}),
            )
        )
    finally:
        current_trace.reset(token)
    evidence = state.get("evidence", {})
    sources = evidence.get("sources", [])
    metrics = dict(
        **context.metrics(),
        aiProvider=provider.config.provider,
        latencyMs=round((time.monotonic() - started) * 1000),
        strategy=strategy,
        rerank=rerank,
        rerankFallback=evidence.get("rerankFallback", False),
        rerankFallbackReason=evidence.get("rerankFallbackReason"),
        retrievalDegraded=evidence.get('retrievalDegraded', False),
        degradationReasons=evidence.get('degradationReasons', []),
        missingCoverage=evidence.get('missingCoverage', []),
        agentic=agentic.enabled(provider) and not retrieval_only,
        agentRounds=state.get('agent_rounds', 0),
        agentAssessments=state.get('agent_assessments', 0),
        agentStopReason=state.get('agent_stop_reason', 'local_calculation' if evidence.get('localAnswer') else 'disabled'),
        agentTools=state.get('agent_observations', []),
        candidates=evidence.get("candidate_count", 0),
        evidence=len(sources),
        coverage=list(dict.fromkeys(f"{s['ticker']}:{s['report_date']}" for s in sources)),
    )
    if retrieval_only:
        return dict(ranking=evidence.get("ranking", []), metrics=metrics)
    answer = state["answer"]
    sources = referenced_sources(answer, sources)
    answer = {**answer, 'citation_ids': [source['id'] for source in sources]}
    metrics['retrievedEvidence'] = metrics['evidence']
    metrics['evidence'] = len(sources)
    metrics['retrievedCoverage'] = metrics['coverage']
    metrics['coverage'] = list(dict.fromkeys(f"{s['ticker']}:{s['report_date']}" for s in sources))
    if state.get("deterministic"):
        metrics["calculationMethod"] = state.get('deterministic_method', 'cash-flow-table')
    result = {
        **answer,
        **({'scope': state['scope']} if state.get('scope') else {}),
        "sources": [
            {**{k: v for k, v in s.items() if k != "text"}, "excerpt": s["text"]} for s in sources
        ],
        "metrics": metrics,
    }
    if trace:
        result["ranking"] = evidence.get("ranking", [])
    return result
