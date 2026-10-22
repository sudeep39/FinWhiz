# One library across local and hosted frontends

The backend owns the library. Replit at https://fin-whiz.replit.app is now the authoritative backend. Local `.env` sets `FINWHIZ_BACKEND_URL` to that origin, so plain `restart.cmd` starts only the local frontend and uses Replit's files, jobs and vectors.

## Preview hosted-backend mode

Deploy the updated Python backend to your selected hosted instance first, preserving its runtime. Then point the local frontend at its origin:

```powershell
.\restart.cmd -BackendUrl https://YOUR-APP.replit.app
```

This stops servers previously managed by the helper and starts only the local frontend on http://127.0.0.1:5173. API uploads, document lists, downloads and research all go to that backend. Enter its workspace password in the frontend if requested. Local `.env` credentials and local files are not copied to it. The persisted `FINWHIZ_BACKEND_URL` means plain `restart.cmd` retains this shared mode. The local Python API/standalone ingestion reject startup in shared mode to avoid accidentally creating a second library.

Direct equivalent for the configured workspace: `npm.cmd run dev:frontend`. HTTPS certificate validation remains enabled. The proxy accepts an origin only, with no credentials/path/query. Shared mode serves the hosted HTML/JS/CSS as well as API requests, so local and remote UI versions match. Only allowlisted public assets are exposed. Unpublished local frontend/backend improvements are not active until deployed to Replit. Developers may explicitly request `--local-assets` when testing compatibility with a remote API.

## Duplicate uploads

The authoritative backend recognizes identical bytes for the same embedding configuration, strategy set and ingestion version. Renaming a file does not bypass detection. It returns the existing job instead of indexing it again, even if it is still queued. Failed copies require retry of the existing job. Mixed batches queue only unseen files. Corrected metadata on identical bytes does not overwrite an existing document: metadata corrections need a separate reprocessing workflow.

Provider/index/model/dimension changes are intentional separate ingestion variants. Fixed and semantic chunking remain separate vector representations. HTML and PDF representations of the same filing have different checksums. Existing source IDs and namespaces are retained; the canonical content identity is additive.

## Inventory before cleanup

Run on **each Python backend's machine**:

```powershell
.\.venv\Scripts\python.exe run.py library-inventory > local-library.json
```

On Replit use `python run.py library-inventory > replit-library.json`. This reads the registry without modifying it and uses stored checksums or hashes available source files locally. It does not contact Pinecone or charge providers. Unknown checksums remain null; a stored checksum does not prove the source file is still available or that vectors are safe to delete. A legacy Node runtime must first be exported/migrated into a separate Python runtime copy using the migration guide.

Compare checksums plus embedding fingerprints, parser versions and strategies across inventories. A batch namespace can contain both duplicates and unique documents: never delete a namespace just because one document overlaps. Preserve the selected source artifacts, job references and citation links before removing any redundant vectors. Unreferenced remote namespaces cannot be identified from only one local registry.

Once the Replit manifest has been copied locally, generate a content-overlap report without remote calls:

```powershell
.\.venv\Scripts\python.exe run.py library-inventory --source replit-library.json > library-overlap.json
```

The report lists matching checksums and embedding candidates. Its `safeToDelete` list is intentionally empty: strategy/parser equivalence, namespace contents and active references still require review.

Changing the frontend target does not merge existing uploads. Before switching permanently, reconcile local-only files into the chosen library using its updated upload endpoint. Existing duplicate Pinecone data is deliberately untouched by this increment.

## Replit retention cleanup

The later Replit-retention cleanup supersedes the preceding increment's no-deletion status. Its authenticated registry is the preservation authority. `scripts/cleanup-pinecone.py` defaults to preview and requires an authenticated audit file for any apply. It protects published namespaces and namespaces belonging to failed Replit jobs, refuses active ingestion, exports complete vectors/metadata to gzip archives, verifies archive readback, rechecks registry ownership/counts, and deletes exact backed-up IDs. Cleanup reports and restorable backups remain under `.backups/pinecone-cleanup-*`.

Completed October 22, 2026: removed 6,333 non-Replit vectors from four namespaces, retaining all 3,332 Replit-owned vectors in four namespaces. The OpenAI index was empty and was unchanged. Backup manifest: `.backups/pinecone-cleanup-20261022T172720602147Z/manifest.json`; archives include vector values and metadata and passed readback verification. Retained namespace counts and registry ownership were rechecked after deletion. The old local registries/artifacts remain on disk for recovery but reference removed namespaces and must not be used as the active library.

Replit's registry currently contains 9 ready document entries, including two uploads of the same Q2 2026 Tesla file. Those Replit-owned copies are preserved by this cleanup. Removing that internal duplicate also requires updating the deployed registry/artifacts; deleting its vectors alone would leave stale references. The deployed scope endpoint currently returns 404, so recent Python scope/dedup changes still require deployment.
