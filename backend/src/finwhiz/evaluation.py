"""Controlled four-condition experiments, provenance and manual acceptance."""

import math
import time
from .config import AppError, digest, index_config, job_matches
from .operations import POLICY
from .research import PROMPTS, research
from .schemas import Review
from .storage import now

CONDITIONS = [
    dict(strategy=strategy, rerank=rerank)
    for strategy in ("fixed", "semantic")
    for rerank in (False, True)
]


def validate_suite(suite):
    if not isinstance(suite, list) or not suite or len({q.get("id") for q in suite}) != len(suite):
        raise AppError("Supply queries with unique IDs.")
    for q in suite:
        if (
            not isinstance(q.get("id"), str)
            or not isinstance(q.get("question"), str)
            or not q["question"].strip()
            or not q.get("tickers")
            or not isinstance(q.get("periods"), list)
            or not isinstance(q.get("labels"), list)
            or q.get("expected") not in ("answered", "insufficient_evidence", "needs_clarification")
        ):
            raise AppError(f"Invalid query: {q.get('id')}")
        for label in q["labels"]:
            if (
                not label.get("ticker")
                or not label.get("reportDate")
                or not label.get("contains")
                or any(not isinstance(s, str) or not s.strip() for s in label["contains"])
            ):
                raise AppError(f"Invalid evidence label: {q['id']}")
        if q["expected"] == "answered" and not q["labels"]:
            raise AppError(f"Missing gold evidence labels: {q['id']}")


def matches_label(row, label):
    return (
        row.get("ticker") == label["ticker"]
        and row.get("report_date") == label["reportDate"]
        and all(s.lower() in row.get("text", "").lower() for s in label["contains"])
    )


def retrieval_metrics(ranking, labels, k=5):
    if not labels:
        raise AppError("Answerable queries require evidence labels.")
    found, first, dcg = set(), 0, 0
    for i, row in enumerate(ranking[:k]):
        hits = {j for j, label in enumerate(labels) if j not in found and matches_label(row, label)}
        if hits:
            first = first or i + 1
            dcg += 1 / math.log2(i + 2)
            found.update(hits)
    return dict(
        recallAt5=len(found) / len(labels),
        mrrAt5=1 / first if first else 0,
        uniqueEvidenceDcgAt5=dcg,
    )


def verify_corpus(suite, jobs, config):
    ready = [j for j in jobs if j["status"] == "ready" and job_matches(j, config)]
    docs = [d for j in ready for d in j.get("documents", [])]
    for q in suite:
        for ticker in q["tickers"]:
            if not any(
                d["ticker"] == ticker and (not q["periods"] or d["reportDate"] in q["periods"])
                for d in docs
            ):
                raise AppError(f"Missing corpus: {ticker} for {q['id']}")
        for label in q["labels"]:
            if not any(
                d["ticker"] == label["ticker"]
                and d["reportDate"] == label["reportDate"]
                and (not label.get("accession") or d.get("accession") == label["accession"])
                and (not label.get("sourceSha256") or d.get("checksum") == label["sourceSha256"])
                for d in docs
            ):
                raise AppError(f"Gold source missing or changed: {q['id']}")
    corpus = {
        strategy: sorted(
            {
                f"{d['ticker']}:{d['reportDate']}:{d.get('checksum') or d.get('accession')}"
                for j in ready
                if j.get("namespaces", {}).get(strategy)
                for d in j.get("documents", [])
            }
        )
        for strategy in ("fixed", "semantic")
    }
    if not corpus["fixed"] or corpus["fixed"] != corpus["semantic"]:
        raise AppError("Both chunkers must use the identical document corpus.")
    return corpus["fixed"]


def mean(values):
    return sum(values) / len(values) if values else None


