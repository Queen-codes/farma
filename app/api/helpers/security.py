"""Token-based API authentication dependency for protected routes.

Key responsibilities:
- Decide whether API auth enforcement is enabled.
- Read expected token from environment configuration.
- Validate Bearer or API-key headers and reject unauthorized requests.

Used by:
- `app.api.routes.aegis`, `app.api.routes.farmer`, and `app.api.routes.jobs`
  via ``Depends(require_api_auth)``.

Assumptions:
- `API_AUTH_ENABLED` defaults to enabled (`true`) unless explicitly disabled.
- `API_AUTH_TOKEN` is configured when auth enforcement is enabled.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def _auth_enabled() -> bool:
    """Return whether API auth enforcement is enabled.

    Args:
        None.

    Returns:
        bool: ``True`` when auth checks should run.

    Raises:
        Does not raise intentionally.

    Side Effects:
        Reads `API_AUTH_ENABLED` from process environment.

    Latency:
        Constant-time local environment lookup.
    """
    raw = os.getenv("API_AUTH_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _expected_token() -> str:
    """Fetch the configured API token used for header validation.

    Args:
        None.

    Returns:
        str: Stripped token string, or empty string if not configured.

    Raises:
        Does not raise intentionally.

    Side Effects:
        Reads `API_AUTH_TOKEN` from process environment.

    Latency:
        Constant-time local environment lookup.
    """
    return (os.getenv("API_AUTH_TOKEN") or "").strip()


def require_api_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Validate API auth headers for protected endpoints.

    Request:
        Headers may include either ``Authorization: Bearer <token>`` or
        ``X-API-Key: <token>``.

    Response:
        No response body on success; execution continues to route handler.

    Status Codes:
        401: Provided token is missing or invalid.
        503: Auth is enabled but `API_AUTH_TOKEN` is not configured.

    Auth:
        This dependency is the auth gate itself.

    Idempotency:
        Idempotent header validation with no persistent state changes.

    Args:
        authorization: Raw ``Authorization`` header value.
        x_api_key: Raw ``X-API-Key`` header value.

    Returns:
        None: Route execution continues when token validation succeeds.

    Raises:
        HTTPException: With status 401 or 503 when auth requirements are not met.

    Side Effects:
        Reads environment configuration to evaluate auth policy.

    Latency:
        Constant-time string parsing and environment lookups.
    """
    if not _auth_enabled():
        return

    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API auth is enabled but API_AUTH_TOKEN is not configured.",
        )

    bearer = ""
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            bearer = parts[1].strip()

    provided = bearer or (x_api_key or "").strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
