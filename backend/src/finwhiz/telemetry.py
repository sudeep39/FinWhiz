"""Request-scoped deadlines and stage measurements, shared safely with search threads."""

import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .config import AppError

logger = logging.getLogger(__name__)
current_trace = ContextVar("finwhiz_trace", default=None)


@dataclass
class ResearchTrace:
    request_id: str
    timeout: float
    started: float = field(default_factory=time.monotonic)
    timings: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def remaining(self):
        remaining = self.timeout - (time.monotonic() - self.started)
        if self.cancelled.is_set() or remaining <= 0:
            raise AppError("Research exceeded its time budget. Select fewer periods or retry.", 504)
        return remaining

    def record(self, stage, duration, **details):
        with self.lock:
            self.timings[stage] = round(self.timings.get(stage, 0) + duration, 2)
            self.events.append(dict(stage=stage, durationMs=round(duration, 2), **details))
        logger.info(
            "research request=%s stage=%s duration_ms=%.2f details=%s",
            self.request_id,
            stage,
            duration,
            details,
        )

    def metrics(self):
        with self.lock:
            return dict(
                requestId=self.request_id,
                stageTimingsMs=dict(self.timings),
                operations=list(self.events),
            )


def remaining(default):
    trace = current_trace.get()
    return min(default, trace.remaining()) if trace else default


@contextmanager
def span(stage, **details):
    trace = current_trace.get()
    if trace:
        trace.remaining()
    started = time.monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException as error:
        outcome = type(error).__name__
        raise
    finally:
        if trace:
            trace.record(stage, (time.monotonic() - started) * 1000, outcome=outcome, **details)
