"""AEGIS Repor

This module generates a PDF report scoped to a scan_id using synthesis outputs persisted in Postgres:
- aegis_scans.rollup_json
- aegis_state_intelligence.assessment_json


"""

from .runner import run_report_dag

__all__ = ["run_report_dag"]
