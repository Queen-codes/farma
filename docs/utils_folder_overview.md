# Utils Folder Overview

## Folder Structure

- `app/utils/gcs_store.py`
  - Storage helper facade.
  - Prefers Google Cloud Storage, with local filesystem fallback under `reports/` and `tmp_audio/`.
- `app/utils/job_store.py`
  - Job lifecycle and event timeline persistence.
  - Uses SQLAlchemy async DB tables (`job_runs`, `job_events`, `farmer_interactions`) with in-memory fallback and optional strict DB mode.
- `app/utils/thinking_bus.py`
  - In-process websocket broadcast bus for short live status messages.

## Major Flows

### 1) Job Tracking Flow

1. API/workflow code creates job via `JobStore.create_job(...)`.
2. Runtime appends progress via `JobStore.add_event(...)`.
3. Final state written with `JobStore.update_job(...)`.
4. Clients fetch status via `JobStore.get_job(...)` and `JobStore.list_events(...)`.

### 2) Event Broadcast Flow

1. `JobStore.add_event(...)` formats a compact event message.
2. It imports `thinking_bus` and calls `thinking_bus.broadcast(...)`.
3. `ThinkingBus` sends message to all connected websocket clients.
4. Failed clients are removed from subscriber set.

### 3) Storage Flow

1. Callers use `upload_bytes`, `download_bytes`, `list_objects`, `delete_object`.
2. `gcs_store._gcs_client()` attempts Google Cloud Storage client.
3. On unavailable client or request failure, operations transparently fallback to local filesystem.

## External Dependencies and Call Sites

- **Database (PostgreSQL via SQLAlchemy async)**
  - `app/utils/job_store.py`
  - Reads/writes `JobRun` and `JobEvent` records.
- **Google Cloud Storage**
  - `app/utils/gcs_store.py`
  - Upload/download/list/delete object operations.
- **WebSocket transport (FastAPI WebSocket)**
  - `app/utils/thinking_bus.py`
  - Broadcasts lightweight event updates to connected clients.

## Request -> Validation -> Service -> Response Pattern (Utils Context)

1. Input params are normalized and shaped (job/event payloads, storage keys).
2. Availability checks decide backend path (DB/GCS vs fallback).
3. External service call executes (DB query/commit, GCS API, websocket send).
4. Fallback path preserves app continuity when services are temporarily unavailable.
5. Standardized dict outputs are returned to upstream API/workflow layers.
