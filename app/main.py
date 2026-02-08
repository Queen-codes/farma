"""FARMA API entrypoint (FastAPI).

- App wiring (middleware, static mounts, lifespan)
- Router registration

All route handlers live in `app/api/routes/`.
Shared helpers live in `app/api/helpers/`.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.helpers.paths import REPORTS_DIR
from app.api.helpers.startup import lifespan
from app.api.routes.aegis import router as aegis_router
from app.api.routes.farmer import router as farmer_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.system import router as system_router

app = FastAPI(
    title="Farma API",
    description="AI-powered agricultural assistance for Nigerian farmers. Includes AEGIS humanitarian intelligence system.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - demo friendly; tighten for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://gen-lang-client-0340377833.web.app",
        "https://gen-lang-client-0340377833.firebaseapp.com",
    ],
    allow_origin_regex=r"^https://([a-zA-Z0-9-]+\.)*(vercel\.app|aistudio\.google\.com|web\.app|firebaseapp\.com)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated PDFs (local dev). Cloud Run should prefer GCS.
if REPORTS_DIR.exists():
    app.mount(
        "/static/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports"
    )

# Routers
app.include_router(system_router)
app.include_router(farmer_router)
app.include_router(jobs_router)
app.include_router(aegis_router)
