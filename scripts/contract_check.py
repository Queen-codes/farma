"""API contract validator for FARMA/AEGIS endpoints.

This script performs schema-level and behavior-level checks against a running
API instance. It validates:
- Core health/admin/list endpoints against Pydantic response models.
- Scan trigger/status/job/event responses.
- Optional Phase 3 invariants (per-state tool events, incremental persistence,
  and finalized LGA risk persistence).

Typical usage:
`python scripts/contract_check.py --base-url http://127.0.0.1:8000 --wait-for-scan --verify-phase3`

Exit behavior:
- Returns exit code `0` when all checks pass.
- Returns exit code `1` when any check fails.
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Type

import requests
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.schemas import *  # noqa: F401,F403
from app.config import AEGIS_FOCUS_STATES


DEFAULT_BASE_URL = os.getenv("FARMA_API_URL", "http://127.0.0.1:8000")


class StatesResponseModel(BaseModel):
    focus_states: list[str]
    description: str
    all_nigerian_states: list[str]


class CropEntryModel(BaseModel):
    name: str
    local_names: list[str]


class CropsResponseModel(BaseModel):
    crops: list[CropEntryModel]


class AdminStatsLoanStatsModel(BaseModel):
    total_applications: int
    approved: int
    approval_rate_percent: float


class AdminStatsAegisStatsModel(BaseModel):
    total_reports: int
    focus_states: list[str]


class AdminStatsResponseModel(BaseModel):
    total_interactions: int
    by_intent: dict[str, int]
    by_decision: dict[str, int]
    by_language: dict[str, int]
    loan_stats: AdminStatsLoanStatsModel
    aegis_stats: AdminStatsAegisStatsModel


class ActivityItemModel(BaseModel):
    type: str
    timestamp: Optional[str] = None
    summary: str
    status: Optional[str] = None
    details: dict[str, Any]


class AdminActivityResponseModel(BaseModel):
    activities: list[ActivityItemModel]
    total: int


class ReportListEntryModel(BaseModel):
    filename: str
    created_at: str
    size_bytes: int
    download_url: str


class ReportsListResponseModel(BaseModel):
    reports: list[ReportListEntryModel]
    total: int


@dataclass
class CheckResult:
    endpoint: str
    ok: bool
    errors: str = "-"


def _request(method: str, path: str, **kwargs) -> requests.Response:
    """Issue an HTTP request against the configured base URL.

    Args:
        method: HTTP method (for example `GET` or `POST`).
        path: Endpoint path beginning with `/`.
        **kwargs: Request arguments forwarded to `requests.request`.
            Must include `_base_url`.

    Returns:
        Raw `requests.Response` object.

    Raises:
        KeyError: If `_base_url` is missing in kwargs.
        requests.RequestException: If the underlying HTTP call fails.

    Side Effects:
        Performs outbound network I/O.
    """
    base_url = kwargs.pop("_base_url")
    url = f"{base_url}{path}"
    resp = requests.request(method, url, timeout=60, **kwargs)
    return resp


def _load_json(resp: requests.Response) -> Any:
    """Decode response JSON, falling back to raw text payload wrapper.

    Args:
        resp: HTTP response returned by `_request`.

    Returns:
        Parsed JSON payload when valid JSON, else `{"_raw": "<text>"}`.

    Raises:
        None: JSON decode failures are handled internally.
    """
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text}


def _validate(model: Type[BaseModel], data: Any) -> Optional[str]:
    """Validate payload against a Pydantic model.

    Args:
        model: Pydantic model type to validate against.
        data: Decoded payload object.

    Returns:
        `None` when payload is valid, else a flattened validation error string.

    Raises:
        None: Validation exceptions are converted to string errors.
    """
    try:
        model.model_validate(data)
        return None
    except ValidationError as e:
        return str(e).replace("\n", " | ")


def _poll(base_url: str, path: str, *, timeout_s: int, sleep_s: float = 2.0) -> Any:
    """Poll an endpoint until timeout and return the latest payload.

    Args:
        base_url: API base URL.
        path: Endpoint path to poll.
        timeout_s: Maximum polling duration in seconds.
        sleep_s: Delay between polls in seconds.

    Returns:
        Last decoded JSON payload observed before timeout.

    Raises:
        requests.RequestException: If network requests repeatedly fail.

    Side Effects:
        Performs repeated HTTP requests and sleeps.
    """
    started = time.time()
    last = None
    while True:
        resp = _request("GET", path, _base_url=base_url)
        last = _load_json(resp)
        if time.time() - started > timeout_s:
            return last
        time.sleep(sleep_s)


def _get(base_url: str, path: str) -> tuple[int, Any]:
    """Perform GET request and return `(status_code, payload)`."""
    resp = _request("GET", path, _base_url=base_url)
    return resp.status_code, _load_json(resp)


def _post(base_url: str, path: str, **kwargs) -> tuple[int, Any]:
    """Perform POST request and return `(status_code, payload)`."""
    resp = _request("POST", path, _base_url=base_url, **kwargs)
    return resp.status_code, _load_json(resp)


def _phase3_verify_events(
    *,
    base_url: str,
    run_id: str,
    expected_states: list[str],
    timeout_s: int,
) -> Optional[str]:
    """Verify per-state tool lifecycle events are present in job stream.

    Args:
        base_url: API base URL.
        run_id: Scan run identifier.
        expected_states: State names expected to emit tool events.
        timeout_s: Poll timeout in seconds.

    Returns:
        `None` when all required events are observed for each state; otherwise
        an error string describing missing events.

    Raises:
        None: Network/status failures are retried and converted to errors.

    Side Effects:
        Polls job events endpoint until success/timeout.
    """
    required = {
        "conflict_started",
        "conflict_completed",
        "displacement_started",
        "displacement_completed",
        "food_security_started",
        "food_security_completed",
        "economic_started",
        "economic_completed",
        "state_completed",
    }

    per_state: dict[str, set[str]] = {s: set() for s in expected_states}
    started = time.time()

    while time.time() - started < timeout_s:
        code, data = _get(base_url, f"/api/jobs/{run_id}/events")
        if code != 200:
            time.sleep(2)
            continue
        events = data.get("events") or []
        for e in events:
            step = e.get("step")
            event_type = e.get("event_type")
            if not step or not event_type:
                continue
            if step in per_state:
                per_state[step].add(event_type)

        missing_by_state = {
            s: sorted(required - got) for s, got in per_state.items() if required - got
        }
        if not missing_by_state:
            return None

        time.sleep(2)

    missing_by_state = {
        s: sorted(required - got) for s, got in per_state.items() if required - got
    }
    return f"Missing per-tool events: {missing_by_state}"


def _phase3_verify_incremental_persist(
    *,
    base_url: str,
    run_id: str,
    timeout_s: int,
) -> Optional[str]:
    """Verify incremental scan persistence before final completion.

    Args:
        base_url: API base URL.
        run_id: Scan run identifier.
        timeout_s: Poll timeout in seconds.

    Returns:
        `None` when persistence is observed during running state (or via
        fallback event ordering); otherwise an explanatory error string.

    Raises:
        None: Network/status failures are retried and converted to errors.
    """
    started = time.time()
    saw_running = False
    while time.time() - started < timeout_s:
        code, data = _get(base_url, f"/api/aegis/scan/{run_id}")
        if code != 200:
            time.sleep(2)
            continue
        status = str(data.get("status", "")).lower()
        if status == "running":
            saw_running = True
            summaries = data.get("state_summaries") or []
            if len(summaries) > 0:
                return None
        if status in {"completed", "failed"}:
            break
        time.sleep(2)

    if not saw_running:
        # If the scan finished too fast to observe "running", fall back to event ordering:
        # at least one state_completed event should exist before scan_completed.
        code, data = _get(base_url, f"/api/jobs/{run_id}/events")
        if code != 200:
            return (
                "Never observed scan in running state; also failed to fetch job events"
            )
        events = data.get("events") or []
        scan_completed_ts = None
        for e in events:
            if e.get("event_type") == "scan_completed" and e.get("created_at"):
                scan_completed_ts = e["created_at"]
                break
        if not scan_completed_ts:
            return "Never observed scan in running state; scan_completed event missing"
        for e in events:
            if e.get("event_type") == "state_completed" and e.get("created_at"):
                if e["created_at"] < scan_completed_ts:
                    return None
        return "Never observed scan in running state and found no state_completed before scan_completed"
    return "Did not observe incremental persistence: state_summaries stayed empty during running"


def _phase3_verify_lga_risk_post_finalize(
    *,
    base_url: str,
    run_id: str,
    timeout_s: int,
) -> Optional[str]:
    """Ensure finalized scan persists LGA risk rows when conflict data exists.

    Args:
        base_url: API base URL.
        run_id: Scan run identifier.
        timeout_s: Poll timeout in seconds.

    Returns:
        `None` when final payload satisfies persistence expectations; otherwise
        an error string.

    Raises:
        None: Network/status failures are retried and converted to errors.
    """
    started = time.time()
    last = None
    while time.time() - started < timeout_s:
        code, last = _get(base_url, f"/api/aegis/scan/{run_id}")
        if code != 200:
            time.sleep(2)
            continue
        status = str((last or {}).get("status", "")).lower()
        if status in {"completed", "failed"}:
            break
        time.sleep(2)

    if not last:
        return "No scan status response"

    status = str(last.get("status", "")).lower()
    if status != "completed":
        return f"Scan did not complete (status={status})"

    conflict_events = last.get("conflict_events") or []
    lga_risk = last.get("lga_risk") or []

    # If there were conflict events, we expect risk rows to exist as stored output.
    if len(conflict_events) > 0 and len(lga_risk) == 0:
        return "lga_risk is empty after completion, but conflict_events is non-empty"

    return None


def main() -> int:
    """Run contract checks and print pass/fail summary table.

    Returns:
        `0` when all checks pass; `1` when any check fails.

    Raises:
        None: All per-check errors are captured and reported in summary output.

    Side Effects:
        Performs HTTP requests to target API and prints a result table.
    """
    parser = argparse.ArgumentParser(description="Validate FARMA API contract")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="API base URL (default: FARMA_API_URL env or http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--wait-for-scan",
        action="store_true",
        help="Wait up to 5 minutes for scan completion before final validation.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Reuse an existing scan run_id instead of triggering a new scan.",
    )
    parser.add_argument(
        "--skip-trigger-scan",
        action="store_true",
        help="Do not trigger a scan (requires --run-id to validate scan/job endpoints).",
    )
    parser.add_argument(
        "--verify-phase3",
        action="store_true",
        help="Verify Phase 3 invariants: per-tool events, incremental persistence, stored LGA risk.",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    results: list[CheckResult] = []

    checks: list[tuple[str, str, str, Type[BaseModel]]] = [
        ("GET /health", "GET", "/health", HealthResponse),
        (
            "GET /api/aegis/dashboard",
            "GET",
            "/api/aegis/dashboard",
            AegisDashboardResponse,
        ),
        ("GET /api/states", "GET", "/api/states", StatesResponseModel),
        ("GET /api/crops", "GET", "/api/crops", CropsResponseModel),
        ("GET /api/admin/stats", "GET", "/api/admin/stats", AdminStatsResponseModel),
        (
            "GET /api/admin/activity",
            "GET",
            "/api/admin/activity",
            AdminActivityResponseModel,
        ),
        (
            "GET /api/aegis/reports",
            "GET",
            "/api/aegis/reports",
            ReportsListResponseModel,
        ),
    ]

    for label, method, path, model in checks:
        try:
            resp = _request(method, path, _base_url=base_url)
            data = _load_json(resp)
            if resp.status_code != 200:
                results.append(CheckResult(label, False, f"HTTP {resp.status_code}"))
                continue
            err = _validate(model, data)
            results.append(CheckResult(label, err is None, err or "-"))
        except Exception as e:
            results.append(CheckResult(label, False, str(e)))

    # Trigger scan for job-related contract checks
    scan_run_id = args.run_id
    if not scan_run_id and not args.skip_trigger_scan:
        try:
            code, data = _post(
                base_url, "/api/aegis/scan", json={"force_refresh": True}
            )
            label = "POST /api/aegis/scan"
            if code != 200:
                results.append(CheckResult(label, False, f"HTTP {code}"))
            else:
                err = _validate(AegisScanResponse, data)
                results.append(CheckResult(label, err is None, err or "-"))
                scan_run_id = data.get("run_id")
        except Exception as e:
            results.append(CheckResult("POST /api/aegis/scan", False, str(e)))
    elif not scan_run_id and args.skip_trigger_scan:
        results.append(
            CheckResult(
                "POST /api/aegis/scan",
                False,
                "Skipped (no scan triggered; pass --run-id or omit --skip-trigger-scan)",
            )
        )

    if scan_run_id:
        # Poll scan status + jobs + events
        status_path = f"/api/aegis/scan/{scan_run_id}"
        job_path = f"/api/jobs/{scan_run_id}"
        events_path = f"/api/jobs/{scan_run_id}/events"

        # Initial validation
        for label, path, model in [
            (f"GET {status_path}", status_path, AegisScanStatusResponse),
            (f"GET {job_path}", job_path, JobResponse),
            (f"GET {events_path}", events_path, JobEventsResponse),
        ]:
            try:
                resp = _request("GET", path, _base_url=base_url)
                data = _load_json(resp)
                if resp.status_code != 200:
                    results.append(
                        CheckResult(label, False, f"HTTP {resp.status_code}")
                    )
                    continue
                err = _validate(model, data)
                results.append(CheckResult(label, err is None, err or "-"))
            except Exception as e:
                results.append(CheckResult(label, False, str(e)))

        if args.wait_for_scan:
            # Wait for completion and validate final scan status again.
            deadline = time.time() + 300
            last_status = None
            while time.time() < deadline:
                resp = _request("GET", status_path, _base_url=base_url)
                if resp.status_code != 200:
                    time.sleep(2)
                    continue
                last_status = _load_json(resp)
                s = str(last_status.get("status", "")).lower()
                if s in {"completed", "failed"}:
                    break
                time.sleep(2)

            label = f"GET {status_path} (final)"
            if last_status is None:
                results.append(CheckResult(label, False, "No status response"))
            else:
                err = _validate(AegisScanStatusResponse, last_status)
                results.append(CheckResult(label, err is None, err or "-"))

        if args.verify_phase3:
            # Derive expected states from scan status response (fallback to focus_states).
            expected_states: list[str] = []
            code, job_data = _get(base_url, job_path)
            if code == 200:
                md = (job_data or {}).get("metadata") or {}
                expected_states = md.get("states") or []
            if not expected_states:
                expected_states = list(AEGIS_FOCUS_STATES)

            err = _phase3_verify_incremental_persist(
                base_url=base_url, run_id=scan_run_id, timeout_s=180
            )
            results.append(
                CheckResult("PHASE3 incremental persist", err is None, err or "-")
            )

            err = _phase3_verify_events(
                base_url=base_url,
                run_id=scan_run_id,
                expected_states=list(expected_states),
                timeout_s=240,
            )
            results.append(
                CheckResult("PHASE3 per-tool events", err is None, err or "-")
            )

            err = _phase3_verify_lga_risk_post_finalize(
                base_url=base_url, run_id=scan_run_id, timeout_s=300
            )
            results.append(
                CheckResult("PHASE3 stored LGA risk", err is None, err or "-")
            )

    # Print summary table
    header = f"{'ENDPOINT':35}  {'STATUS':6}  ERRORS"
    print(header)
    print("-" * len(header))
    any_fail = False
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        if not r.ok:
            any_fail = True
        print(f"{r.endpoint:35}  {status:6}  {r.errors}")

    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
