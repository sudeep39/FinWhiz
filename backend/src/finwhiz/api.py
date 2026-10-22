"""Compatible browser API; application services own the financial behavior."""

import asyncio
import hmac
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import Response

from .config import ROOT, AppError, Settings, job_matches
from .documents import iso_date
from .ingestion import worker_main
from .providers import Providers, ProviderPool
from .supervisor import supervise
from .telemetry import ResearchTrace
from .http_helpers import response, read_json, BodyLimit
from .research import research
from .scope import resolve_scope
from .fiscal import catalog_documents
from .storage import Jobs, Storage, runtime_lock

logger = logging.getLogger(__name__)
SECURITY = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
}
STATIC = {
    "/": ("index.html", "text/html"),
    "/app.mjs": ("app.mjs", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/data/experiment-filings.json": ("data/experiment-filings.json", "application/json"),
    "/data/experiment-filings.csv": ("data/experiment-filings.csv", "text/csv"),
}


def create_app(
    settings=None,
    *,
    disable_workers=False,
    provider_factory=Providers,
    worker_target=worker_main,
    processing_timeout_seconds=1800,
):
    settings = settings or Settings(_env_file=ROOT / ".env")
    if settings.finwhiz_backend_url:
        raise AppError('A shared backend is configured. Use restart.cmd to start its local frontend instead of creating another library owner.')
    store = Storage(settings.finwhiz_data_dir)
    if store.path("state").exists() and not (store.directory / "jobs.sqlite3").exists():
        raise AppError(
            "Legacy runtime detected. Stop Node and use run.py migrate --source=LEGACY --to=NEW_DIRECTORY --apply, then set FINWHIZ_DATA_DIR to the new directory."
        )
    jobs = Jobs(store)
    auth_limits, chat_limits = {}, {}
    active_chats = 0
    pool = ProviderPool(settings, provider_factory)
    pending_research = set()

    @asynccontextmanager
    async def lifespan(app):
        with runtime_lock(store.directory):
            await asyncio.to_thread(jobs.recover)
            task = (
                None
                if disable_workers
                else asyncio.create_task(
                    supervise(jobs, store, settings, worker_target, processing_timeout_seconds)
                )
            )
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if pending_research:
                    await asyncio.gather(*pending_research, return_exceptions=True)
                if app.state.maintenance_task:
                    await asyncio.gather(app.state.maintenance_task, return_exceptions=True)
                pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    app.state.store, app.state.jobs = store, jobs
    app.state.maintenance = False
    app.state.active_mutations = 0
    app.state.maintenance_task = None

    @app.middleware("http")
    async def security(request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.monotonic()
        selected = False
        tracked_mutation = False
        try:
            request.state.ai = settings.provider(request.headers.get("x-ai-provider"))
            selected = True
            if request.url.path.startswith("/api/") and request.url.path != "/api/health":
                if (
                    settings.replit_deployment or settings.node_env == "production"
                ) and not settings.app_password:
                    raise AppError(
                        "Set APP_PASSWORD in deployment secrets before enabling this shared workspace.",
                        503,
                    )
                client = request.client.host if request.client else "local"
                current = time.monotonic()
                for key in list(auth_limits):
                    if current - auth_limits[key][0] > 60:
                        del auth_limits[key]
                if auth_limits.get(client, (0, 0))[1] >= 20:
                    raise AppError("Too many password attempts. Wait a minute.", 429)
                provided = re.sub(r"^Bearer ", "", request.headers.get("authorization", ""))
                if settings.app_password and not hmac.compare_digest(
                    provided.encode(), settings.app_password.encode()
                ):
                    start, count = auth_limits.get(client, (current, 0))
                    auth_limits[client] = start, count + 1
                    raise AppError("Enter your workspace password.", 401)
                origin = request.headers.get("origin")
                if (
                    request.method == "POST"
                    and origin
                    and urlparse(origin).netloc
                    not in (request.headers.get("host"), request.headers.get("x-forwarded-host"))
                ):
                    raise AppError("Origin not allowed.", 403)
            if request.method == 'POST' and request.url.path.startswith('/api/'):
                if app.state.maintenance:
                    raise AppError('Library maintenance is running. Retry when it finishes.', 409)
                if not re.fullmatch(r'/api/jobs/[a-f0-9-]+/deduplicate', request.url.path):
                    app.state.active_mutations += 1
                    tracked_mutation = True
            result = await call_next(request)
        except AppError as error:
            result = response({"error": str(error)}, error.status)
        except Exception as error:
            logger.error("request failed id=%s type=%s", request_id, type(error).__name__)
            result = response({"error": "The request could not be completed."}, 502)
        finally:
            if tracked_mutation:
                app.state.active_mutations -= 1
        result.headers["X-Request-ID"] = request_id
        if selected:
            result.headers.update(SECURITY)
        logger.info(
            "request id=%s path=%s status=%s duration_ms=%d",
            request_id,
            request.url.path,
            result.status_code,
            (time.monotonic() - started) * 1000,
        )
        return result

    @app.exception_handler(AppError)
    async def application_error(request, error):
        return response({"error": str(error)}, error.status)

    async def chat_route(request, path):
        nonlocal active_chats
        config = request.state.ai
        if path in ("/api/chat", "/api/scope") and request.method == "POST":
            client = request.client.host if request.client else "local"
            current = time.monotonic()
            for key in list(chat_limits):
                if current - chat_limits[key][0] > 60:
                    del chat_limits[key]
            start, count = chat_limits.get(client, (current, 0))
            if path == '/api/chat':
                chat_limits[client] = start, count + 1
            if path == '/api/chat' and count + 1 > 12:
                raise AppError("Please wait a moment before sending another question.", 429)
            body = await read_json(request)
            question = body.get("question")
            if not isinstance(question, str) or not question.strip() or len(question) > 3000:
                raise AppError("Enter a question of up to 3,000 characters.", 400)
            tickers = body.get("tickers")
            if (
                not isinstance(tickers, list)
                or not 1 <= len(tickers) <= 4
                or any(
                    not isinstance(t, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", t)
                    for t in tickers
                )
            ):
                raise AppError("Select at least one company.", 400)
            strategy = body.get("strategy") or "fixed"
            if strategy not in ("fixed", "semantic"):
                raise AppError("Invalid retrieval strategy.", 400)
            periods = body.get("periods") or []
            if (
                not isinstance(periods, list)
                or len(periods) > 32
                or any(not iso_date(p) for p in periods)
            ):
                raise AppError("Invalid period selection.", 400)
            ready = [
                j
                for j in (await asyncio.to_thread(jobs.all))
                if j["status"] == "ready" and job_matches(j, config)
            ]
            if any(
                not any(any(d["ticker"] == ticker for d in j.get("documents", [])) for j in ready)
                for ticker in tickers
            ):
                raise AppError(
                    "The selected company is not ready yet. Finish loading its filings first.", 409
                )
            history = body.get("history", [])
            history = (
                [
                    dict(role=m["role"], content=m["content"][:1500], scope=m.get('scope'))
                    for m in history[-4:]
                    if isinstance(m, dict)
                    and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)
                ]
                if isinstance(history, list)
                else []
            )
            scoped_jobs = [job for job in ready if job.get('namespaces', {}).get(strategy)]
            documents = await asyncio.to_thread(catalog_documents, scoped_jobs, store)
            resolved = resolve_scope(question.strip(), documents, tickers, periods, history)
            if path == '/api/scope':
                return response(resolved)
            if resolved['status'] != 'resolved':
                from .research import empty_answer
                return response({**empty_answer(resolved['status'], resolved['message']), 'scope': resolved})
            if active_chats >= 2:
                raise AppError("Two questions are already processing. Please retry shortly.", 429)
            active_chats += 1
            context = ResearchTrace(request.state.request_id, settings.chat_timeout_seconds)

            async def execute_research():
                nonlocal active_chats
                try:
                    provider = pool.get(config)
                    return await asyncio.to_thread(
                        research,
                        store,
                        ready,
                        provider,
                        question.strip(),
                        tickers,
                        periods,
                        strategy,
                        body.get("rerank") is not False,
                        history,
                        context=context,
                        scope_result=resolved,
                    )
                finally:
                    active_chats -= 1

            task = asyncio.create_task(execute_research())
            pending_research.add(task)

            def finished(completed):
                pending_research.discard(completed)
                if not completed.cancelled():
                    completed.exception()  # consume exceptions even after a client timeout

            task.add_done_callback(finished)
            try:
                result = await asyncio.wait_for(asyncio.shield(task), settings.chat_timeout_seconds)
                return response(result)
            except (TimeoutError, asyncio.CancelledError) as error:
                context.cancelled.set()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise AppError(
                    "Research exceeded its time budget. Select fewer periods or retry.", 504
                ) from None
        return None

    async def deduplicate_route(request, path):
        match = re.fullmatch(r'/api/jobs/([a-f0-9-]+)/deduplicate', path)
        if not match or request.method != 'POST':
            return None
        if not settings.app_password:
            raise AppError('Set APP_PASSWORD before using the maintenance API.', 403)
        from .deduplication import preview, public_plan, apply
        body = await read_json(request)
        keep_id = body.get('keepJobId')
        if not isinstance(keep_id, str) or not re.fullmatch(r'[a-f0-9-]{1,64}', keep_id) or type(body.get('apply', False)) is not bool:
            raise AppError('Supply keepJobId and an optional boolean apply flag.', 400)
        plan = await asyncio.to_thread(preview, jobs, match[1], keep_id)
        if not body.get('apply', False):
            return response(public_plan(plan))
        if body.get('confirmToken') != plan['confirmToken']:
            raise AppError('Preview this merge and supply its current confirmToken before applying.', 409)
        if app.state.active_mutations or active_chats or app.state.maintenance:
            raise AppError('Wait for active uploads, research or maintenance to finish.', 409)
        if request.state.ai.provider != plan['provider'] or request.state.ai.indexName != plan['index']:
            raise AppError('Select the duplicate job\'s AI provider before applying.', 409)
        app.state.maintenance = True
        async def execute():
            try:
                provider = pool.get(request.state.ai)
                return await asyncio.to_thread(apply, jobs, plan, provider)
            finally:
                app.state.maintenance = False
        task = asyncio.create_task(execute())
        app.state.maintenance_task = task
        task.add_done_callback(lambda completed: None if completed.cancelled() else completed.exception())
        return response(await asyncio.shield(task))

    async def dispatch(request: Request, path: str):
        path = "/" + path
        maintenance_result = await deduplicate_route(request, path)
        if maintenance_result is not None:
            return maintenance_result
        from .workspace_routes import workspace
        from .document_routes import documents

        for handler in (workspace, documents):
            result = await handler(request, path, settings=settings, store=store, jobs=jobs)
            if result is not None:
                return result
        result = await chat_route(request, path)
        if result is not None:
            return result
        if path.startswith("/api/"):
            return response({"error": "Endpoint not found."}, 404)
        if path in STATIC and request.method == "GET":
            file, content_type = STATIC[path]
            return Response(
                (ROOT / file).read_bytes(),
                headers={
                    "Content-Type": content_type + "; charset=utf-8",
                    "Cache-Control": "no-store",
                },
            )
        return Response("Not found", status_code=404)

    from .routes import register_routes

    register_routes(app, dispatch)
    return app
