# Python duplicate-merge maintenance API

This API is implemented only in Python. Deploy it to Replit before using it against the shared library. No production records were changed while implementing it.

## Deployment requirements

- Preserve/export the **deployed** Replit runtime and source artifacts before switching backends. A fresh empty Python registry cannot manage the existing Replit records.
- Follow [Python runtime migration](PYTHON_MIGRATION.md) to import the stopped legacy runtime into a separate Python runtime, preserving job IDs, source IDs and namespaces. Do not overwrite the original runtime. Migrating the local development runtime does not migrate Replit's deployed library.
- On Replit set `APP_PASSWORD`, `PINECONE_API_KEY`, the existing compatible provider index configuration, and `FINWHIZ_DATA_DIR` to the migrated persistent runtime. Keep `FINWHIZ_BACKEND_URL` **empty on the authoritative backend**; that setting belongs to the local frontend client.
- Use one Python backend/worker owner per runtime. Do not run the old Node backend against the migrated directory. The retained JavaScript backend has no maintenance endpoint.

## API contract

`POST /api/jobs/{removeJobId}/deduplicate`

Required headers: `Authorization: Bearer <APP_PASSWORD>` and the matching `X-AI-Provider` (`nebius` for the identified Replit duplicate). Existing origin checks apply. The endpoint is disabled without a workspace password, including local development.

Preview request:

```json
{"keepJobId":"3f963a10-e7d8-4982-8d3f-6b11b2662cfe"}
```

Preview returns `confirmToken`, source checksum mappings, canonical/deletion namespaces, `deleteVectorCount`, and `status: "preview"`. It reads the registry, source files and local chunk artifacts without any Pinecone/model calls. No records are changed.

Apply request:

```json
{
  "keepJobId":"3f963a10-e7d8-4982-8d3f-6b11b2662cfe",
  "apply":true,
  "confirmToken":"TOKEN_FROM_PREVIEW"
}
```

The token binds the job snapshots, checksums, configuration and chunk IDs. A stale token returns 409. Only fully redundant ready jobs with matching source bytes, company/period metadata, embedding configuration, strategies and parser versions can be merged. Another job must not reference the deleted namespaces. Partial overlaps are rejected.

Apply waits for an idle library, blocks new uploads/research/retries during maintenance, verifies canonical vectors, backs up the redundant vector values/metadata and original registry entry, and verifies backup readback. It then atomically retires the duplicate's documents/namespaces before deleting exact backed-up vector IDs. The kept job and its namespace counts remain unchanged. No embedding or chat calls are made; Pinecone reads/deletes are remote operations.

The retired job remains as a source-link alias with no searchable documents/namespaces. Its old file IDs resolve to the canonical source storage location, preserving existing citations. Historical source/chunk blobs are left on disk for recovery and are not searched. Metadata-only aliases are not additional vector copies.

## Run from your local terminal after deployment

The helper uses `FINWHIZ_BACKEND_URL` and `FINWHIZ_SHARED_BACKEND_PASSWORD` from local `.env`, without printing the password:

```powershell
cd D:\Data\Sudeep-C\code\ai\finwhiz
$plan = .\.venv\Scripts\python.exe scripts/deduplicate-job.py --remove-job 6bfa4ce0-2cd2-4945-baa0-c13fe157fd95 --keep-job 3f963a10-e7d8-4982-8d3f-6b11b2662cfe | ConvertFrom-Json
$plan
```

Review the plan. For the currently identified standalone Q2 duplicate, expect **307 redundant vectors** across fixed and semantic namespaces; keep the eight-filing batch. Apply:

```powershell
.\.venv\Scripts\python.exe scripts/deduplicate-job.py --remove-job 6bfa4ce0-2cd2-4945-baa0-c13fe157fd95 --keep-job 3f963a10-e7d8-4982-8d3f-6b11b2662cfe --apply --confirm-token $plan.confirmToken
```

Success returns `status: "completed"` and `backupManifestKey`. Expected outcome for the audited library is 8 searchable document entries and 3,025 retained vectors, provided nothing changes before deployment.

## Failures and recovery

Before registry retirement, backup/verification failure leaves both searchable jobs untouched. After retirement, the duplicate stays out of search even if remote deletion fails. Repeat the same confirmed request to resume; exact-ID deletion is idempotent. A completed operation can be repeated without further deletion. The backend keeps maintenance running if the HTTP client disconnects and waits for it on shutdown. A preview POST may return 409 while maintenance is still active; wait for completion before retrying.

Durable operation manifests are stored under storage key `deduplication/{token}` and gzip vector archives under `deduplication-backup/{token}/{strategy}` in the configured runtime (physical files use the existing hashed-key storage format). Preserve the complete runtime backup. After a partial deletion, restore vectors from these archives **before** restoring the original searchable job; do not blindly restore registry references to absent vectors. Archive corruption blocks further deletion and requires operator recovery.
