# Shared document library and upload reuse

One authoritative backend owns source files, parsed artifacts, jobs and vector references. Local and hosted frontends can both use that backend. Selecting/deploying the authoritative backend remains a review decision. No automatic synchronization between independent runtimes is implied.

## Requirements

- Allow the local frontend proxy and restart helper to target an HTTP(S) backend origin, without starting a local backend in this mode.
- Deduplicate byte-identical uploads within the authoritative runtime, including concurrent requests, repeated files in a batch, and mixed old/new batches.
- Canonical document identity is SHA-256 of original bytes, independent of filenames and upload jobs. Preserve existing source IDs and namespaces.
- Reuse only matching provider/embedding/index configuration, strategy set, and ingestion version. Missing legacy ingestion version is treated as reusable under the existing configuration; upgrading legacy parsing must be an explicit migration.
- Return existing queued/processing/ready/failed jobs. Failed jobs use explicit retry rather than a new paid ingestion. Return HTTP 200 when entirely reused; HTTP 202 when any files are newly queued.
- Reuse succeeds even when the new-ingestion queue is full. Queue limits still apply atomically to new work.
- Provide offline inventory of content checksums, jobs, embedding fingerprints and namespaces, with no remote calls or deletions.

## Limits

This increment retains the single-owner SQLite/filesystem architecture. It does not implement shared database/object storage, merge two deployed registries, deduplicate differently encoded copies of a filing, or delete existing Pinecone records. Deploy this code to the chosen backend before expecting its upload endpoint to deduplicate. Legacy namespaces and source links remain valid.