def summarize(results, answers):
    groups = {}
    for condition in CONDITIONS:
        rows = [r for r in results if all(r[k] == v for k, v in condition.items())]
        scored = [r for r in rows if r["answerable"]]
        claims = [c for r in rows for c in r.get("automatedReview", {}).get("claims", [])]
        latencies = sorted(r["latencyMs"] for r in rows)
        groups[f"{condition['strategy']}/{'reranked' if condition['rerank'] else 'baseline'}"] = (
            dict(
                queries=len(rows),
                failures=sum(not r["success"] for r in rows),
                rerankFallbacks=sum(
                    bool(
                        r.get("metrics", (r.get("answer") or {}).get("metrics", {})).get(
                            "rerankFallback"
                        )
                    )
                    for r in rows
                ),
                recallAt5=mean([r["recallAt5"] for r in scored]),
                mrrAt5=mean([r["mrrAt5"] for r in scored]),
                meanLatencyMs=mean(latencies),
                p95LatencyMs=(
                    latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None
                ),
                statusAccuracy=mean([int(r["statusCorrect"]) for r in rows]) if answers else None,
                automatedFaithfulness=mean([int(c["supported"]) for c in claims]),
                reviewedClaims=len(claims),
            )
        )
    deltas = {}
    for strategy in ("fixed", "semantic"):
        a, b = groups[f"{strategy}/baseline"], groups[f"{strategy}/reranked"]
        deltas[strategy] = {
            key: b[key] - a[key] if a[key] is not None and b[key] is not None else None
            for key in ("recallAt5", "mrrAt5", "meanLatencyMs")
        }
    return dict(
        groups=groups,
        rerankingDeltas=deltas,
        latencyScope="end-to-end answer, excluding optional judge" if answers else "retrieval only",
        humanFaithfulness="pending independent claim review",
    )


def run_benchmark(
    suite,
    jobs,
    store,
    provider,
    answers=False,
    judge=False,
    repetitions=1,
    research_fn=research,
    on_result=None,
):
    validate_suite(suite)
    if type(repetitions) is not int or not 1 <= repetitions <= 5:
        raise AppError("Repetitions must be 1–5.")
    corpus = verify_corpus(suite, jobs, provider.config)
    provenance = dict(
        createdAt=now(),
        suiteSha256=digest(suite),
        corpusSha256=digest(corpus),
        corpus=corpus,
        aiProvider=provider.config.provider,
        index=index_config(provider.config),
        chatModel=provider.config.chatModel,
        judgeModel=provider.config.judgeModel if judge else None,
        answers=answers,
        repetitions=repetitions,
        order="rotated by query and repetition",
        targets=POLICY["targets"],
        policy=POLICY,
    )
    results = []
    for repeat in range(repetitions):
        for i, q in enumerate(suite):
            for offset in range(4):
                condition = CONDITIONS[(i + repeat + offset) % 4]
                started = time.monotonic()
                row = dict(
                    queryId=q["id"],
                    repeat=repeat,
                    **condition,
                    expected=q["expected"],
                    answerable=q["expected"] == "answered",
                    success=False,
                    recallAt5=0 if q["expected"] == "answered" else None,
                    mrrAt5=0 if q["expected"] == "answered" else None,
                )
                try:
                    answer = research_fn(
                        store,
                        jobs,
                        provider,
                        q["question"],
                        q["tickers"],
                        q["periods"],
                        condition["strategy"],
                        condition["rerank"],
                        q.get("history", []),
                        not answers,
                        answers,
                    )
                    row.update(
                        success=True,
                        latencyMs=round((time.monotonic() - started) * 1000),
                        status=answer.get("status"),
                        statusCorrect=answer.get("status") == q["expected"] if answers else None,
                        contextEvidenceRecall=(
                            retrieval_metrics(
                                [
                                    {**s, "text": s.get("excerpt") or s.get("text", "")}
                                    for s in answer.get("sources", [])
                                ],
                                q["labels"],
                                16,
                            )["recallAt5"]
                            if answers and q["labels"]
                            else None
                        ),
                        ranking=answer.get("ranking", []),
                        metrics=answer.get("metrics", {}),
                        answer=(
                            {k: v for k, v in answer.items() if k != "ranking"} if answers else None
                        ),
                    )
                    if row["answerable"]:
                        row.update(retrieval_metrics(answer.get("ranking", []), q["labels"]))
                    if answers and judge and answer.get("status") == "answered":
                        try:
                            row["automatedReview"] = provider.structured(
                                (PROMPTS / "review.txt").read_text(encoding="utf-8"),
                                dict(
                                    question=q["question"], goldEvidence=q["labels"], answer=answer
                                ),
                                Review,
                                model=provider.config.judgeModel,
                            )
                        except Exception as error:
                            row["judgeError"] = str(error)
                except Exception as error:
                    row.update(
                        success=False,
                        latencyMs=round((time.monotonic() - started) * 1000),
                        error=str(error),
                        statusCorrect=False if answers else None,
                        recallAt5=0 if row["answerable"] else None,
                        mrrAt5=0 if row["answerable"] else None,
                    )
                results.append(row)
                if on_result:
                    on_result(row, provenance)
    return dict(
        provenance=provenance, queries=suite, results=results, summary=summarize(results, answers)
    )


