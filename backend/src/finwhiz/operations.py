"""Freshness and SLA reporting share the versioned quality policy."""

import json
from datetime import datetime, timezone
from .config import ROOT
from .storage import now

POLICY = json.loads((ROOT / "quality-policy.json").read_text())


def age(value, current):
    try:
        return max(
            0,
            (current - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds() * 1000,
        )
    except (ValueError, TypeError, AttributeError):
        return float("inf")


def operational_status(jobs, check=None, current=None):
    current = current or datetime.now(timezone.utc)
    check = check or {}
    latest = {}
    for job in jobs:
        if job["status"] == "ready":
            for doc in job.get("documents", []):
                latest[doc["ticker"]] = max(latest.get(doc["ticker"], ""), doc["reportDate"])
    return dict(
        policy=POLICY,
        checkedAt=current.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        freshness=dict(
            lastReviewedAt=check.get("checkedAt"),
            reviewDue=not check
            or age(check.get("checkedAt"), current)
            > POLICY["freshness"]["reviewEveryDays"] * 86400000,
            companies=[
                dict(
                    ticker=ticker,
                    reportDate=period,
                    ageDays=int(age(period + "T00:00:00Z", current) // 86400000),
                    stale=age(period + "T00:00:00Z", current)
                    > POLICY["freshness"]["maxPeriodAgeDays"] * 86400000,
                )
                for ticker, period in latest.items()
            ],
            updates=check.get("updates", []),
        ),
        sla=dict(
            breaches=[
                dict(jobId=j["id"], status=j["status"])
                for j in jobs
                if (
                    j["status"] == "queued"
                    and age(j.get("createdAt"), current)
                    > POLICY["service"]["maxQueueMinutes"] * 60000
                )
                or (
                    j["status"] == "processing"
                    and age(j.get("startedAt"), current)
                    > POLICY["service"]["maxProcessingMinutes"] * 60000
                )
            ],
            completed=[
                dict(
                    jobId=j["id"],
                    processingMs=age(
                        j["startedAt"],
                        datetime.fromisoformat(j["completedAt"].replace("Z", "+00:00")),
                    ),
                )
                for j in jobs
                if j["status"] == "ready" and j.get("startedAt") and j.get("completedAt")
            ],
        ),
    )
