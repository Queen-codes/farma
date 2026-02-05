from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReportInputs:
    scan_id: int
    scan_run_id: str
    scan_started_at: Optional[str]
    scan_completed_at: Optional[str]
    states: List[str]
    rollup_json: Dict[str, Any]
    assessments_by_state: Dict[str, Dict[str, Any]]


@dataclass
class ReportData:
    report_id: str
    scan_id: int
    generated_at: str
    states: List[str]
    rollup: Dict[str, Any]
    assessments_by_state: Dict[str, Dict[str, Any]]
    uri_whitelist: List[str] = field(default_factory=list)

    def build_uri_whitelist(self) -> List[str]:
        uris: Set[str] = set()
        for assessment in self.assessments_by_state.values():
            for finding in (assessment.get("key_findings") or []):
                for uri in (finding.get("source_uris") or []):
                    if isinstance(uri, str) and uri:
                        uris.add(uri)
        self.uri_whitelist = sorted(uris)
        return self.uri_whitelist

    def totals(self) -> Tuple[int, int]:
        total_events = 0
        total_fatalities = 0
        for assessment in self.assessments_by_state.values():
            metrics = assessment.get("metrics") or {}
            try:
                total_events += int(metrics.get("conflict_events") or 0)
            except Exception:
                pass
            try:
                total_fatalities += int(metrics.get("fatalities") or 0)
            except Exception:
                pass
        return total_events, total_fatalities

