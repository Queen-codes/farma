# AEGIS Folder Overview (`app/aegis`)

## Folder Structure

- `app/aegis/__init__.py`
  - Stable exports for API layer (`run_aegis_scan`, DB models/helpers).
- `app/aegis/graph.py`
  - Thin orchestration wrapper that delegates to `scan.runner`.
- `app/aegis/db/`
  - `connection.py`: async engine/session lifecycle helpers.
  - `models.py`: SQLAlchemy schema for scans, state intelligence, reports, marathon days, simulations.
- `app/aegis/scan/`
  - Planner + grounded tool execution per state.
  - Persists raw tool outputs and conflict events.
  - Finalizes scan totals + LGA risk aggregates.
- `app/aegis/synthesis/`
  - Normalizes scan outputs.
  - Generates state assessments and scan rollups via schema-constrained LLM calls.
  - Persists `assessment_json` and `rollup_json`.
- `app/aegis/report/`
  - Loads synthesis artifacts.
  - Generates narrative + infographics.
  - Builds PDF and persists report metadata (optional GCS upload).
- `app/aegis/simulator/`
  - Deterministic scenario projections + LLM policy brief.
  - Persists simulation artifacts.
- `app/aegis/marathon/`
  - Day-over-day continuity analysis and thought-signature replay.
  - Can trigger autonomous simulation/report actions.

## Major Flows

1. Scan flow:
   - API/scheduler calls `app.aegis.graph.run_aegis_scan`.
   - `scan.runner` executes `scan.graph` (fan-out per state).
   - `scan.state_worker` does planner -> tools -> synthesis text and optional incremental persistence.
   - `scan.persist.finalize_scan` updates scan totals and LGA risk table.
2. Synthesis flow:
   - `synthesis.runner.run_synthesis_dag` executes `synthesis.graph`.
   - Per-state node normalizes data + computes deterministic metrics + generates assessment JSON.
   - Rollup node aggregates assessments and generates rollup JSON.
   - Persistence updates `StateIntelligence.assessment_json` and `AegisScan.rollup_json`.
3. Report flow:
   - `report.runner.run_report_dag` executes report nodes in sequence.
   - Loads scan rollup + state assessments (+ optional simulation).
   - Generates narrative and infographics, builds PDF, persists report row, optionally uploads to GCS.
4. Simulation flow:
   - `simulator.runner.run_simulation_dag` executes deterministic projections then policy brief generation.
   - Persists scenario/projection/brief artifacts.
5. Marathon flow:
   - `marathon.runner.run_marathon_day` executes continuity graph.
   - Loads previous day context, computes deltas, generates continuity note.
   - Decides whether to auto-trigger simulation/report, then persists marathon-day artifacts.

## External Dependency Touchpoints

- PostgreSQL / SQLAlchemy:
  - Used across all persistence modules in `db`, `scan.persist`, `synthesis.persist`,
    `report.persist`, `simulator.persist`, and `marathon.persist`.
- Gemini API:
  - Scan planning/synthesis adapter: `scan.gemini_adapter`.
  - Grounded scan tools: `scan.grounding`, `scan.tools.*`.
  - Synthesis structured generation: `synthesis.llm`.
  - Report narrative and infographics: `report.narrative`, `report.infographics`.
  - Simulator policy brief: `simulator.llm`.
  - Marathon continuity note: `marathon.llm`.
- Google Search grounding:
  - Used by scan tools through `types.Tool(google_search=...)` in `scan.grounding`.
- Filesystem:
  - Report PDF output and infographic cache directories in `report.config` + `report.pdf/cache`.
- GCS:
  - Optional report upload in `report.nodes.persist_report_node` via `app.utils.gcs_store`.

## Notes and Assumptions

- Assumption: synthesis must run before report/simulator/marathon features that require rollup JSON.
- Assumption: `GOOGLE_API_KEY` is required for all LLM/grounded workflows.
- Assumption: timestamps are stored as naive UTC in DB models.
- TODO: verify production expectations for marathon autonomous trigger thresholds and action policies.
