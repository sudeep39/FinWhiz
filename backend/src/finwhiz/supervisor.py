"""Lifecycle of the single isolated ingestion worker."""

import asyncio
import logging
import multiprocessing
import time

logger = logging.getLogger(__name__)


async def supervise(jobs, store, settings, worker_target, processing_timeout_seconds):
    process, job = None, None
    try:
        while True:
            job = await asyncio.to_thread(jobs.claim)
            if not job:
                await asyncio.sleep(0.3)
                continue
            process = multiprocessing.get_context("spawn").Process(
                target=worker_target,
                args=(str(store.directory), job, settings.model_dump(mode="json")),
            )
            try:
                process.start()
            except Exception as error:
                logger.error("worker start failed job=%s type=%s", job["id"], type(error).__name__)
                await asyncio.to_thread(
                    jobs.update,
                    job["id"],
                    status="failed",
                    error="Processing worker could not start. Retry this job.",
                )
                process.close()
                process = None
                await asyncio.sleep(0.3)
                continue
            started = time.monotonic()
            while process.is_alive():
                if time.monotonic() - started > processing_timeout_seconds:
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
                    if process.is_alive():
                        process.kill()
                    await asyncio.to_thread(
                        jobs.update,
                        job["id"],
                        status="failed",
                        error="Processing exceeded 30 minutes. Retry using a smaller batch.",
                    )
                    break
                await asyncio.sleep(0.2)
            await asyncio.to_thread(process.join)
            current = next(j for j in (await asyncio.to_thread(jobs.all)) if j["id"] == job["id"])
            if current["status"] == "processing":
                await asyncio.to_thread(
                    jobs.update,
                    job["id"],
                    status="failed",
                    error="Processing worker stopped unexpectedly. Retry to resume cached work.",
                )
            process.close()
            process = None
    finally:
        if process:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join)
            process.close()
            if (
                job
                and next(j for j in (await asyncio.to_thread(jobs.all)) if j["id"] == job["id"])[
                    "status"
                ]
                == "processing"
            ):
                await asyncio.to_thread(
                    jobs.update, job["id"], status="queued", detail="Resuming after server restart"
                )
