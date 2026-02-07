"""FARMA workflow package.

Purpose:
- Contains the end-to-end farmer request orchestration graph and node modules.
- Provides shared workflow utilities (LLM calls, geocoding, GEE signals,
  translation, and job-event emitting helpers).

Entry points:
- `app.workflows.graph.farma_graph`: compiled LangGraph for pipeline execution.
- `app.workflows.runner`: async orchestration helpers used by API routes.
"""
