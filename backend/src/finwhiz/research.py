"""Bounded research graph: policy, retrieval, generation, validation and repair."""

import re
import time
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .config import AppError, job_matches, compact
from .finance import cash_flow_answer, empty_chart
from .retrieval import retrieve
from .schemas import Answer, Repair
from .answer_validation import validation_issues, finalize
from .telemetry import ResearchTrace, current_trace, span
from .documents import token_count
from .fiscal import catalog_documents
from .scope import resolve_scope
from .automotive_margins import answer as automotive_margin_answer

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


def build_graph(store, jobs, provider):
    def policy(state):
        plan = plan_question(state["question"], state["history"])
        scope = state.get('scope')
        if not state['retrieval_only'] and not plan.get('status'):
            documents = catalog_documents([job for job in jobs if job_matches(job, provider.config) and job.get('namespaces', {}).get(state['strategy'])], store)
            if documents:
                scope = scope or resolve_scope(state['question'], documents, state['tickers'], state['periods'], state['history'])
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
        result = retrieve(
            store,
            jobs,
            provider,
            state["plan"].get("retrievalQuestion", state["question"]),
            state["tickers"],
            state["periods"],
            state["strategy"],
            state["rerank"],
            filing_scope={(f['ticker'], f['reportDate']) for f in state['scope']['filings']} if state.get('scope') else None,
            resolved_scope=state.get('scope'),
        )
        return {"evidence": result}

    def generate(state):
        sources = state["evidence"]["sources"]
        if not sources:
            return {
                "answer": empty_answer(
                    "insufficient_evidence",
                    "I could not find supporting evidence in the selected filings. Try a different period or a more specific financial metric.",
                )
            }
        deterministic = cash_flow_answer(state["question"], sources)
        method = 'cash-flow-table'
        if not deterministic:
            deterministic = automotive_margin_answer(state['question'], sources, state.get('scope'))
            method = 'automotive-credit-table'
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
        )
        input_tokens = token_count(system) + token_count(compact(payload))
        measurement = current_trace.get()
        if measurement:
            measurement.record(
                "answer_input", 0, inputTokens=input_tokens, evidenceCount=len(sources)
            )
        if input_tokens > budget:
            raise AppError(
                "Selected evidence exceeds the answer context budget. Select fewer periods or a narrower question.",
                413,
            )
        answer = provider.structured(system, payload, Answer)
        return {"answer": answer}

    def validate(state):
        answer = state["answer"]
        issues = validation_issues(
            answer, state["evidence"]["sources"], not state.get("deterministic", False)
        )
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
        result = provider.structured(
            (PROMPTS / "repair.txt").read_text(encoding="utf-8"),
            dict(
                question=state["question"],
                tickers=state["tickers"],
                requestedPeriods=state["periods"],
                resolvedScope=state.get('scope'),
                evidence=state["evidence"]["sources"],
                validationErrors=state["issues"],
                previousAnswer=state['answer'],
            ),
            Repair,
        )
        allowed = {s["id"] for s in state["evidence"]["sources"]}
        if any(
            not s["source_ids"] or any(i not in allowed for i in s["source_ids"])
            for s in result["sentences"]
        ):
            return {'repaired': True, 'answer': empty_answer(
                'insufficient_evidence',
                'The retrieved passages did not support a verifiable answer. Ask for a specific metric and comparison period.'
            )}
        return {
            "repaired": True,
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
    graph.add_conditional_edges("retrieve", lambda s: END if s["retrieval_only"] else "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges("validate", lambda s: "repair" if s["issues"] else END)
    graph.add_edge("repair", "validate")
    return graph.compile()


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
    started = time.monotonic()
    context = context or ResearchTrace(
        str(uuid.uuid4()), getattr(getattr(provider, "settings", None), "chat_timeout_seconds", 60)
    )
    token = current_trace.set(context)
    try:
        state = build_graph(store, jobs, provider).invoke(
            dict(
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
        candidates=evidence.get("candidate_count", 0),
        evidence=len(sources),
        coverage=list(dict.fromkeys(f"{s['ticker']}:{s['report_date']}" for s in sources)),
    )
    if retrieval_only:
        return dict(ranking=evidence.get("ranking", []), metrics=metrics)
    answer = state["answer"]
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
