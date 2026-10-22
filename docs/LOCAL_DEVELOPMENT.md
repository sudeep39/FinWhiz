# Run the backend and frontend separately

## One-command restart on Windows

From the project directory, run:

```powershell
.\restart.cmd
```

This stops servers previously launched by this helper, starts Python on 8000 and the frontend on 5173 in the background, and waits for both health checks. Open http://127.0.0.1:5173. It works from another directory too when invoked by its full path. The wrapper invokes PowerShell with a process-local execution policy; it does not change your system policy.

```powershell
.\restart.cmd -Stop
.\restart.cmd -BackendPort 8001 -FrontendPort 5174
.\restart.cmd -BackendUrl https://YOUR-APP.replit.app
Get-Content .cache\dev-servers\backend.error.log -Tail 40 -Wait
```

Logs are saved in `.cache/dev-servers/` and replaced on each restart. If you previously started servers manually, stop those once with Ctrl+C before using the helper. It refuses to stop untracked port owners. Restart stops managed process trees immediately, so let active research/uploads finish first; interrupted ingestion is recovered by the backend on startup. Closing the launcher terminal leaves the background servers running; use `-Stop` to shut them down.

With `-BackendUrl`, or a persisted `FINWHIZ_BACKEND_URL` in `.env`, the helper starts only the frontend and forwards public UI assets and API requests to that backend. This workspace is configured to use Replit by default. See [shared library setup](SHARED_LIBRARY.md). The separate local-backend instructions below apply only to an explicitly isolated development configuration with the shared URL cleared; the old local registry now references removed vector namespaces and must not be used as the production library.

Use two PowerShell terminals. Keep both commands running. The frontend is plain HTML/CSS/JavaScript; its development server forwards `/api/*` to Python, so no CORS changes are needed.

## Terminal 1: Python backend on port 8000

```powershell
cd D:\Data\Sudeep-C\code\ai\finwhiz
.\.venv\Scripts\python.exe run.py serve --host 127.0.0.1 --port 8000
```

Wait for Uvicorn's `Application startup complete` message. Check http://127.0.0.1:8000/api/health. This command loads the root `.env` and uses its configured runtime directory. It does not require virtual environment activation.

For a fresh checkout only, create the environment and install dependencies before starting:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
```

The current workspace already has a virtual environment. Configure provider credentials and the migrated runtime location in `.env`; do not overwrite an existing `.env` with the example file.

## Terminal 2: frontend on port 5173

```powershell
cd D:\Data\Sudeep-C\code\ai\finwhiz
node scripts/frontend-server.mjs
```

Alternatively, use `npm.cmd run dev:frontend`. This helper needs Node 22 or newer and no npm package installation. Open http://127.0.0.1:5173 in your browser. Its proxied health endpoint is http://127.0.0.1:5173/api/health. Refresh after frontend edits; there is no automatic reload.

The request flow is browser → frontend 5173 → Python API 8000 → providers. The development server only exposes the five public frontend assets and API routes, not project files or `.env`.

If changing ports, change the proxy target too:

```powershell
# Terminal 1
.\.venv\Scripts\python.exe run.py serve --host 127.0.0.1 --port 8001
# Terminal 2
node scripts/frontend-server.mjs --port=5174 --backend=http://127.0.0.1:8001
```

## Troubleshooting

- **Address already in use / WinError 10048 / EADDRINUSE:** stop the previous server with Ctrl+C, or choose another port.
- **Runtime already owned/locked:** stop the previous Python backend (including an earlier `npm start`). Only one backend may own a runtime directory. Changing ports does not remove this restriction.
- **502 Cannot reach the Python backend:** check Terminal 1 and confirm the proxy target matches the backend port.
- **401 / workspace password prompt:** enter the password configured as `APP_PASSWORD` in `.env`.
- **ModuleNotFoundError:** run the dependency install above using the same `.venv` Python executable used to start the backend.
- **PowerShell blocks npm.ps1:** use `npm.cmd` or the direct `node` command above.
- **Health succeeds but research fails:** inspect backend logs and provider settings in `.env`. Health does not verify paid provider connectivity.

Stop each server with Ctrl+C in its own terminal. `python -m http.server` alone does not provide the required API proxy. `npm start` starts the Python server, not this separate frontend server.

For the existing single-server mode, run `.\.venv\Scripts\python.exe run.py serve` and open http://localhost:3100 (unless `PORT` overrides it).
