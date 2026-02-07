"""System and operational endpoints for service metadata and health.

Key responsibilities:
- Expose a root metadata endpoint for quick service discovery.
- Expose health diagnostics including database connectivity and service config.
- Provide websocket access to the internal "thinking" event stream.

Used by:
- `app.main` via router inclusion.
- Frontend/system monitors that check readiness and subscribe to thinking events.

Assumptions:
- Database session factory is available for deep health checks.
- `thinking_bus` handles websocket fan-out and connection lifecycle.
"""

from __future__ import annotations

import os
from datetime import timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.schemas import HealthResponse
from app.utils.thinking_bus import thinking_bus

router = APIRouter(tags=["System"])


@router.get("/")
def root() -> dict[str, str]:
    """Return top-level API metadata.

    Request:
        No body, query, or path parameters.

    Response:
        JSON object with service name, version, status, docs URL, and
        short description.

    Status Codes:
        200: Metadata returned.

    Auth:
        No authentication is required.

    Idempotency:
        Fully idempotent read-only endpoint.

    Args:
        None.

    Returns:
        dict[str, str]: Static service metadata payload.

    Raises:
        Does not raise intentionally.

    Side Effects:
        None.

    Latency:
        Constant-time in-memory response.
    """
    return {
        "name": "Farma API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "description": "AI-powered agricultural assistance for Nigerian farmers",
    }


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Report health status for core API dependencies.

    Request:
        No body, query, or path parameters.

    Response:
        `HealthResponse` with overall status, DB connectivity state, version,
        and configured downstream service flags.

    Status Codes:
        200: Health response always returned; degraded DB states are encoded in
            payload instead of non-200 status codes.

    Auth:
        No authentication is required.

    Idempotency:
        Idempotent read-only endpoint.

    Args:
        None.

    Returns:
        HealthResponse: Service health snapshot at request time.

    Raises:
        Does not raise intentionally; DB probe errors are converted to a
        degraded payload.

    Side Effects:
        Performs a lightweight `SELECT 1` database check.
        Reads environment variables to report service configuration flags.

    Latency:
        Usually fast; DB connectivity checks add network latency.
    """
    db_status = "unknown"
    status = "healthy"
    try:
        from app.aegis.db.connection import get_async_session
        from sqlalchemy import text

        async with get_async_session() as session:
            await session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
        status = "degraded"

    return HealthResponse(
        status=status,
        version="1.0.0",
        database=db_status,
        services={
            "farma_workflow": "ready",
            "aegis_synthesis": "ready",
            "aegis_reports": "ready",
            "gemini_api": "configured" if os.getenv("GOOGLE_API_KEY") else "missing",
        },
    )


@router.websocket("/ws/thinking")
async def thinking_stream(websocket: WebSocket) -> None:
    """Stream internal "thinking" events to subscribed websocket clients.

    Request:
        WebSocket upgrade request to `/ws/thinking`.
        Token is passed as a `token` query parameter (WebSocket connections
        cannot use HTTP headers for auth in browsers).
        Incoming text frames are accepted as keepalive/flow-control signals.

    Response:
        Outbound messages are pushed by `thinking_bus` whenever new events are
        published.

    Status Codes:
        WebSocket handshake semantics apply (HTTP 101 on successful upgrade).
        Connection is closed with 1008 (Policy Violation) if auth fails.

    Auth:
        Requires valid API token via `?token=` query parameter when
        API_AUTH_ENABLED is true.

    Args:
        websocket: Active WebSocket connection object from FastAPI.

    Returns:
        None: Runs until the socket disconnects.

    Raises:
        WebSocketDisconnect: Raised when client disconnects; handled locally.

    Side Effects:
        Registers and deregisters socket with `thinking_bus`.

    Latency:
        Long-lived streaming connection; runtime depends on connection duration.
    """
    # Authenticate before accepting the connection
    auth_enabled = os.getenv("API_AUTH_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if auth_enabled:
        expected = (os.getenv("API_AUTH_TOKEN") or "").strip()
        provided = (websocket.query_params.get("token") or "").strip()
        if not expected or provided != expected:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await thinking_bus.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await thinking_bus.disconnect(websocket)
