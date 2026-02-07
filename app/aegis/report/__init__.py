"""AEGIS report agent entrypoint.

Purpose:
- Expose stable report-generation runner used by API and scheduler code.

Flow:
- `run_report_dag` executes report graph nodes that load synthesis artifacts,
  generate narrative/infographics, build PDF, and persist report metadata.
"""

from .runner import run_report_dag

__all__ = ["run_report_dag"]
