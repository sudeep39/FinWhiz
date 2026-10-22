"""Legacy-compatible blobs and an atomic SQLite job registry."""

import gzip
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import AppError, compact, digest


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Storage:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        return self.directory / digest(key)

    def get(self, key):
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError:
            return None

    def put(self, key, value):
        data = value.encode() if isinstance(value, str) else value
        target = self.path(key)
        temporary = target.with_suffix(f".{uuid.uuid4()}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def json(self, key, fallback=None):
        data = self.get(key)
        if data is None:
            return fallback
        if key.startswith("embedding/") and data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        return json.loads(data)

    def put_json(self, key, value):
        data = compact(value).encode()
        self.put(key, gzip.compress(data, mtime=0) if key.startswith("embedding/") else data)

    def remove(self, key):
        self.path(key).unlink(missing_ok=True)


class Jobs:
    """One worker claims jobs transactionally. Legacy state is imported once."""

    def __init__(self, store):
        self.store = store
        self.path = store.directory / "jobs.sqlite3"
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, ordinal INTEGER NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            if not db.execute("SELECT 1 FROM metadata WHERE key='legacy-import'").fetchone():
                for i, job in enumerate(store.json("state", {"jobs": []})["jobs"]):
                    db.execute(
                        "INSERT OR IGNORE INTO jobs VALUES (?, ?, ?)", (job["id"], compact(job), -i)
                    )
                db.execute("INSERT INTO metadata VALUES ('legacy-import','done')")

    @contextmanager
    def connection(self, write=True):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            if write:
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self):
        with self.connection(write=False) as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM jobs ORDER BY ordinal DESC")
            ]

    def add(self, job):
        with self.connection() as db:
            pending = [json.loads(r[0]) for r in db.execute("SELECT data FROM jobs")]
            if sum(j["status"] in ("queued", "processing") for j in pending) >= 3:
                raise AppError("Three batches are already queued. Wait for a batch to finish.", 429)
            ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM jobs").fetchone()[0]
            db.execute("INSERT INTO jobs VALUES (?,?,?)", (job["id"], compact(job), ordinal))

    def update(self, job_id, **changes):
        with self.connection() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise AppError("This job cannot be retried.", 400)
            job = {**json.loads(row[0]), **changes}
            db.execute("UPDATE jobs SET data=? WHERE id=?", (compact(job), job_id))
            return job

    def retry(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or json.loads(row[0])["status"] != "failed":
                raise AppError("This job cannot be retried.", 400)
            pending = [json.loads(r[0]) for r in db.execute("SELECT data FROM jobs")]
            if sum(j["status"] in ("queued", "processing") for j in pending) >= 3:
                raise AppError("Upload queue is full. Please try again later.", 429)
            job = {**json.loads(row[0]), "status": "queued", "error": None, "percent": 0}
            db.execute("UPDATE jobs SET data=? WHERE id=?", (compact(job), job_id))
            return job

    def claim(self):
        with self.connection() as db:
            for job_id, data in db.execute("SELECT id,data FROM jobs ORDER BY ordinal"):
                job = json.loads(data)
                if job["status"] == "queued":
                    job.update(status="processing", startedAt=now())
                    db.execute("UPDATE jobs SET data=? WHERE id=?", (compact(job), job_id))
                    return job

    def recover(self):
        for job in self.all():
            if job["status"] == "processing":
                self.update(job["id"], status="queued", detail="Resuming after server restart")


@contextmanager
def runtime_lock(directory):
    """OS-released lock prevents two API/worker owners of a runtime after crashes."""
    stream = (Path(directory) / "python-owner.lock").open("a+b")
    try:
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise AppError("This runtime already has a Python application owner.") from None
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()
