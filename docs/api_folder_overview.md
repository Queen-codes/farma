# API Folder Overview (`app/api`)

## Folder Structure

- `app/api/__init__.py`
  - Re-exports commonly used API schema classes from `app/api/schemas.py`.
- `app/api/schemas.py`
  - Pydantic enums/models for all request and response payloads.
- `app/api/helpers/`
  - `paths.py`: shared filesystem paths (`REPORTS_DIR`).
  - `runtime.py`: env parsing, UTC timestamps, background task helper.
  - `security.py`: API token auth dependency.
  - `aegis_queries.py`: scan-summary query/aggregation helpers.
  - `aegis_scheduler.py`: unattended daily scheduler loop.
  - `startup.py`: lifespan startup/shutdown + SQL migrations.
- `app/api/routes/`
  - `system.py`: root metadata, health check, thinking websocket.
  - `farmer.py`: farmer workflow simulate/resume endpoints.
  - `jobs.py`: asynchronous job status/event polling.
  - `aegis.py`: scans, synthesis, marathon, simulations, reports.

## Major Flows

1. Request entry:
   - `app/main.py` includes routers from `app/api/routes/*`.
2. Validation:
   - FastAPI validates query/path/body inputs against `app/api/schemas.py`.
3. Route orchestration:
   - Route handlers in `app/api/routes/*` create/read jobs and trigger background tasks.
4. Service execution:
   - Background work is delegated to `app.aegis.*` runners or `app.workflows.runner`.
5. Status/response:
   - Clients poll `/api/jobs/*` or status endpoints (`/api/aegis/scan/*`, `/api/aegis/report/*`).

## External Dependency Touchpoints

- Database (PostgreSQL/SQLAlchemy):
  - Directly used in `app/api/helpers/startup.py`, `app/api/helpers/aegis_scheduler.py`,
    `app/api/helpers/aegis_queries.py`, `app/api/routes/aegis.py`, and
    `app/api/routes/system.py`.
- GEE (Google Earth Engine):
  - Invoked downstream in FARMA workflow modules such as
    `app/workflows/gee_signals.py` and `app/workflows/nodes/loan/satellite.py`.
  - API routes trigger those flows indirectly (for example via `run_farma_job` or AEGIS runners).
- Gemini (LLM):
  - Invoked downstream in AEGIS and FARMA modules, including
    `app/aegis/scan/state_worker.py`, `app/aegis/synthesis/llm.py`,
    `app/aegis/report/narrative.py`, and `app/workflows/gemini_async.py`.
  - API endpoints in `app/api/routes/aegis.py` and `app/api/routes/farmer.py`
    trigger these runners asynchronously.
- Maps / Geocoding:
  - Google Geocoding API is called in `app/workflows/geocode_provenance.py`.
  - Triggered indirectly by farmer workflow routes.
- SMS:
  - API accepts SMS-like payloads (`SMSRequest`) and drives SMS response flow.
  - Current sender node (`app/workflows/graph.py:sms_sender_node`) logs/simulates sending
    and notes Twilio/Africa's Talking integration as future/placeholder behavior.

## Notes and Assumptions

- Assumption: `REPORTS_DIR` is writable by the running process.
- Assumption: `API_AUTH_TOKEN` is configured when `API_AUTH_ENABLED=true`.
- TODO: verify whether production SMS delivery is still simulated or wired to a provider.
