"""AEGIS deterministic synthesis/analysis workflow orchestration

Design goals:
- scan_id scoped
- bounded LLM calls (1 per state + 1 rollup)
- no web browsing; citations only from scan-grounded URIs
- structured JSON outputs (schema-first) persisted to DB
"""

from .runner import run_synthesis_dag

__all__ = ["run_synthesis_dag"]
