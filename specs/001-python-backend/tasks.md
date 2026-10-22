# Python backend migration increment tracker

Status: Python backend implemented locally. I01-I06 complete with offline evidence; I07 production acceptance remains pending. User explicitly selected offline-only verification on 2026-10-21. See [requirements](spec.md) and [technical plan](plan.md).

## Working rules

Choose the first unblocked approved increment. Mark its state In progress, record owner/date and execute only its agreed scope. Link requirement IDs in tests and change descriptions. Before marking Done, attach actual verification evidence and document deviations. Stop a handoff with a specific next action and blockers; never infer completion from elapsed effort.

States: Pending review, Ready, In progress, Blocked, Done. Checkboxes mean completed work, not proposed work. Scope review precedes changing an increment to Ready.

## Increment overview

| Increment | Outcome | Dependencies | Requirements | State |
| --- | --- | --- | --- | --- |
| I01 | Frozen contracts, fixtures and baseline | Scope review | R01-R20 baseline | Done |
| I02 | Runnable Python API and storage compatibility | I01 | R01, R02, R13, R14, R18 | Done |
| I03 | Isolated and validated provider adapters | I02 | R07, R08 | Done |
| I04 | Resumable ingestion and ready corpus | I02, I03 | R03-R06, R19, R20 | Done |
| I05 | Evidence-grounded research parity | I03, I04 | R09-R12, R14, R18 | Done |
| I06 | Python evaluation and operational tools | I04, I05 | R15, R16, R20 | Done |
| I07 | Verified deployment and rollback | I01-I06 | R13, R17 and release criteria | In progress |

## I01 Baseline and contracts

- [x] T001 Record reviewed decisions D01-D06 and unresolved questions; capture source revision or a SHA-256 inventory if the project is untracked.
- [x] T002 Run existing JavaScript/Python tests and browser checks where available; record commands, results and environmental blockers.
- [x] T003 Freeze API request/response/error/header fixtures, static routes, request limits and provider-selection behavior.
- [x] T004 Freeze representative source/parsing/chunking/retrieval/financial fixtures and benchmark provenance without paid provider calls.
- [x] T005 Inventory runtime formats, CLI commands and compatibility hazards; define live comparison tolerances/budget before that comparison.

Exit: contracts and baseline evidence reviewed; known failures recorded independently of migration regressions.

## I02 Python foundation

- [x] T006 Add Python packaging, locked dependencies, typed settings and dependency injection.
- [x] T007 Implement legacy storage reader, safe file/cache writes and source download behavior; verify gzip and hash compatibility.
- [x] T008 Implement API lifecycle, static serving, health/state/operations contracts and compatibility errors.
- [x] T009 Implement authentication, origin/security headers, request limits and request/provider context isolation.

Exit: clean startup and foundational API contracts pass against legacy fixture data; no legacy data modified.

## I03 Provider integration

- [x] T010 Implement chat/embedding adapters, immutable snapshots and index provenance validation for both providers.
- [x] T011 Preserve Nebius query instructions, dimension validation, response ordering and compatible embedding fingerprints.
- [x] T012 Implement local output schemas, quota handling, bounded retries/timeouts and batching; prove failure behavior with fixtures.

Exit: both adapters pass isolated fixture tests; any live capability check is separately recorded.

## I04 Ingestion

- [x] T013 Port metadata extraction and HTML/PDF/TXT parsing; assess reuse of existing Python functions.
- [x] T014 Port parent/table handling and fixed/semantic chunking; record versioned boundary differences.
- [x] T015 Implement persistent queue/claims, worker lifecycle, upload cleanup, progress, deadlines and retry.
- [x] T016 Build ingestion graph with durable checkpoints, cache reuse, bounded upserts and visibility verification.
- [x] T017 Verify crash/restart/replay before and after upserts and publication; assert no false ready state or duplicate dispatch.

Exit: upload-to-ready and recovery scenarios pass for both strategies, including provider failure and partial indexing.

## I05 Research

