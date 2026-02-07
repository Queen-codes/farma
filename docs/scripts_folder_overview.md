# Scripts Folder Overview

## Purpose

`/scripts` contains operational CLI utilities for local setup, migration, seeding,
smoke validation, API contract checking, and DB inspection.

These are manual/operator tools, not application runtime modules.

## Script Map

- `scripts/init_db.py`
  - Initializes DB schema via `app.aegis.db.connection.init_db`.
- `scripts/migrate_marathon_v2.py`
  - Applies additive Marathon v2 columns to `aegis_marathon_days`.
- `scripts/seed_demo_data.py`
  - Seeds deterministic demo `FarmerProfile` data.
- `scripts/seed_marathon_demo.py`
  - Seeds multi-week AEGIS scan/intelligence demo data and conflict events.
- `scripts/smoke_demo.py`
  - End-to-end API smoke run (`health -> scan -> report -> farmer job`).
- `scripts/contract_check.py`
  - Contract validator for API payload schemas and optional Phase 3 invariants.
- `scripts/view_db.py`
  - Read-only DB viewer for latest scan summaries and source URIs.

## Print Output Decision

Print/log output is intentionally kept in these scripts.

Reason:
- They are operator-facing CLI tools where progress visibility and immediate
  diagnostics are useful.
- Output is used as the primary feedback channel when run manually in terminal.

Noisy/debug-only ad-hoc script runners were moved/removed earlier; remaining
prints are task-oriented status output.

## External Dependencies

- Database access through SQLAlchemy async sessions/engine.
- HTTP calls to running API for smoke/contract scripts.
- Application models/services from `app.aegis`, `app.api`, and `app.workflows`.
