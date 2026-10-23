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
    """Filesystem blob store shared by API, ingestion, research and maintenance.

    api.create_app and CLI commands construct it for one runtime directory.
    Logical keys are hashed into filenames; job metadata is managed by Jobs."""
    def __init__(self, directory):
        """Resolve and create the runtime blob directory.

        Called by API/CLI setup; creates directories but does not open SQLite."""
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        """Map a logical key to its hashed path inside this runtime.

        Used by blob methods and cache/provenance readers. Returns a Path without
        reading or creating the target file."""
        return self.directory / digest(key)

    def get(self, key):
        """Read a logical blob as bytes, or return None if it is absent.

        Used for original uploads, recovery artifacts and JSON cache reads.
        Other filesystem errors propagate to the caller."""
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError:
            return None

    def put(self, key, value):
        """Atomically write bytes or UTF-8 text under a logical key.

        Used by uploads, ingestion and maintenance. Flushes and fsyncs a temporary
        file before replacement so readers do not observe a partial artifact."""
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
        """Decode a stored JSON blob, returning fallback only if absent.

        Used throughout ingestion/research for chunk, parent and embedding caches.
        Embedding blobs may be gzip-compressed; malformed JSON raises an error."""
        data = self.get(key)
        if data is None:
            return fallback
        if key.startswith("embedding/") and data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        return json.loads(data)

    def put_json(self, key, value):
        """Serialize and atomically store JSON; gzip embedding cache entries.

        Used by ingestion, cached_embeddings and maintenance checkpoints.
        Delegates durability to put; returns None."""
        data = compact(value).encode()
        self.put(key, gzip.compress(data, mtime=0) if key.startswith("embedding/") else data)

    def remove(self, key):
        """Remove one logical blob; a missing blob is harmless.

        Available to cleanup callers. Does not remove job registry entries or
        Pinecone vectors; those require their own coordinated maintenance."""
        self.path(key).unlink(missing_ok=True)


class Jobs:
    """Transactional SQLite registry for ingestion jobs and their checkpoints.

    Constructed beside Storage by API/CLI startup. Upload routes add jobs,
    supervisor/worker code claims them, and ingestion updates their progress.
    Legacy state is imported once when the registry is initialized."""

    def __init__(self, store):
        """Open/create registry tables and import legacy state once.

        Called by API/CLI setup with the runtime Storage. Persists an import
        marker so constructing Jobs again does not repeat the import."""
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
        """Yield a SQLite transaction and always close its connection.

        Registry methods and maintenance use this context manager. Writes use
        WAL and BEGIN IMMEDIATE; reads use BEGIN. Success commits, exceptions
        roll back, and lock contention can wait up to the connection timeout."""
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
        """Return decoded job dictionaries, newest registry ordinal first.

        Used by workspace state, research scope, workers and maintenance. Each
        call reads a snapshot rather than returning shared mutable registry data."""
        with self.connection(write=False) as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM jobs ORDER BY ordinal DESC")
            ]

    def add(self, job):
        """Insert a job, rejecting a queue with three queued/processing batches.

        Called by upload/ingestion entry points. Persists the supplied dictionary
        and a new ordinal; raises AppError when admission is full."""
        with self.connection() as db:
            pending = [json.loads(r[0]) for r in db.execute("SELECT data FROM jobs")]
            if sum(j["status"] in ("queued", "processing") for j in pending) >= 3:
                raise AppError("Three batches are already queued. Wait for a batch to finish.", 429)
            ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM jobs").fetchone()[0]
            db.execute("INSERT INTO jobs VALUES (?,?,?)", (job["id"], compact(job), ordinal))

    def update(self, job_id, **changes):
        """Merge changes into one persisted job and return the updated dictionary.

        Used by ingestion progress/checkpoints, supervision and maintenance.
        Raises AppError for an unknown job ID."""
        with self.connection() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise AppError("This job cannot be retried.", 400)
            job = {**json.loads(row[0]), **changes}
            db.execute("UPDATE jobs SET data=? WHERE id=?", (compact(job), job_id))
            return job

    def retry(self, job_id):
        """Requeue a failed job after checking queue capacity.

        Called by the document retry endpoint. Clears error/progress fields and
        returns the job; rejects unknown/nonfailed jobs or a full queue."""
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
        """Atomically mark the oldest queued job processing and return it.

        Called by ingestion worker code. Returns None when there is no queued job;
        the transaction prevents two claimers from taking the same batch."""
        with self.connection() as db:
            for job_id, data in db.execute("SELECT id,data FROM jobs ORDER BY ordinal"):
                job = json.loads(data)
                if job["status"] == "queued":
                    job.update(status="processing", startedAt=now())
                    db.execute("UPDATE jobs SET data=? WHERE id=?", (compact(job), job_id))
                    return job

    def recover(self):
        """Requeue jobs left processing after an interrupted server run.

        Called by API lifespan before starting the worker, under the runtime
        owner lock. Cached artifacts remain available for ingestion to resume."""
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