- [x] T018 Port BM25, RRF, dense filters, diversity, rerank fallback and parent expansion using frozen ranking fixtures.
- [x] T019 Port query policy, follow-ups, deterministic cash-flow/arithmetic and period safeguards.
- [x] T020 Build research graph, prompt files, output validation and one-repair limit.
- [x] T021 Verify citations, chart grounding, arithmetic, missing coverage, invalid model output and two-chat limit.
- [x] T022 Run existing browser workflows against Python: upload, polling, provider switch, scope selection, research, sources and retry.

Exit: all four retrieval conditions and financial safeguards pass; reviewed behavioral differences are listed.

## I06 Evaluation and operations

- [x] T023 Port all inventoried backend CLI workflows, keeping dry-run defaults and shared services.
- [x] T024 Port metric/scoring/report semantics, failed-trial handling, provenance and manual-review state.
- [x] T025 Port freshness, operational status, project export and backup/restore; verify fixture round trips.
- [x] T026 Complete approved live comparison or record it as pending with exact blockers; never mark pending quality evidence passed.

Exit: CLI coverage is complete, deterministic reports match expectations and live evidence status is explicit.

## I07 Deployment and cutover

- [ ] T027 Update Replit build/start, environment documentation and local Python run instructions; verify persistent storage.
- [x] T028 Rehearse quiescent backup, dry-run import, artifact readback and rollback using separate runtime directories.
- [ ] T029 Run release checks and review requirement-to-evidence coverage, live results and unresolved deviations.
- [ ] T030 After release review, switch deployment to Python; retain the legacy rollback path and retire Node backend commands deliberately.

Exit: every release gate is satisfied and rollback is demonstrated. Production cutover remains pending while required live evidence or storage durability is unresolved.

## Evidence and handoff log

| Increment/task | Owner/date | Verification command or artifact | Result/deviation | Next action |
| --- | --- | --- | --- | --- |
| Documentation draft | 2026-10-21 | Spec, plan and tracker created | Ready for scope review; no implementation run | Review decisions D01-D07 and I01 scope |
| I01 / T001-T005 | Codex / 2026-10-21 | [Baseline evidence](baseline/README.md) | 40 JS + 16 Python tests and browser checks passed; HTTP/domain snapshots reproduce. PDF golden fixture deferred to T013; D07 live budget pending. | See completed I02-I06 below |
| I02-I06 / T006-T026 | Codex / 2026-10-21 | [Migration verification](../../docs/PYTHON_MIGRATION.md), backend/tests, lockfile | Python API/workflows/tools implemented; 31 Python tests, 40 legacy Node tests and Python browser checks passed; 16 preparation tests passed at baseline. Corpus and recovery readback verified. Live runs explicitly deferred by user. | Hosted release gates only |
| I07 / T028 | Codex / 2026-10-21 | artifacts/migration/runtime-rehearsal.json | 2 jobs / 532 artifacts backed up, restored and imported; original runtime retained. Clean-environment startup passed. | Verify hosted durability before deployment |

## Remaining release work

- T027: Python hosting configuration and local persistent runtime are verified. Replit disk durability and Linux/Python 3.12 execution remain unverified.
- T029: Offline release checks and requirement coverage are recorded in [migration verification](../../docs/PYTHON_MIGRATION.md). Live provider acceptance is deferred by the user's explicit offline-only choice; no quality score is claimed.
- T030: Local startup now uses Python, the local runtime was imported, and hosting configuration is prepared. No hosted deployment was performed. Retain the legacy rollback path until production release gates are satisfied.
- T026 is checked because the task explicitly permits recording pending live evidence; it does not mean live comparison passed.

## Change request template

- Change and reason:
- Affected requirement IDs and increments:
- API/data compatibility impact:
- Acceptance criteria and verification:
- Review decision/date:

Subsequent latency, clarity and correctness work is tracked in [backend improvements](../002-backend-improvements/tasks.md). Its offline completion does not change the pending I07 hosted release gates.
