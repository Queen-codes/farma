"""End-to-end smoke runner for core FARMA/AEGIS API workflow.

This script performs a fast operational sanity check against a running API:
1. Health and dashboard endpoints.
2. AEGIS scan trigger and completion polling.
3. Report generation and optional PDF download.
4. Farmer simulation job trigger and completion.

Use this after deployment or local startup to quickly verify integration paths.
"""

import os
import time
from typing import Any, Dict, Optional

import requests


def _base_url() -> str:
    """Return API base URL from env with localhost default."""
    return os.getenv("FARMA_API_URL", "http://localhost:8000").rstrip("/")


def _req(method: str, path: str, **kwargs) -> requests.Response:
    """Send one HTTP request and enforce non-error status.

    Args:
        method: HTTP method name.
        path: Endpoint path beginning with `/`.
        **kwargs: Additional request options forwarded to `requests.request`.

    Returns:
        Successful HTTP response object.

    Raises:
        requests.HTTPError: If response status code indicates failure.
        requests.RequestException: For transport-level failures.
    """
    url = f"{_base_url()}{path}"
    resp = requests.request(method, url, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp


def _poll_json(
    path: str, *, key_status: str = "status", timeout_s: int = 180
) -> Dict[str, Any]:
    """Poll JSON endpoint until terminal status is reached.

    Args:
        path: Endpoint path to poll.
        key_status: JSON key containing status text.
        timeout_s: Maximum wait duration.

    Returns:
        Final JSON payload where status is `completed`, `failed`, or `error`.

    Raises:
        TimeoutError: If terminal status is not reached within timeout.
        requests.RequestException: If polling requests fail.
    """
    started = time.time()
    while True:
        data = _req("GET", path).json()
        status = str(data.get(key_status, "")).lower()
        if status in {"completed", "failed", "error"}:
            return data
        if time.time() - started > timeout_s:
            raise TimeoutError(
                f"Timeout polling {path} after {timeout_s}s (last status={status})"
            )
        time.sleep(2)


def main() -> None:
    """Run full smoke sequence and raise on first critical failure.

    Returns:
        None.

    Raises:
        RuntimeError: For failed or malformed pipeline stages.
        requests.RequestException: For HTTP/transport failures.
        TimeoutError: For long-running stages exceeding timeouts.
    """
    print(f"[smoke] FARMA_API_URL={_base_url()}")

    health = _req("GET", "/health").json()
    print(f"[smoke] /health: status={health.get('status')} db={health.get('database')}")
    if str(health.get("database", "")).startswith("error"):
        raise RuntimeError(
            "Database is not reachable. Start Postgres and set DATABASE_URL."
        )

    dash = _req("GET", "/api/aegis/dashboard").json()
    print(
        f"[smoke] /api/aegis/dashboard: focus_states={len(dash.get('focus_states', []))} summaries={len(dash.get('state_summaries', []))}"
    )

    # Scan
    scan_req = {"days_back": 1, "force_refresh": True}
    scan = _req("POST", "/api/aegis/scan", json=scan_req).json()
    run_id = scan.get("run_id")
    scan_id = scan.get("scan_id")
    print(f"[smoke] scan started: run_id={run_id} scan_id={scan_id}")
    if not run_id:
        raise RuntimeError(f"Scan did not return run_id: {scan}")
    scan_status = _poll_json(f"/api/aegis/scan/{run_id}", timeout_s=240)
    print(
        "[smoke] scan done:"
        f" status={scan_status.get('status')}"
        f" states_scanned={scan_status.get('states_scanned')}"
        f" total_events={scan_status.get('total_events')}"
    )
    if str(scan_status.get("status")).lower() == "failed":
        raise RuntimeError(f"Scan failed: {scan_status}")

    scan_db_id = scan_status.get("scan_id") or scan_id
    if not scan_db_id:
        print(
            "[smoke] warning: scan_id is 0; report generation will likely fail (DB write failed)."
        )
        return

    # Report (keep it fast for demo)
    report_req = {
        "scan_id": int(scan_db_id),
        "include_infographics": False,
        "include_annexes": False,
    }
    report = _req("POST", "/api/aegis/report", json=report_req).json()
    report_id = report.get("report_id")
    print(f"[smoke] report started: report_id={report_id}")
    if not report_id:
        raise RuntimeError(f"Report did not return report_id: {report}")
    report_status = _poll_json(f"/api/aegis/report/{report_id}", timeout_s=240)
    print(
        f"[smoke] report done: status={report_status.get('status')} download_url={report_status.get('download_url')}"
    )
    if str(report_status.get("status")).lower() in {"failed", "error"}:
        raise RuntimeError(f"Report failed: {report_status}")
    if report_status.get("download_url"):
        pdf = _req("GET", f"/api/aegis/report/{report_id}/download")
        ct = pdf.headers.get("content-type", "")
        print(f"[smoke] pdf: content-type={ct} bytes={len(pdf.content)}")
        if "application/pdf" not in ct:
            raise RuntimeError(f"Unexpected PDF content-type: {ct}")

    # Farmer pipeline
    farmer_params = {
        "phone": "+2347000000000",
        "message": "I wan borrow 50k for maize near Maiduguri",
    }
    farmer_job = _req("POST", "/api/farmer/simulate", params=farmer_params).json()
    job_id = farmer_job.get("job_id")
    print(f"[smoke] farmer job started: job_id={job_id}")
    if not job_id:
        raise RuntimeError(f"Farmer simulate did not return job_id: {farmer_job}")
    job = _poll_json(f"/api/jobs/{job_id}", timeout_s=180)
    print(
        f"[smoke] farmer job done: status={job.get('status')} has_result={bool(job.get('result'))}"
    )
    if str(job.get("status")).lower() == "failed":
        raise RuntimeError(f"Farmer job failed: {job}")

    print("[smoke] demo path OK")


if __name__ == "__main__":
    main()
