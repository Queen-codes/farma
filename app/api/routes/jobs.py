"""Endpoints for querying asynchronous job status and event timelines.

Key responsibilities:
- Return normalized job status for long-running background workflows.
- Return ordered event streams used by clients for progress UIs.

Used by:
- `app.main` through router inclusion.
- Frontends polling background FARMA/AEGIS workflow progress.
- Other route modules that return job IDs to clients.

Assumptions:
- `job_store` persists jobs/events with expected schema keys.
- API auth dependency gates operational access in production.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.helpers.security import require_api_auth
from app.api.schemas import JobEventsResponse, JobResponse, JobStatus
from app.utils.job_store import job_store

router = APIRouter(
    prefix="/api/jobs",
    tags=["Jobs"],
    dependencies=[Depends(require_api_auth)],
)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """Return current status details for a specific background job.

    Request:
        Path parameter `job_id` is required.

    Response:
        `JobResponse` containing status, timing, and optional result payload.

    Status Codes:
        200: Job found and returned.
        404: Job ID does not exist in the store.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        job_id: Unique job identifier previously returned by a workflow endpoint.

    Returns:
        JobResponse: Normalized job status payload.

    Raises:
        HTTPException: 404 when the job cannot be found.

    Side Effects:
        Reads job data from the async job store.

    Latency:
        Typically fast; depends on backing store latency.
    """
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    status = job.get("status", "running")
    try:
        status_enum = JobStatus(status)
    except Exception:
        status_enum = JobStatus.RUNNING
    return JobResponse(
        job_id=job.get("job_id"),
        job_type=job.get("job_type", "unknown"),
        status=status_enum,
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        result=job.get("result"),
    )


@router.get("/{job_id}/events", response_model=JobEventsResponse)
async def get_job_events(job_id: str) -> JobEventsResponse:
    """Return the full event timeline recorded for a background job.

    Request:
        Path parameter `job_id` is required.

    Response:
        `JobEventsResponse` with the job ID and ordered event list.

    Status Codes:
        200: Event list returned (empty list is valid).

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        job_id: Unique job identifier whose events should be listed.

    Returns:
        JobEventsResponse: Job event history for UI progress display.

    Raises:
        Does not raise intentionally for missing jobs; unknown IDs return an
        empty event list from `job_store`.

    Side Effects:
        Reads event data from the async job store.

    Latency:
        Depends on event count and backing store latency.
    """
    events = await job_store.list_events(job_id)
    return JobEventsResponse(job_id=job_id, events=events)
