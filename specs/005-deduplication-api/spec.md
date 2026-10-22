# Python duplicate-job merge API

Expose `POST /api/jobs/{removeJobId}/deduplicate` under workspace authentication and origin checks. A configured password is required even in development. Default requests preview only; apply requires a fresh confirmation token tied to both job snapshots, source identities, processing configuration and chunk IDs.

Only fully redundant ready jobs are eligible. The kept job must contain identical source bytes and matching company/form/report-date metadata, embedding fingerprint, chunking strategies and verified parser version. Missing sources/artifacts, partial overlaps, shared deletion namespaces, queued/processing ingestion, active requests or stale previews must block destructive execution.

Before deletion, verify kept vectors, compare namespace counts, back up every removed vector's values/metadata and original job, and read backups back. Retire duplicate searchable references in one SQLite transaction, keeping its original file IDs as aliases to canonical source bytes. Delete exact backed-up IDs only; never use namespace-wide deleteAll.

Persist operation state in the runtime so a failed remote delete or interrupted manifest phase transition can resume. After retirement, the job must never become searchable automatically. Verify empty removed namespaces and unchanged kept counts before reporting completion. Preserve backups and source links. Concurrent uploads, retries and research are excluded during application; maintenance survives HTTP client cancellation and is drained before shutdown.

This increment targets Python only. It neither deploys to Replit nor changes its live vectors. Import the actual deployed legacy registry/artifacts into the persistent Python runtime before using the endpoint. Local frontend shared-backend configuration must not be copied onto the authoritative Replit backend.
