# Workflows Folder Overview

## Folder Structure

- `app/workflows/state.py`: Shared typed LangGraph state contract (`FarmaState`).
- `app/workflows/graph.py`: Main LangGraph definition (nodes, routing, edges, compile).
- `app/workflows/runner.py`: Runtime bridge that runs/resumes graph and forwards job events.
- `app/workflows/job_events.py`: Helper to emit graph custom events.
- `app/workflows/loan_schemas.py`: Pydantic schemas for parser and underwriting structured output.
- `app/workflows/gemini_async.py`: Shared async Gemini JSON-call utility.
- `app/workflows/language_utils.py`: Farmer-language translation helpers and fallback cache.
- `app/workflows/geocode_provenance.py`: Deterministic Google geocoding + confidence metadata.
- `app/workflows/geocode_shared.py`: Shared geocode normalization and clarification helpers.
- `app/workflows/gee_signals.py`: Earth Engine metrics (NDVI, CHIRPS, SAR, AEZ helpers).
- `app/workflows/gee_artifacts.py`: Visualization/artifact helpers for geospatial outputs.

### Node Packages

- `app/workflows/nodes/parsers/`: SMS/voice input parsing.
- `app/workflows/nodes/loan/`: Loan geocode -> satellite -> AEGIS -> underwriting.
- `app/workflows/nodes/disease/`: Disease analysis + safety guardrails.
- `app/workflows/nodes/climate/`: Climate geocode + forecast + rainfall + advisory.
- `app/workflows/nodes/human/`: Human escalation and awaiting-response handlers.

## Major Execution Flows

### 1) Entry + Intent Routing

1. `graph.route_input` routes to `sms_parser_node` or `voice_parser_node`.
2. Parser sets `intent`, `language`, `parsed_data`, and initial `status`.
3. `intent_gate` promotes `READY_FOR_ANALYSIS` to `ANALYSIS_ONGOING`.
4. `route_by_intent` dispatches to loan/disease/climate/human branches.

### 2) Loan Flow

1. `nodes/loan/geocode.py::geocoding_node`
2. `nodes/loan/satellite.py::satellite_analysis_node`
3. `nodes/loan/aegis_context.py::aegis_risk_check_node`
4. `nodes/loan/underwriter.py::loan_underwriter_node`
5. `graph.sms_sender_node`

Notes:
- Low-confidence location triggers `AWAITING_FARMER_RESPONSE`.
- Underwriter may set `NEEDS_HUMAN_VERIFICATION`.

### 3) Disease Flow

1. `nodes/disease/analyze.py::disease_generate_once`
2. `nodes/disease/guardrails.py::disease_guardrails`
3. `graph.response_aggregator`
4. `graph.sms_sender_node`

Notes:
- Guardrails remove unsafe advice, request clarification, or escalate high-risk cases.

### 4) Climate Flow

1. `nodes/climate/geocode.py::geocode_location_deterministic`
2. Parallel:
   - `nodes/climate/forecast.py::fetch_weather_forecast`
   - `nodes/climate/chirps.py::fetch_recent_rainfall_chirps`
3. `nodes/climate/advisory.py::gemini_climate_advisory`
4. `graph.response_aggregator`
5. `graph.sms_sender_node`

Notes:
- Approximate coordinates are usually acceptable for climate advisory.

### 5) Human-in-the-loop Flow

1. `nodes/human/escalation.py::human_escalation_handler`
2. Node emits triage metadata and pauses via `interrupt(...)`.
3. `runner.resume_farma_job(...)` resumes thread with human response.
4. Flow returns to response path and SMS send.

## External Dependencies and Call Sites

- **Gemini (LLM)**:
  - Shared client wrapper: `app/workflows/gemini_async.py`
  - Callers: parsers, disease analyzer, climate advisory, underwriter, translation.
- **Google Maps Geocoding API**:
  - `app/workflows/geocode_provenance.py`
  - Used by loan and climate geocode nodes.
- **Google Earth Engine (GEE)**:
  - Core signals: `app/workflows/gee_signals.py`
  - Used by `nodes/loan/satellite.py` and `nodes/climate/chirps.py`.
- **Weather API (Open-Meteo)**:
  - `nodes/climate/forecast.py`.
- **Database (AEGIS intelligence)**:
  - `nodes/loan/aegis_context.py` via SQLAlchemy async session.
- **Job/Event persistence**:
  - `runner.py` writes to `app.utils.job_store`.
- **SMS sending**:
  - Central send stage is `graph.sms_sender_node` (currently event/log simulation).

## Request -> Validation -> Service -> Response Pattern

1. **Request ingestion**: parser node reads message/audio.
2. **Validation/normalization**: Pydantic models + helper transforms.
3. **Service calls**: geocode, weather, GEE, Gemini, AEGIS DB.
4. **Policy checks**: guardrails/underwriting status transitions.
5. **Response assembly**: aggregator (when needed) + sender node.
6. **Observability**: node events streamed to job timeline via runner.
