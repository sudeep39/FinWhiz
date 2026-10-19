# FinWhiz on Replit

## Run

- Existing stack: JavaScript frontend, Node.js HTTP API, worker threads, and Pinecone. Keep the imported structure.
- Node.js 22 is required and configured.
- Use the **Start application** workflow (`PORT=5000 npm start`) or Replit's Run button.
- The preview serves on `0.0.0.0:5000`. Outside Replit, `npm start` defaults to port 3100.
- Install from the lockfile with `npm ci`.

## Credentials and data

- Add `OPENAI_API_KEY` and `PINECONE_API_KEY` through Replit Secrets for the default OpenAI provider. Alternatively configure `NEBIUS_API_KEY` and choose Nebius in the app.
- Set `APP_PASSWORD` through Secrets to protect the shared workspace. It is required in production; the existing API rejects production requests without it.
- The UI and health endpoint can start without provider keys, but uploads and AI research cannot run until keys are configured.
- OpenAI expects a dense/cosine Pinecone index with 1,536 dimensions (`PINECONE_INDEX`, default `finwhiz-filings-v1`). Nebius uses its own 4,096-dimensional index (`PINECONE_NEBIUS_INDEX`, default `finwhiz-nebius-v1`).
- Runtime files live in ignored `.runtime/` by default. Before publishing, verify durable storage and backups for `FINWHIZ_DATA_DIR`; Pinecone stores vectors, not the local upload/queue/cache files.
- No database migration or paid ingestion/evaluation is needed just to start the app.

## Checks

- `npm test` runs the Node test suite.
- `GET /api/health` returns `{"ok":true}`.
- The imported `npm run test:browser` script requires Microsoft Edge; it is not portable to this Linux environment without changes.
