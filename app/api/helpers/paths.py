"""Filesystem paths shared across API modules.

Key responsibilities:
- Resolve the repository base directory from this module location.
- Provide the one true reports output directory for generated PDFs.
- Ensure the reports directory exists at import time.

Used by:
- `app.main` for static file mounting.
- `app.api.routes.aegis` for report generation and local listing.
- `app.api.helpers.aegis_scheduler` for scheduled report output.
- `app.aegis.marathon.nodes` for marathon-triggered report outputs.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
