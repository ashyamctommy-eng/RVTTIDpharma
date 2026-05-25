"""
main.py
-------
Application factory for the D.Pharma Study Vault.
Wires together:
  - Lifespan (DB init, startup/shutdown hooks)
  - Static file mounts (uploaded_notes + static assets)
  - Jinja2 template engine
  - CORS middleware
  - API router
  - Root HTML page
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.routes import router as api_router
from core.config import settings
from core.database import init_db


# ── Lifespan ────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup; nothing special on shutdown."""
    await init_db()
    yield


# ── App factory ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS (permissive for local dev; tighten in production) ─────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static mounts ───────────────────────────────────────────────────────────────
# Serve uploaded files at /uploaded_notes/<filename>
app.mount(
    "/uploaded_notes",
    StaticFiles(directory=str(settings.UPLOAD_DIR)),
    name="uploaded_notes",
)

# Serve CSS/JS assets at /static/<asset>
app.mount(
    "/static",
    StaticFiles(directory=str(settings.STATIC_DIR)),
    name="static",
)

# ── Template engine ─────────────────────────────────────────────────────────────
templates = Jinja2Templates(directory=str(settings.TEMPLATES_DIR))

# ── API router ──────────────────────────────────────────────────────────────────
app.include_router(api_router)


# ── Root route: serve the SPA shell ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})
