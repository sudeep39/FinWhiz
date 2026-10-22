# Increment tracking

- [x] Add content identity catalog and transactional upload reuse.
- [x] Preserve embedding variants, source IDs, legacy namespaces, explicit failed-job retry.
- [x] Return mixed-batch reuse details and update upload feedback.
- [x] Support remote HTTP(S) backend in frontend proxy and restart helper.
- [x] Export offline content/namespace inventory using a read-only registry connection.
- [x] Offline Python regression suite, proxy tests and browser checks. PowerShell launcher syntax checked; hosted mode remains pending deployment/selection.

Validation: 49 Python tests passed, 2 proxy tests passed, browser checks passed with paid providers mocked. CLI local inventory and UTF-16 PowerShell-redirection manifest comparison passed; no remote providers were contacted.
- [x] User selected Replit as authoritative backend; local launcher uses it by default.
- [ ] Deploy current backend code to that instance and configure both frontends.
- [x] Compare authenticated Replit registry with local registries; preserve local source files/backups.
- [x] Review exact Pinecone cleanup candidates, then perform approved non-Replit cleanup.

## Replit retention cleanup, October 22, 2026

- [x] Authenticate and inspect Replit registry for both providers.
- [x] Retain all four Replit-owned namespaces (3,332 Nebius vectors).
- [x] Back up and readback-verify 6,333 other vectors, then delete those exact IDs from four namespaces.
- [x] Verify preserved namespace counts, deleted namespace emptiness and Replit registry ownership.
- [x] Persist shared backend URL, stop managed local backend, restart frontend against Replit.
- [x] Serve matching hosted UI and validate/translate browser origin for the older deployed API.
- [x] Verify identical authenticated local/Replit libraries and hosted UI assets; invalid POST returns expected 400 before model calls.
- [x] Block independent local API/ingestion writers while shared mode is configured.

Verification: 76 Python tests and 3 proxy tests passed. No embedding/chat calls were made. Replit itself has a duplicate Q2 Tesla upload; all its records were preserved as requested. Removing that internal duplicate requires deployed registry reconciliation. New Python scope/dedup improvements still need deployment to Replit.
