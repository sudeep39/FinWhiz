"""Bounded remote calls with request-scoped telemetry and retry budgets."""

import time
import httpx
from .config import AppError
from .telemetry import remaining, span, current_trace


class JsonTransport:
    """Bounded JSON HTTP adapter owned by Providers.transport.

    Providers.request delegates embedding and Pinecone HTTP calls here.
    GenerationClient uses LangChain separately with the same HTTP client."""
    def __init__(self, client, settings):
        """Bind a shared HTTP client and server timeout/retry settings.

        Called by Providers.__init__; no request is sent during construction."""
        self.client, self.settings = client, settings

    def request(self, url, body=None, headers=None, method="POST", attempts=None):
        """Send JSON and return the decoded successful response within budgets.

        Called through Providers.request for embeddings and vector operations.
        url/body/headers/method define the request; attempts optionally overrides
        retry count. Retries transient statuses/transport errors, never billing
        exhaustion, and shares research/agent deadlines. Raises AppError on
        classified failures; the supplied method/path may perform remote writes."""
        operation = (
            "embedding"
            if url.endswith("/embeddings")
            else "index_discovery" if "/indexes/" in url else "vector_" + url.rsplit("/", 1)[-1]
        )
        limit = (
            self.settings.embedding_timeout_seconds
            if operation == "embedding"
            else self.settings.vector_timeout_seconds
        )
        attempts = attempts or self.settings.provider_attempts
        started = time.monotonic()

        def time_left():
            left = limit - (time.monotonic() - started)
            if left <= 0:
                raise AppError(f"{operation} timed out. Retry or select fewer periods.", 504)
            return remaining(left)

        for attempt in range(attempts):
            seconds = time_left()
            response = None
            try:
                with span(operation, attempt=attempt + 1):
                    response = self.client.request(
                        method,
                        url,
                        json=body,
                        headers=headers,
                        timeout=httpx.Timeout(
                            seconds, connect=min(3, seconds), pool=min(2, seconds)
                        ),
                    )
                time_left()
                trace = current_trace.get()
                if trace:
                    trace.record(
                        "provider_response",
                        0,
                        operation=operation,
                        status=response.status_code,
                        providerRequestId=response.headers.get("x-request-id"),
                    )
            except httpx.TimeoutException:
                if attempt == attempts - 1:
                    raise AppError(
                        f"{operation} timed out. Retry or select fewer periods.", 504
                    ) from None
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise AppError("Provider connection failed. Retry the operation.") from None
            if response is not None:
                if response.is_success:
                    return response.json()
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                if isinstance(error, dict) and (
                    error.get("type") == "insufficient_quota"
                    or error.get("code")
                    in (
                        "insufficient_quota",
                        "credit_balance_exhausted",
                        "billing_hard_limit_reached",
                    )
                ):
                    raise AppError(
                        "Provider API quota is exhausted. Restore API credits or configure a key with available quota, then retry to resume cached work."
                    )
                if response.status_code not in (429, 500, 502, 503, 504):
                    raise AppError(
                        f"Provider returned HTTP {response.status_code}. Check server credentials, permissions, and quota."
                    )
            if attempt == attempts - 1:
                raise AppError("Provider temporarily unavailable.")
            delay = min(2**attempt, time_left())
            time.sleep(delay)
            time_left()
