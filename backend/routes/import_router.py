"""Import API — fire-and-forget image/text ingestion with SSE progress streaming."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend import llm_client
from backend.config import settings
from backend.pipeline.import_pipeline import process_image_import, process_text_import

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# In-process job store
# Job entries: {status, result, error, created_at, filename}
# status: "pending" | "running" | "complete" | "error"
# ---------------------------------------------------------------------------

_JOBS: dict[str, dict[str, Any]] = {}

_SSE_POLL_INTERVAL_S = 1.5
_JOB_TTL_S = 3600  # Jobs older than 1 hour are reaped on the next status check.


def _new_job(filename: str) -> str:
    """Create a new job entry and return its UUID."""
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": time.time(),
        "filename": filename,
    }
    return job_id


def _expire_old_jobs() -> None:
    """Remove job entries older than _JOB_TTL_S to prevent unbounded memory growth."""
    cutoff = time.time() - _JOB_TTL_S
    stale = [jid for jid, j in _JOBS.items() if j["created_at"] < cutoff]
    for jid in stale:
        del _JOBS[jid]
    if stale:
        logger.debug("import job store → expired %d old job(s)", len(stale))


# ---------------------------------------------------------------------------
# Background task wrappers
# ---------------------------------------------------------------------------


async def _run_image_job(
    job_id: str,
    file_bytes: bytes,
    filename: str,
    entity_id: str,
) -> None:
    """Execute process_image_import, updating the job store on completion or failure."""
    _JOBS[job_id]["status"] = "running"
    try:
        result = await process_image_import(
            file_bytes=file_bytes,
            filename=filename,
            entity_id=entity_id,
            world_state_path=settings.world_state_path,
            lancedb_path=settings.lancedb_path,
            llm_client=llm_client,
        )
        _JOBS[job_id]["status"] = "complete"
        _JOBS[job_id]["result"] = result
        logger.info("import image job %s complete → entity=%s", job_id, entity_id)
    except Exception as exc:
        _JOBS[job_id]["status"] = "error"
        _JOBS[job_id]["error"] = str(exc)
        logger.error("import image job %s failed: %s", job_id, exc)


async def _run_text_job(
    job_id: str,
    file_bytes: bytes,
    filename: str,
) -> None:
    """Execute process_text_import, updating the job store on completion or failure."""
    _JOBS[job_id]["status"] = "running"
    try:
        result = await process_text_import(
            file_bytes=file_bytes,
            filename=filename,
            world_state_path=settings.world_state_path,
            lancedb_path=settings.lancedb_path,
            llm_client=llm_client,
        )
        _JOBS[job_id]["status"] = "complete"
        _JOBS[job_id]["result"] = result
        logger.info("import text job %s complete → %s chunks", job_id, result.get("chunks"))
    except Exception as exc:
        _JOBS[job_id]["status"] = "error"
        _JOBS[job_id]["error"] = str(exc)
        logger.error("import text job %s failed: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class JobAccepted(BaseModel):
    """202 response body for a newly queued import job."""

    job_id: str
    status: str = "pending"
    filename: str


class JobStatus(BaseModel):
    """Polling response for GET /import/status/{job_id}."""

    job_id: str
    status: str
    filename: str
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


async def _sse_generator(job_id: str) -> AsyncGenerator[str, None]:
    """Yield Server-Sent Events for *job_id* every _SSE_POLL_INTERVAL_S seconds.

    Emits a ``data: <json>\\n\\n`` frame on each tick.  Terminates automatically
    when the job reaches ``complete`` or ``error`` status, or if the job_id is
    not found.  The client may close the connection at any time without error.
    """
    while True:
        job = _JOBS.get(job_id)
        if job is None:
            payload = json.dumps({"job_id": job_id, "status": "not_found", "error": "Job not found."})
            yield f"data: {payload}\n\n"
            return

        payload = json.dumps(
            {
                "job_id": job_id,
                "status": job["status"],
                "filename": job["filename"],
                "result": job["result"],
                "error": job["error"],
            }
        )
        yield f"data: {payload}\n\n"

        if job["status"] in ("complete", "error"):
            return

        await asyncio.sleep(_SSE_POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/image", response_model=JobAccepted, status_code=202)
async def upload_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Image file (JPEG, PNG, WebP, GIF)."),
    entity_id: str = Form(
        ...,
        description="Graph node ID this image depicts, e.g. char_theron.",
        pattern=r"^(char|loc|item|org|event|concept)_[a-z0-9_]+$",
    ),
) -> JobAccepted:
    """Queue an image for vision-based description and LanceDB indexing.

    Returns 202 immediately; the vision inference runs in the background
    (expect 60–240s on CPU-only hardware).  Poll /import/status/{job_id}
    or subscribe to /import/stream/{job_id} for progress.
    """
    _expire_old_jobs()
    filename = file.filename or "unknown.jpg"
    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    job_id = _new_job(filename)
    background_tasks.add_task(_run_image_job, job_id, file_bytes, filename, entity_id)
    logger.info("import /image → queued job %s (entity=%s, file=%s)", job_id, entity_id, filename)

    return JobAccepted(job_id=job_id, filename=filename)


@router.post("/text", response_model=JobAccepted, status_code=202)
async def upload_text(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Plain text or Markdown lore document."),
) -> JobAccepted:
    """Queue a text/markdown document for chunked embedding and LanceDB indexing.

    The document is split at paragraph boundaries into ≤4 000-character chunks,
    each embedded independently using nomic-embed-text.  A concept_ node is
    added to world_state.json so the document appears in the graph view.
    """
    _expire_old_jobs()
    filename = file.filename or "unknown.txt"
    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    job_id = _new_job(filename)
    background_tasks.add_task(_run_text_job, job_id, file_bytes, filename)
    logger.info("import /text → queued job %s (file=%s, bytes=%d)", job_id, filename, len(file_bytes))

    return JobAccepted(job_id=job_id, filename=filename)


@router.get("/status/{job_id}", response_model=JobStatus)
async def job_status(job_id: str) -> JobStatus:
    """Return the current status of an import job by ID.

    Suitable for simple polling when SSE is not available.
    Returns 404 if the job_id is unknown or has expired (TTL = 1 hour).
    """
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return JobStatus(
        job_id=job_id,
        status=job["status"],
        filename=job["filename"],
        result=job["result"],
        error=job["error"],
        created_at=job["created_at"],
    )


@router.get("/stream/{job_id}")
async def stream_job(job_id: str) -> StreamingResponse:
    """Subscribe to Server-Sent Events for an import job.

    Emits a ``data: <json>\\n\\n`` frame every 1.5 seconds containing the full
    job status payload.  The stream terminates automatically when the job
    reaches ``complete`` or ``error``.

    Expected client usage (JavaScript):
        const es = new EventSource('/import/stream/<job_id>');
        es.onmessage = e => { const job = JSON.parse(e.data); ... };
    """
    if job_id not in _JOBS:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return StreamingResponse(
        _sse_generator(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx proxy buffering for SSE.
        },
    )
