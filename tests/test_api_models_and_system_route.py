"""Tests for API system route contract and schema default isolation.

Coverage:
- Root route payload shape.
- Health route behavior for healthy/degraded DB paths.
- Pydantic mutable default isolation for response schemas.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.routes.system import health_check, root
from app.api.schemas import AegisDashboardResponse, AegisReportStatusResponse


class _DummySession:
    """Minimal async session stub used to fake successful DB health checks."""

    async def execute(self, _query: Any) -> int:
        """Pretend to execute a query and return a non-empty result marker."""
        return 1


class _DummySessionCtx:
    """Async context manager stub that yields `_DummySession`."""

    async def __aenter__(self) -> _DummySession:
        """Return a dummy session instance on context entry."""
        return _DummySession()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> bool:
        """Do not suppress exceptions from the wrapped block."""
        return False


def test_root_route_payload_shape() -> None:
    """Assert root route exposes stable public metadata fields."""
    data = root()
    assert data["name"] == "Farma API"
    assert data["status"] == "running"
    assert data["docs"] == "/docs"


@pytest.mark.anyio
async def test_health_check_reports_connected_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify health endpoint reports connected DB and configured Gemini key."""
    monkeypatch.setattr("app.aegis.db.connection.get_async_session", lambda: _DummySessionCtx())
    monkeypatch.setenv("GOOGLE_API_KEY", "present")

    out = await health_check()
    assert out.status == "healthy"
    assert out.database == "connected"
    assert out.services["gemini_api"] == "configured"


@pytest.mark.anyio
async def test_health_check_reports_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify degraded health payload when DB session factory raises."""

    def _boom() -> None:
        """Raise a deterministic DB failure for health-check fallback path."""
        raise RuntimeError("db down")

    monkeypatch.setattr("app.aegis.db.connection.get_async_session", _boom)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    out = await health_check()
    assert out.status == "degraded"
    assert out.database.startswith("error:")
    assert out.services["gemini_api"] == "missing"


def test_pydantic_mutable_defaults_are_isolated() -> None:
    """Ensure response model mutable defaults are not shared across instances."""
    a = AegisReportStatusResponse(report_id="R1", status="running")
    b = AegisReportStatusResponse(report_id="R2", status="running")
    a.steps_completed.append("x")
    a.timings["phase"] = 1.2

    assert b.steps_completed == []
    assert b.timings == {}


def test_dashboard_mutable_defaults_are_isolated() -> None:
    """Ensure dashboard alert default lists are instance-local."""
    a = AegisDashboardResponse(total_scans=1, total_reports=1, focus_states=[], state_summaries=[])
    b = AegisDashboardResponse(total_scans=0, total_reports=0, focus_states=[], state_summaries=[])
    a.recent_alerts.append({"msg": "hi"})
    assert b.recent_alerts == []