def comparison_report(run):
    lines = [
        "# Chunking comparison and reranking impact",
        "",
        f"Run: {run['provenance']['createdAt']}",
        f"Corpus SHA-256: {run['provenance']['corpusSha256']}",
        f"Query suite SHA-256: {run['provenance']['suiteSha256']}",
        "",
        f"Latency scope: {run['summary']['latencyScope']}. Failed requests remain in accuracy/recall denominators.",
        "",
        "| Condition | Trials | Failures | Rerank fallbacks | Recall@5 | MRR@5 | p95 ms | Status accuracy |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, group in run["summary"]["groups"].items():
        values = [
            group[k]
            for k in (
                "queries",
                "failures",
                "rerankFallbacks",
                "recallAt5",
                "mrrAt5",
                "p95LatencyMs",
                "statusAccuracy",
            )
        ]
        lines.append(
            "| "
            + name
            + " | "
            + " | ".join("pending" if v is None else str(round(v, 3)) for v in values)
            + " |"
        )
    lines.extend(
        [
            "",
            "## Reranking deltas",
            "",
            str(run["summary"]["rerankingDeltas"]),
            "",
            "## Limitations",
            "",
            "Independent manual claim, citation, period and unit review remains required. Automated review is not human certification. Table splitting is shared between strategies. Labels measure required evidence rather than exhaustive relevance. Reranking may regress. Fallback counts include skipped or incomplete reranking; a requested rerank condition is not proof that every trial was reranked. Retrieval-only latency cannot establish end-to-end p95. See raw trials and provenance for failures.",
        ]
    )
    return "\n".join(lines) + "\n"


def score_reviews(run, reviews):
    if not run["provenance"]["answers"]:
        raise AppError("Manual answer scoring requires an end-to-end run.")

    def key(r):
        return r["queryId"], r["strategy"], r["rerank"], r["repeat"]

    if not isinstance(reviews, list) or len({key(r) for r in reviews}) != len(reviews):
        raise AppError("Invalid or duplicate manual reviews.")
    known = {key(r) for r in run["results"]}
    if any(key(r) not in known for r in reviews):
        raise AppError("Review does not belong to this run.")
    indexed, groups = {key(r): r for r in reviews}, {}
    for name, summary in run["summary"]["groups"].items():
        strategy, mode = name.split("/")
        rows = [
            r
            for r in run["results"]
            if r["strategy"] == strategy and r["rerank"] == (mode == "reranked")
        ]
        selected = [indexed.get(key(row), {}) for row in rows]
        complete = bool(rows) and all(
            r.get("reviewed") is True
            and isinstance(r.get("reviewer"), str)
            and r["reviewer"].strip()
            and isinstance(r.get("claims"), list)
            and (not row["answerable"] or r["claims"])
            and all(
                isinstance(c.get("text"), str)
                and c["text"].strip()
                and type(c.get("supported")) is bool
                and type(c.get("citationCorrect")) is bool
                for c in r["claims"]
            )
            and all(
                type(r.get(k)) is bool
                for k in ("numericCorrect", "unitsCorrect", "periodsCorrect", "relevantAnswer")
            )
            for r, row in zip(selected, rows)
        )
        claims = [c for r in selected if r.get("reviewed") for c in r.get("claims", [])]
        faithfulness = mean([int(c["supported"]) for c in claims]) if complete else None
        citations = mean([int(c["citationCorrect"]) for c in claims]) if complete else None
        correct = (
            mean(
                [
                    int(r["numericCorrect"] and r["unitsCorrect"] and r["periodsCorrect"])
                    for r in selected
                ]
            )
            if complete
            else None
        )
        relevance = mean([int(r["relevantAnswer"]) for r in selected]) if complete else None
        targets = POLICY["targets"]
        passed = (
            complete
            and summary["failures"] == 0
            and faithfulness is not None
            and faithfulness >= targets["faithfulness"]
            and citations >= targets["citationAccuracy"]
            and relevance >= targets["answerRelevance"]
            and correct >= targets["numericAccuracy"]
            and summary["recallAt5"] is not None
            and summary["recallAt5"] >= targets["recallAt5"]
            and summary["statusAccuracy"] is not None
            and summary["statusAccuracy"] >= targets["statusAccuracy"]
            and summary["p95LatencyMs"] is not None
            and summary["p95LatencyMs"] <= POLICY["service"]["answerP95Ms"]
        )
        groups[name] = dict(
            reviewComplete=complete,
            faithfulness=faithfulness,
            citationAccuracy=citations,
            answerRelevance=relevance,
            numericUnitPeriodAccuracy=correct,
            acceptance=("pass" if passed else "fail") if complete else "pending",
        )
    return dict(
        scoredAt=now(),
        groups=groups,
        targets=POLICY,
        source="independent manual review; self-reported reviewer identities",
    )
