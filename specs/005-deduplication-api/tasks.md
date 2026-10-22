# Python maintenance API

- [x] Authenticated, read-only merge preview with content/configuration validation.
- [x] Explicit apply with snapshot-bound confirmation token.
- [x] Idle-library checks and exclusion of concurrent uploads/research/retries.
- [x] Canonical visibility, remote count checks and vector backup/readback before deletion.
- [x] Atomic registry retirement, preserved old source URLs and exact-ID deletion.
- [x] Durable manifests, idempotent apply and resumable deletion failures.
- [x] Python client helper and Replit migration/deployment instructions.
- [x] Offline regression coverage; no JavaScript backend changes.
- [ ] Deploy Python to Replit with its migrated deployed registry and persistent artifacts.
- [ ] Preview and apply the standalone Q2 duplicate merge on Replit; verify 8 documents/3,025 vectors if the audited library is unchanged.

Validation: full Python suite passed (81 tests at that run), followed by all 8 maintenance tests including 3 additional recovery/integrity cases. Desktop/mobile browser checks passed with paid providers mocked. No additional production records were changed.
