# Tests Folder Overview

## Folder Structure

- `tests/__init__.py`: Test package marker and high-level suite description
- `tests/test_aegis_deterministic_logic.py`: Unit tests for AEGIS deterministic helpers (scoring, deltas, scenario selection, URI dedupe).
- `tests/test_api_models_and_system_route.py`: API system route contract checks and schema default-isolation tests.
- `tests/test_farm_locations.py`: Static Nigeria farm-location dataset and retrieval helpers used by tests/manual runs.
- `tests/test_gcs_store_local_fallback.py`: Utility tests for local fallback behavior in storage helpers.
- `tests/test_gemini_async_fixes.py`: Unit tests for Gemini config/thinking-level normalization logic.
- `tests/test_language_translation.py`: Translation utility and integration-style behavior checks.
- `tests/test_loan_approval.py`, `tests/test_loan_igbo.py`, `tests/test_loan_yoruba.py`: End-to-end-ish loan workflow scenario tests.
- `tests/test_report_cache_and_data.py`: Tests for report cache key generation and report aggregation.
- `tests/test_runtime_and_geocode_helpers.py`: Runtime helper and shared geocode helper tests.
- `tests/test_scan_tools_parsing.py`: Parsing tests for AEGIS scan tool text extraction.
- `tests/test_simulator_projections.py`: Scenario projection and policy recommendation tests.
- `tests/test_workflow_routing_and_guardrails.py`: Workflow routing and disease guardrail regression tests.

## Major Test Flows

### 1) Deterministic Logic Validation

1. Provide controlled input dicts/text.
2. Call pure helper functions (no network side effects).
3. Assert stable output fields and state transitions.

Files:
- `test_aegis_deterministic_logic.py`
- `test_scan_tools_parsing.py`
- `test_simulator_projections.py`
- `test_runtime_and_geocode_helpers.py`

### 2) API Contract Validation

1. Call API route functions directly (`root`, `health_check`).
2. Monkeypatch DB/session behavior for success/failure paths.
3. Assert response schema values and status changes.

File:
- `test_api_models_and_system_route.py`

### 3) Workflow Routing/Guardrail Validation

1. Build lightweight state dicts.
2. Call graph routing/guardrail nodes.
3. Assert node selection, truncation behavior, and escalation logic.

File:
- `test_workflow_routing_and_guardrails.py`

### 4) End-to-End-ish Loan Scenarios

1. Build full initial state.
2. Run workflow through `run_farma_job`.
3. Assert intent, status, decision presence, and SMS constraints.

Files:
- `test_loan_approval.py`
- `test_loan_igbo.py`
- `test_loan_yoruba.py`

## External Dependencies Used in Tests

- **Database/session behavior** (mocked or real depending on test path):
  - `test_api_models_and_system_route.py`
- **Workflow engine + service integrations** (when running loan scenario tests):
  - `test_loan_*.py` call `run_farma_job` and may touch geocode/LLM/GEE/AEGIS paths depending on environment.
- **Filesystem temporary storage**:
  - `test_gcs_store_local_fallback.py`
  - `test_report_cache_and_data.py`
- **Web/runtime helpers**:
  - `test_runtime_and_geocode_helpers.py`
