"""Integrity-checked, non-overwriting runtime snapshots and legacy import."""

import json
import re
import shutil
import sqlite3
from pathlib import Path

from .config import AppError, digest
from .storage import Jobs, Storage, compact, now, runtime_lock

SAFE = re.compile(r"[a-f0-9]{64}|jobs\.sqlite3|checkpoints\.sqlite3")


def separate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if (
        source == destination
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise AppError("Source and destination must be separate directories.")
    if destination.exists() and any(destination.iterdir()):
        raise AppError("Destination must be empty; existing data is never overwritten.")
    return source, destination


def inspect_legacy(source):
    source = Path(source).resolve()
    raw = (source / digest("state")).read_bytes()
    state = json.loads(raw)
    if not isinstance(state, dict) or not isinstance(state.get("jobs"), list):
        raise AppError("Invalid legacy state.")
    if any(j["status"] in ("queued", "processing") for j in state["jobs"]):
        raise AppError("Stop the application and drain the queue before migrating or backing up.")
    missing = []
    for job in state["jobs"]:
        for file in job.get("files", []):
            if file.get("key") and not (source / digest(file["key"])).is_file():
                missing.append(file["key"])
        for strategy in job.get("namespaces", {}):
            if not (source / digest(f"chunks/{job['id']}/{strategy}")).is_file():
                missing.append(f"chunks/{job['id']}/{strategy}")
    if missing:
        raise AppError("Legacy artifacts are missing: " + ", ".join(missing))
    return state, raw


def snapshot(source, destination):
    source, destination = separate(source, destination)
    destination.mkdir(parents=True, exist_ok=True)
    with runtime_lock(source):
        modern = (source / "jobs.sqlite3").exists()
        if modern:
            store = Storage(source)
            jobs = Jobs(store).all()
            if any(j["status"] in ("queued", "processing") for j in jobs):
                raise AppError(
                    "Stop accepting jobs and wait for the queue to drain before backing up."
                )
            before = None
        else:
            state, before = inspect_legacy(source)
            jobs = state["jobs"]
        files = []
        for path in sorted(source.iterdir()):
            if not path.is_file() or not SAFE.fullmatch(path.name):
                continue
            target = destination / path.name
            if path.suffix == ".sqlite3":
                with sqlite3.connect(path) as src, sqlite3.connect(target) as dst:
                    src.backup(dst)
            else:
                shutil.copyfile(path, target)
            files.append(
                dict(
                    name=path.name, sha256=digest(target.read_bytes()), bytes=target.stat().st_size
                )
            )
        # Export current state for legacy rollback/readability; registry remains authoritative.
        state_path = destination / digest("state")
        state_path.write_text(compact({"jobs": jobs}), encoding="utf-8")
        files = [f for f in files if f["name"] != state_path.name]
        files.append(
            dict(
                name=state_path.name,
                sha256=digest(state_path.read_bytes()),
                bytes=state_path.stat().st_size,
            )
        )
        if before is not None and (source / digest("state")).read_bytes() != before:
            raise AppError("State changed during backup. Retry with the server stopped.")
        manifest = dict(version=2 if modern else 1, createdAt=now(), files=files)
        (destination / "BACKUP_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest


def restore(source, destination):
    source, destination = separate(source, destination)
    manifest = json.loads((source / "BACKUP_MANIFEST.json").read_text())
    files = manifest.get("files", [])
    if (
        manifest.get("version") not in (1, 2)
        or not files
        or len({f["name"] for f in files}) != len(files)
        or not any(f["name"] == digest("state") for f in files)
    ):
        raise AppError("Invalid backup manifest.")
    for file in files:
        path = source / file["name"]
        if (
            not SAFE.fullmatch(file["name"])
            or path.is_symlink()
            or digest(path.read_bytes()) != file["sha256"]
        ):
            raise AppError("Backup checksum or filename validation failed.")
    destination.mkdir(parents=True, exist_ok=True)
    for file in files:
        shutil.copyfile(source / file["name"], destination / file["name"])
    return dict(restoredFiles=len(files), pineconeRestored=False)


def migrate_legacy(source, destination, apply=False):
    source, destination = separate(source, destination)
    if (source / "jobs.sqlite3").exists():
        raise AppError("Source is already a Python runtime; use snapshot/restore.")
    state, before = inspect_legacy(source)
    inventory = [
        dict(name=p.name, sha256=digest(p.read_bytes()), bytes=p.stat().st_size)
        for p in sorted(source.iterdir())
        if p.is_file() and re.fullmatch(r"[a-f0-9]{64}", p.name)
    ]
    report = dict(
        dryRun=not apply,
        jobs=len(state["jobs"]),
        files=inventory,
        sourceStateSha256=digest(before),
        identifierPolicy="Preserve all legacy identifiers and namespaces; new ingestion uses py-v1.",
    )
    if apply:
        destination.mkdir(parents=True, exist_ok=True)
        for file in inventory:
            target = destination / file["name"]
            shutil.copyfile(source / file["name"], target)
            if digest(target.read_bytes()) != file["sha256"]:
                raise AppError("Migration readback checksum failed.")
        if (source / digest("state")).read_bytes() != before:
            raise AppError(
                "Legacy state changed during import. Stop the Node server and retry to an empty directory."
            )
        imported = Jobs(Storage(destination)).all()
        if imported != state["jobs"]:
            raise AppError("Job registry readback differs from legacy state.")
        (destination / "MIGRATION_MANIFEST.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    return report
