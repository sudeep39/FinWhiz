"""Shared HTTP compatibility and bounded multipart parsing."""

import asyncio
import json
import re
import uuid
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from .config import AppError, digest
from .documents import validate_details


def response(value, status=200):
    """Build the UTF-8, no-store JSON response used by API area handlers."""
    return JSONResponse(
        value,
        status_code=status,
        headers={"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
    )


def public_job(job):
    """Return a browser-facing job with internal file keys/paths removed.

    Used by workspace/document routes; file entries expose only ID, name and
    size. This does not filter arbitrary extra fields on the job itself.
    """
    return {
        **{k: v for k, v in job.items() if k != "files"},
        "files": [{k: f[k] for k in ("id", "name", "size")} for f in job["files"]],
    }


async def read_json(request):
    """Read a route's JSON object with a 32,000-byte limit.

    Called by research and maintenance endpoints. Returns a dictionary or
    raises AppError with HTTP 400 for invalid JSON/object shape or 413 for size.
    """
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 32000:
            raise AppError("Request is too large.", 413)
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise AppError("Invalid JSON request.", 400) from None


async def receive_files(request, store):
    """Parse an upload batch and persist accepted files in the runtime store.

    Called by document_routes.documents after authentication. Validates batch
    metadata, filenames and size/count limits; returns a sorted list of file
    records, each with its details for job creation. Failed batches remove the
    blobs written so far. Unlike read_json, this function writes blobs.
    """
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise AppError("Use a multipart file upload.", 400)
    files = []
    try:
        async with request.form(max_files=32, max_fields=2, max_part_size=32000) as form:
            try:
                details = json.loads(form.get("details", "[]"))
            except (ValueError, TypeError):
                raise AppError("Invalid file details.", 400) from None
            if not validate_details(details):
                raise AppError("Invalid file details.", 400)
            seen, total = set(), 0
            for field, upload in form.multi_items():
                if not isinstance(upload, UploadFile):
                    continue
                name = re.sub(
                    r"[\x00-\x1f]", "", upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
                )[:180]
                match = re.fullmatch(r"file_(\d+)", field)
                index = int(match[1]) if match else -1
                if (
                    not re.search(r"\.(html?|pdf|txt)$", name, re.I)
                    or not 0 <= index <= 31
                    or index in seen
                ):
                    raise AppError("Choose HTML, PDF, or TXT files.", 400)
                seen.add(index)
                data = await upload.read(20 * 1024 * 1024 + 1)
                if len(data) > 20 * 1024 * 1024:
                    raise AppError("Each file must be smaller than 20 MB.", 400)
                total += len(data)
                if total > 100 * 1024 * 1024:
                    raise AppError("Each batch must be smaller than 100 MB.", 400)
                file_id = str(uuid.uuid4())
                key = f"upload/{file_id}"
                await asyncio.to_thread(store.put, key, data)
                files.append(
                    dict(
                        id=file_id,
                        key=key,
                        name=name,
                        size=len(data),
                        index=index,
                        details=details[index] if index < len(details) else {},
                        checksum=digest(data),
                    )
                )
        if not files:
            raise AppError("Select at least one readable file.", 400)
        return sorted(files, key=lambda f: f["index"])
    except BaseException as error:
        for file in files:
            store.remove(file["key"])
        if isinstance(error, AppError) or isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, HTTPException):
            detail = str(error.detail)
            if "Too many files" in detail or "Too many fields" in detail:
                raise AppError("Upload batch limit exceeded (maximum 32 files).", 400) from None
            if "maximum size" in detail:
                raise AppError("File details are too large.", 400) from None
        raise AppError("Invalid upload request.", 400) from None


class BodyLimit:
    """ASGI middleware limiting incoming HTTP bodies before upload parsing.

    Registered by api.create_app. FastAPI invokes __call__ for each request;
    individual JSON and multipart parsers apply their own smaller limits."""

    def __init__(self, app):
        """Keep the next ASGI application in the middleware chain.

        FastAPI/Starlette constructs this after api.create_app registers BodyLimit."""
        self.app = app

    async def __call__(self, scope, receive, send):
        """Wrap HTTP receive to count bytes before passing them to the application.

        Invoked by the ASGI server, not directly by a route. Raises HTTP 413 via
        AppError beyond the transport cap; non-HTTP scopes pass through unchanged.
        The receive/send callables follow the ASGI protocol."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        count = 0

        async def limited_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > 101 * 1024 * 1024:
                raise AppError("Each batch must be smaller than 100 MB.", 413)
            return message

        await self.app(scope, limited_receive, send)
