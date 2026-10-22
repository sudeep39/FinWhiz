"""API areas with explicit routes; unknown methods retain legacy 404 behavior."""

from fastapi import APIRouter, Request


def register_routes(app, dispatch):
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    for area, paths in {
        "workspace": ["/api/health", "/api/state", "/api/operations"],
        "documents": ["/api/uploads", "/api/jobs/{job_id}/retry", "/api/jobs/{job_id}/deduplicate", "/api/sources/{source_id}"],
        "research": ["/api/chat", "/api/scope"],
    }.items():
        router = APIRouter(tags=[area])

        async def handle(request: Request):
            return await dispatch(request, request.url.path.lstrip("/"))

        for path in paths:
            router.add_api_route(path, handle, methods=methods)
        app.include_router(router)

    async def fallback(request: Request, path: str):
        return await dispatch(request, path)

    app.add_api_route("/{path:path}", fallback, methods=methods)
