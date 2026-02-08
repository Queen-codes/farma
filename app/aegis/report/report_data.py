"""Data containers for report input loading and render-time aggregation.

Purpose:
- Define structured dataclasses passed between report graph nodes.
- Provide helper methods for URI whitelist and aggregate totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse


def utcnow_iso() -> str:
    """Return current UTC timestamp in ISO-8601 string form."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_source_uri(uri: Any) -> Optional[str]:
    """Return normalized absolute http(s) URI or `None` if invalid."""
    if not isinstance(uri, str):
        return None
    value = uri.strip()
    if not value:
        return None
    if value.startswith("www."):
        value = f"https://{value}"
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    # Prefer direct target URI for Google redirect wrapper links.
    if parsed.netloc in {"www.google.com", "google.com"} and parsed.path == "/url":
        target = (parse_qs(parsed.query).get("q") or [None])[0]
        if isinstance(target, str) and target:
            try:
                t = urlparse(target)
                if t.scheme in {"http", "https"} and t.netloc:
                    return target.strip()
            except Exception:
                pass
    return value


@dataclass
class ReportInputs:
    """Raw report input bundle loaded from DB artifacts."""

    scan_id: int
    scan_run_id: str
    scan_started_at: Optional[str]
    scan_completed_at: Optional[str]
    states: List[str]
    rollup_json: Dict[str, Any]
    assessments_by_state: Dict[str, Dict[str, Any]]
    simulation: Optional[Dict[str, Any]] = None


@dataclass
class ReportData:
    """Normalized render-time report context passed to narrative/PDF builders."""

    report_id: str
    scan_id: int
    generated_at: str
    states: List[str]
    rollup: Dict[str, Any]
    assessments_by_state: Dict[str, Dict[str, Any]]
    simulation: Optional[Dict[str, Any]] = None
    uri_whitelist: List[str] = field(default_factory=list)

    def build_uri_whitelist(self) -> List[str]:
        """Build sorted unique URI list from assessment key findings."""
        uris: List[str] = []
        seen: Set[str] = set()
        for assessment in self.assessments_by_state.values():
            for finding in (assessment.get("key_findings") or []):
                for raw_uri in (finding.get("source_uris") or []):
                    uri = _normalize_source_uri(raw_uri)
                    if not uri or uri in seen:
                        continue
                    seen.add(uri)
                    uris.append(uri)
        self.uri_whitelist = uris
        return self.uri_whitelist

    def totals(self) -> Tuple[int, int]:
        """Aggregate total conflict events and fatalities across states."""
        total_events = 0
        total_fatalities = 0
        for assessment in self.assessments_by_state.values():
            metrics = assessment.get("metrics") or {}
            try:
                total_events += int(
                    metrics.get("conflict_events_count")
                    or metrics.get("conflict_events")
                    or 0
                )
            except Exception:
                pass
            try:
                total_fatalities += int(metrics.get("fatalities") or 0)
            except Exception:
                pass
        return total_events, total_fatalities
