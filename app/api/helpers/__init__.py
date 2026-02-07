"""Shared helper utilities for API routes and startup wiring.

This package groups small utilities that keep route modules focused on HTTP
contract logic instead of infrastructure details.

Key responsibilities:
- Runtime helpers (background tasks, clock helpers, env parsing).
- Security helpers for API token enforcement.
- Startup/bootstrap helpers for DB setup and scheduler startup.
- Shared filesystem path constants.

Used by:
- `app.main` during app startup.
- `app.api.routes.*` modules for auth, scheduling, and utility functions.
"""
