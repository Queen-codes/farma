"""FastAPI route packages for system, FARMA workflow, jobs, and AEGIS APIs.

Key responsibilities:
- Organize HTTP endpoints into focused route modules.
- Keep router wiring consistent for inclusion from `app.main`.

Used by:
- `app.main`, which imports each module's `router` and registers it on the app.

Assumptions:
- Route modules enforce auth for operational endpoints where required.
"""
