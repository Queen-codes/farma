"""FARMA workflow endpoints for simulation and human-in-the-loop resume.

Key responsibilities:
- Start farmer pipeline runs as asynchronous jobs.
- Resume paused jobs waiting for human escalation input.
- Emit stable job/event contracts for frontend progress tracking.

Used by:
- `app.main` via router inclusion.
- Frontend tools initiating farmer simulations and escalation responses.
- `app.workflows.runner` for actual workflow execution.

Assumptions:
- `job_store` is available for job/event persistence.
- `run_farma_job` and `resume_farma_job` implement business workflow logic.
- API token auth is enabled for operational access in protected deployments.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.helpers.runtime import spawn_bg_task, utcnow_naive
from app.api.helpers.security import require_api_auth
from app.api.schemas import JobResponse, JobStatus, ResumeRequest
from app.utils.job_store import job_store

router = APIRouter(
    prefix="/api",
    tags=["Farmer"],
    dependencies=[Depends(require_api_auth)],
)


@router.post("/farmer/simulate", response_model=JobResponse)
async def simulate_farmer_pipeline(
    request: Request, phone: str, message: str, use_aegis_context: bool = True
) -> JobResponse:
    """Create a new FARMA simulation job from SMS-style query inputs.

    Request:
        Query parameters:
        - `phone` (required): Farmer phone number / conversation thread ID.
        - `message` (required): Incoming farmer SMS text.

    Response:
        `JobResponse` with a new `job_id` and initial `running` status.

    Status Codes:
        200: Job accepted and background task scheduled.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Not idempotent. Each call creates a new job ID and background run.

    Args:
        request: FastAPI request object used to access app state for background tasks.
        phone: Farmer phone number used as workflow thread identifier.
        message: SMS text that seeds the workflow initial state.

    Returns:
        JobResponse: Newly created job contract for client polling.

    Raises:
        Does not raise intentionally in normal control flow; runtime failures
        during background execution are recorded into job status/events.

    Side Effects:
        Writes a job record and events to `job_store`.
        Spawns an asynchronous background workflow task.

    Latency:
        Fast request path; long-running work happens asynchronously.
    """
    job_id = f"FARMA-{uuid.uuid4().hex[:8].upper()}"
    await job_store.create_job(
        job_id,
        "farmer_simulation",
        metadata={"phone": phone, "use_aegis_context": bool(use_aegis_context)},
    )

    sms_input = {
        "input_type": "sms",
        "phone": phone,
        "message": message,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
        "use_aegis_context": bool(use_aegis_context),
    }

    async def run_pipeline() -> None:
        """Execute the FARMA workflow and persist background failures.

        Args:
            None.

        Returns:
            None.

        Raises:
            Does not raise intentionally; exceptions are converted into failed
            job status/events.

        Side Effects:
            Calls `run_farma_job` and writes updates/events to `job_store`.

        Latency:
            Potentially slow due to end-to-end workflow execution.
        """
        try:
            from app.workflows.runner import run_farma_job

            await run_farma_job(
                job_id=job_id,
                initial_state=sms_input,
                thread_id=phone,
                emit_job_events=True,
            )
        except Exception as e:
            # Fail in job contract so the UI never hangs on running.
            await job_store.update_job(
                job_id,
                status="failed",
                result={"error": str(e)},
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="pipeline_failed",
                status="failed",
                step="error",
                message=str(e),
            )

    spawn_bg_task(request.app, run_pipeline())

    return JobResponse(
        job_id=job_id,
        job_type="farmer_simulation",
        status=JobStatus.RUNNING,
        started_at=utcnow_naive(),
        completed_at=None,
        result=None,
    )


@router.post("/farmer/{job_id}/resume", response_model=JobResponse)
async def resume_farmer_pipeline(
    job_id: str, body: ResumeRequest, request: Request
) -> JobResponse:
    """Resume a job paused for human escalation input.

    Request:
        Path parameter:
        - `job_id`: Existing FARMA job awaiting human input.
        JSON body:
        - `response_text`: Agent message that unblocks the workflow interrupt.

    Response:
        `JobResponse` showing the resumed job as `running`.

    Status Codes:
        200: Resume accepted and background continuation scheduled.
        400: Job exists but is not resumable (wrong status or missing metadata).
        404: Job ID is unknown.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Not idempotent. Repeated calls can enqueue multiple resume attempts.

    Args:
        job_id: Target job identifier to resume.
        body: Human response payload passed back into workflow interrupt.
        request: FastAPI request object used to schedule background continuation.

    Returns:
        JobResponse: Updated job contract indicating execution has resumed.

    Raises:
        HTTPException: 404 for unknown job, 400 for non-resumable jobs.

    Side Effects:
        Reads and mutates job state in `job_store`.
        Spawns background continuation of the FARMA workflow.

    Latency:
        Fast request path; actual pipeline continuation runs asynchronously.
    """
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    status = (job.get("status") or "").lower()
    if status != "awaiting_human":
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is not awaiting human input (status={status})",
        )

    phone = (job.get("metadata") or {}).get("phone")
    if not phone:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} has no phone in metadata — cannot resume",
        )

    async def _bg() -> None:
        """Resume interrupted workflow and normalize failure handling.

        Args:
            None.

        Returns:
            None.

        Raises:
            Does not raise intentionally; failures are written to `job_store`.

        Side Effects:
            Calls `resume_farma_job` and mutates job status/events.

        Latency:
            Depends on remaining workflow path after human escalation.
        """
        try:
            from app.workflows.runner import resume_farma_job

            await resume_farma_job(
                job_id=job_id,
                thread_id=phone,
                human_response=body.response_text,
                emit_job_events=True,
            )
        except Exception as e:
            await job_store.update_job(
                job_id,
                status="failed",
                result={"error": str(e)},
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="pipeline_failed",
                status="failed",
                step="error",
                message=str(e),
            )

    spawn_bg_task(request.app, _bg())

    return JobResponse(
        job_id=job_id,
        job_type=job.get("job_type", "farmer_simulation"),
        status=JobStatus.RUNNING,
        started_at=job.get("started_at"),
        completed_at=None,
        result=None,
    )
