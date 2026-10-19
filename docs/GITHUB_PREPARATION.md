# Separate GitHub project

FinWhiz currently resides inside the shared `ai` workspace. Do not publish that parent folder. Generate a clean, separate root:

```sh
node scripts/export-project.mjs
```

The timestamped folder under `release/` is suitable for its own Git repository and root-level Replit import. It includes a SHA-256 source manifest. Downloaded SEC HTML, secrets, runtime uploads, caches, dependencies, screenshots and backups stay in the original workspace. Small SEC catalog/checksum metadata is retained for tests and provenance. The web app does not preload that catalog into Research. To reproduce downloads, configure the SEC user agent described in README and run the download scripts.

Review the export and run `npm ci`, `npm test`, and `npm run test:browser` (installed Edge required). Python tools are optional and require `requirements.txt`. No API keys are needed for offline tests. Do not copy `.env` into Git. No license has been chosen on your behalf.

When publication is requested, run the following **inside the exported folder**; these are future instructions, not actions already performed:

```sh
git init -b main
git add .
git commit -m "Initial FinWhiz financial document intelligence application"
gh repo create FinWhiz --private --source=. --remote=origin --push
```

Choose visibility deliberately. Replit can import the resulting GitHub repository directly; no manually pre-created FinWhiz project is necessary. Configure provider secrets and APP_PASSWORD in the imported app. Require a verified durable FINWHIZ_DATA_DIR before treating hosted uploads as persistent. Do not use Autoscale for this in-process queue.

The original-source backup is `.backups/20261019T005620Z/source-before-review.zip`; retain it outside the published repository. No unused source has been discarded in this review.
