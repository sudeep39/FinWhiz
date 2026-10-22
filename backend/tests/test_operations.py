import copy
import json
from datetime import datetime, timezone

import pytest

from finwhiz.catalog import select_filings
from finwhiz.config import AppError, digest
from finwhiz.evaluation import retrieval_metrics, run_benchmark, score_reviews
from finwhiz.operations import operational_status
from finwhiz.recovery import migrate_legacy, restore, snapshot
from finwhiz.storage import Jobs, Storage
from test_workflows import FixtureProvider


def test_metrics_failures_and_manual_review(settings):
    labels = [dict(ticker="ACME", reportDate="2025-03-31", contains=["Revenue", "125"])]
    row = dict(ticker="ACME", report_date="2025-03-31", text="Revenue 125")
    assert retrieval_metrics([row, row], labels) == dict(
        recallAt5=1, mrrAt5=1, uniqueEvidenceDcgAt5=1
    )
    jobs = [
        dict(
            id="job",
            status="ready",
            aiConfig=settings.provider().model_dump(),
            namespaces=dict(fixed="a", semantic="b"),
            documents=[dict(ticker="ACME", reportDate="2025-03-31", checksum="hash")],
        )
    ]
    suite = [
        dict(
            id="q",
            question="Revenue?",
            tickers=["ACME"],
            periods=[],
            labels=labels,
            expected="answered",
        )
    ]
    calls = []

    def fake(*args):
        calls.append(args)
        if args[6] == "semantic":
            raise AppError("synthetic provider failure")
        return dict(status="answered", ranking=[row], sources=[{**row, "excerpt": row["text"]}])

    run = run_benchmark(
        suite,
        jobs,
        Storage(settings.finwhiz_data_dir),
        FixtureProvider(settings.provider()),
        answers=True,
        research_fn=fake,
    )
    assert len(calls) == 4
    assert run["summary"]["groups"]["semantic/baseline"]["failures"] == 1
    assert run["summary"]["groups"]["semantic/baseline"]["recallAt5"] == 0
    assert run["summary"]["groups"]["semantic/baseline"]["statusAccuracy"] == 0
    scores = score_reviews(run, [])
    assert all(group["acceptance"] == "pending" for group in scores["groups"].values())


def test_legacy_import_snapshot_restore_and_tampering(tmp_path):
    original = Storage(tmp_path / "legacy")
    state = dict(
        jobs=[
            dict(
                id="j",
                status="failed",
                files=[dict(id="f", key="upload/f", name="filing.txt", size=3)],
            )
        ]
    )
    original.put_json("state", state)
    original.put("upload/f", b"abc")
    original.put_json("embedding/cache", [0.25, -0.5])
    target = tmp_path / "python"
    report = migrate_legacy(original.directory, target)
    assert report["dryRun"] and not target.exists()
    migrate_legacy(original.directory, target, True)
    assert Jobs(Storage(target)).all() == state["jobs"]
    before = {p.name: p.read_bytes() for p in original.directory.iterdir()}
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    snapshot(target, backup)
    restore(backup, restored)
    assert Jobs(Storage(restored)).all() == state["jobs"]
    assert Storage(restored).get("upload/f") == b"abc"
    assert Storage(restored).json("embedding/cache") == [0.25, -0.5]
    assert {p.name: p.read_bytes() for p in original.directory.iterdir()} == before
    with pytest.raises(AppError, match="empty"):
        restore(backup, restored)
    (backup / digest("upload/f")).write_bytes(b"tampered")
    with pytest.raises(AppError, match="checksum"):
        restore(backup, tmp_path / "bad")


def test_freshness_and_collection_policy():
    current = datetime(2026, 10, 21, tzinfo=timezone.utc)
    state = [
        dict(id="q", status="queued", createdAt="2026-10-20T00:00:00Z"),
        dict(id="r", status="ready", documents=[dict(ticker="ACME", reportDate="2025-03-31")]),
    ]
    status = operational_status(state, current=current)
    assert status["freshness"]["reviewDue"]
    assert status["freshness"]["companies"][0]["stale"]
    assert status["sla"]["breaches"] == [dict(jobId="q", status="queued")]
    recent = dict(
        form=["10-Q/A", "10-Q", "10-K", "10-Q"],
        reportDate=["2025-03-31", "2025-03-31", "2024-12-31", "2026-03-31"],
        filingDate=["2025-05-02", "2025-05-01", "2025-02-15", "2026-05-01"],
        accessionNumber=["a", "b", "c", "d"],
        primaryDocument=["a.htm", "b.htm", "c.htm", "d.htm"],
    )
    assert [r["accession"] for r in select_filings(recent, "123", "2025-06-01", 4)] == ["b", "c"]
