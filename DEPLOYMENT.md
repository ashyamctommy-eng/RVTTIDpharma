# D.Pharma Study Vault — Deployment Guide
### GitHub Pages (Frontend) + Render (FastAPI Backend)

> **Reading time:** ~25 minutes  
> **Difficulty:** Beginner-friendly  
> **Cost:** Free tier on both platforms

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Final Repo Structure](#2-final-repo-structure)
3. [Code Changes Required](#3-code-changes-required)
   - 3.1 [core/config.py — ALLOWED_ORIGINS](#31-coreconfigpy--allowed_origins)
   - 3.2 [main.py — Dynamic CORS](#32-mainpy--dynamic-cors)
   - 3.3 [docs/index.html — Standalone Frontend](#33-docsindexhtml--standalone-frontend)
   - 3.4 [render.yaml — Render Manifest](#34-renderyaml--render-manifest)
   - 3.5 [.env.example](#35-envexample)
4. [GitHub Repo Setup](#4-github-repo-setup)
5. [Render Backend Setup](#5-render-backend-setup)
6. [Connecting Frontend to Backend](#6-connecting-frontend-to-backend)
7. [SQLite Persistence on Render](#7-sqlite-persistence-on-render)
8. [Final Checklist](#8-final-checklist)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        USER BROWSER                         │
└──────────────┬──────────────────────────┬───────────────────┘
               │ loads HTML/CSS/JS         │ API calls (HTTPS)
               ▼                           ▼
┌──────────────────────┐     ┌─────────────────────────────────┐
│   GitHub Pages       │     │   Render Web Service            │
│   (Static Host)      │     │   (FastAPI + Uvicorn)           │
│                      │     │                                 │
│  docs/index.html     │────▶│  POST /api/upload               │
│  (pure HTML/JS/CSS)  │     │  GET  /api/notes                │
│                      │     │  DELETE /api/notes/{id}         │
│  Free · Global CDN   │     │                                 │
│  Auto-deploy on push │     │  + Render Disk (SQLite + files) │
└──────────────────────┘     └─────────────────────────────────┘
```

**Key insight:** GitHub Pages can only serve _static files_. It cannot run Python. So we:
- Keep the Python/FastAPI code at the **repo root** (Render reads it from there)
- Put a standalone `docs/index.html` (pure HTML + JS, no server-side rendering) in the **`docs/` folder** (GitHub Pages reads it from there)
- The HTML file makes direct `fetch()` calls to your Render backend URL

---

## 2. Final Repo Structure

After all changes, your repository will look like this:

```
dpharma-portal/                   ← repo root
│
├── core/
│   ├── __init__.py
│   ├── config.py                 ← MODIFIED (add ALLOWED_ORIGINS)
│   └── database.py
│
├── models/
│   ├── __init__.py
│   └── resource.py
│
├── schemas/
│   ├── __init__.py
│   └── resource.py
│
├── api/
│   ├── __init__.py
│   └── routes.py
│
├── static/                       ← kept (Render serves it)
├── templates/
│   └── index.html                ← kept (used when running locally)
│
├── uploaded_notes/               ← on Render this lives on a Disk
│   └── .gitkeep
│
├── docs/
│   └── index.html                ← NEW: standalone SPA for GitHub Pages
│
├── main.py                       ← MODIFIED (dynamic CORS)
├── render.yaml                   ← NEW: Render deployment manifest
├── requirements.txt
├── .env.example                  ← NEW: template for env vars
├── .gitignore
└── DEPLOYMENT.md                 ← this file
```

> **Rule of thumb:** Everything Render needs lives at the repo root. Everything GitHub Pages serves lives in `docs/`.

---

## 3. Code Changes Required

### 3.1 `core/config.py` — ALLOWED_ORIGINS

Replace the entire `core/config.py` with the version below. The key addition is `ALLOWED_ORIGINS`, which reads a comma-separated list of allowed CORS origins from an environment variable so you never hard-code your GitHub Pages URL.

```python
# core/config.py

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Application ───────────────────────────────────────────────────────────
    APP_TITLE: str = "D.Pharma Study Vault"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── Paths ─────────────────────────────────────────────────────────────────
    BASE_DIR: Path = Path(__file__).resolve().parent.parent

    # On Render with a Disk, UPLOAD_DIR is overridden via env var to the
    # mount path (e.g. /var/data/uploaded_notes). Locally it stays relative.
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", str(Path(__file__).resolve().parent.parent / "uploaded_notes")))

    TEMPLATES_DIR: Path = BASE_DIR / "templates"
    STATIC_DIR: Path = BASE_DIR / "static"

    # ── Database ──────────────────────────────────────────────────────────────
    # On Render, set DATABASE_URL to point at the Disk mount path:
    # sqlite+aiosqlite:////var/data/dpharma_vault.db
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{Path(__file__).resolve().parent.parent / 'dpharma_vault.db'}"
    )

    # ── CORS Origins ──────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins.
    # Example: https://yourname.github.io,https://custom-domain.com
    # Set to * only in development. Always restrict in production.
    ALLOWED_ORIGINS_STR: str = os.getenv("ALLOWED_ORIGINS", "*")

    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        if self.ALLOWED_ORIGINS_STR.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS_STR.split(",") if origin.strip()]

    # ── Upload Constraints ────────────────────────────────────────────────────
    ALLOWED_EXTENSIONS: set = {".pdf", ".docx", ".doc", ".pptx", ".ppt"}
    MAX_UPLOAD_SIZE_MB: int = 50

    # ── Semesters ─────────────────────────────────────────────────────────────
    VALID_SEMESTERS: list = ["Y1S1", "Y1S2", "Y2S1", "Y2S2", "Y3S1", "Y3S2"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Guarantee the upload directory exists at import time
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
```

---

### 3.2 `main.py` — Dynamic CORS

Replace `main.py` with the version below. The only meaningful change is that `allow_origins` now reads from `settings.ALLOWED_ORIGINS` instead of the hard-coded `["*"]`.

```python
# main.py

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Dynamic CORS ────────────────────────────────────────────────────────────
# Origins come from the ALLOWED_ORIGINS environment variable.
# In production on Render, set this to your GitHub Pages URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static mounts ────────────────────────────────────────────────────────────
app.mount(
    "/uploaded_notes",
    StaticFiles(directory=str(settings.UPLOAD_DIR)),
    name="uploaded_notes",
)

app.mount(
    "/static",
    StaticFiles(directory=str(settings.STATIC_DIR)),
    name="static",
)

# ── Template engine ──────────────────────────────────────────────────────────
templates = Jinja2Templates(directory=str(settings.TEMPLATES_DIR))

# ── API router ───────────────────────────────────────────────────────────────
app.include_router(api_router)


# ── Root route ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})
```

---

### 3.3 `docs/index.html` — Standalone Frontend

Create a new file at `docs/index.html`. This is **not** rendered by Jinja2 — it is a plain HTML file that GitHub Pages serves directly. All API calls use a top-of-file `API_BASE` constant that you will update with your Render URL in [Section 6](#6-connecting-frontend-to-backend).

> **Why a separate file?** The `templates/index.html` uses Jinja2's `{{ request }}` and is served by FastAPI. GitHub Pages can't run Python, so it needs a self-contained HTML file.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>D.Pharma Study Vault</title>

  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            vault: {
              50:  '#f0f9ff', 100: '#e0f2fe', 200: '#bae6fd',
              300: '#7dd3fc', 400: '#38bdf8', 500: '#0ea5e9',
              600: '#0284c7', 700: '#0369a1', 800: '#075985', 900: '#0c4a6e',
            },
          },
          fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
        },
      },
    };
  </script>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet" />

  <style>
    *, *::before, *::after { box-sizing: border-box; }
    #drop-zone.drag-over { border-color: #0ea5e9; background-color: #f0f9ff; }
    @keyframes spin  { to { transform: rotate(360deg); } }
    .spinner { animation: spin 0.8s linear infinite; }
    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(12px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .card-enter { animation: fadeUp 0.25s ease both; }
    .pill-active { background-color: #0ea5e9; color: #ffffff; }
    #toast { transition: opacity 0.35s ease, transform 0.35s ease; }
    #toast.hidden  { opacity: 0; transform: translateY(16px); pointer-events: none; }
    #toast.visible { opacity: 1; transform: translateY(0); }
  </style>
</head>

<body class="bg-slate-50 text-slate-800 min-h-screen flex flex-col font-sans antialiased">

  <!-- ══ HEADER ══════════════════════════════════════════════════════════════ -->
  <header class="bg-gradient-to-r from-vault-800 to-vault-600 shadow-lg">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 bg-white/20 rounded-xl flex items-center justify-center backdrop-blur-sm">
          <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.746 0 3.332.477 4.5 1.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
        </div>
        <div>
          <h1 class="text-xl font-bold text-white tracking-tight">D.Pharma Study Vault</h1>
          <p class="text-vault-200 text-xs font-medium tracking-wide">Diploma in Pharmaceutical Technology</p>
        </div>
      </div>
      <div class="hidden sm:flex items-center gap-2 bg-white/15 backdrop-blur-sm rounded-xl px-4 py-2">
        <svg class="w-4 h-4 text-vault-200" fill="currentColor" viewBox="0 0 20 20">
          <path d="M9 4.804A7.968 7.968 0 005.5 4c-1.255 0-2.443.29-3.5.804v10A7.969 7.969 0 015.5 14c1.669 0 3.218.51 4.5 1.385A7.962 7.962 0 0114.5 14c1.255 0 2.443.29 3.5.804v-10A7.968 7.968 0 0014.5 4c-1.255 0-2.443.29-3.5.804V12a1 1 0 11-2 0V4.804z" />
        </svg>
        <span class="text-white text-sm font-semibold" id="header-count">0</span>
        <span class="text-vault-200 text-xs">resources</span>
      </div>
    </div>
  </header>

  <!-- ══ MAIN ════════════════════════════════════════════════════════════════ -->
  <main class="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-8 space-y-8">

    <!-- Upload Panel -->
    <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
      <div class="border-b border-slate-100 px-6 py-4 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <svg class="w-5 h-5 text-vault-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
          <h2 class="text-base font-semibold text-slate-700">Upload Resource</h2>
        </div>
        <button id="toggle-upload" class="text-slate-400 hover:text-slate-600 transition-colors">
          <svg id="toggle-icon" class="w-5 h-5 transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
          </svg>
        </button>
      </div>

      <div id="upload-body" class="p-6">
        <form id="upload-form" novalidate class="space-y-5">
          <div id="drop-zone"
            class="border-2 border-dashed border-slate-300 rounded-xl p-8 text-center cursor-pointer transition-all duration-200 hover:border-vault-400 hover:bg-vault-50">
            <input type="file" id="file-input" name="file" class="hidden" accept=".pdf,.docx,.doc,.pptx,.ppt" />
            <div id="drop-idle" class="space-y-2">
              <div class="flex justify-center">
                <div class="w-14 h-14 bg-vault-50 rounded-xl flex items-center justify-center">
                  <svg class="w-7 h-7 text-vault-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
              </div>
              <p class="text-slate-600 font-medium text-sm">Drag & drop your file here, or
                <span class="text-vault-600 font-semibold cursor-pointer hover:underline">browse</span>
              </p>
              <p class="text-slate-400 text-xs">PDF, DOCX, DOC, PPTX, PPT — up to 50 MB</p>
            </div>
            <div id="drop-selected" class="hidden space-y-1">
              <div class="flex justify-center">
                <div class="w-12 h-12 bg-emerald-50 rounded-xl flex items-center justify-center">
                  <svg class="w-6 h-6 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
              </div>
              <p id="selected-name" class="text-slate-700 font-semibold text-sm break-all"></p>
              <p id="selected-size" class="text-slate-400 text-xs"></p>
              <button type="button" id="clear-file" class="text-xs text-red-400 hover:text-red-600 underline mt-1">Remove file</button>
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="sm:col-span-2">
              <label for="input-title" class="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                Document Title <span class="text-red-400">*</span>
              </label>
              <input id="input-title" name="title" type="text" required
                placeholder="e.g. Pharmaceutics I: Introduction to Dosage Forms"
                class="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-vault-400 focus:border-transparent transition" />
              <p id="err-title" class="text-xs text-red-500 mt-1 hidden">Title is required.</p>
            </div>
            <div>
              <label for="input-subject" class="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                Subject / Module <span class="text-red-400">*</span>
              </label>
              <input id="input-subject" name="subject" type="text" required
                placeholder="e.g. Pharmacology"
                class="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-vault-400 focus:border-transparent transition" />
              <p id="err-subject" class="text-xs text-red-500 mt-1 hidden">Subject is required.</p>
            </div>
            <div>
              <label for="input-semester" class="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                Semester <span class="text-red-400">*</span>
              </label>
              <select id="input-semester" name="semester" required
                class="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-vault-400 focus:border-transparent transition appearance-none cursor-pointer">
                <option value="" disabled selected>Select semester…</option>
                <option value="Y1S1">Year 1 – Semester 1</option>
                <option value="Y1S2">Year 1 – Semester 2</option>
                <option value="Y2S1">Year 2 – Semester 1</option>
                <option value="Y2S2">Year 2 – Semester 2</option>
                <option value="Y3S1">Year 3 – Semester 1</option>
                <option value="Y3S2">Year 3 – Semester 2</option>
              </select>
              <p id="err-semester" class="text-xs text-red-500 mt-1 hidden">Semester is required.</p>
            </div>
          </div>

          <div class="flex justify-end">
            <button id="submit-btn" type="submit"
              class="inline-flex items-center gap-2 bg-vault-600 hover:bg-vault-700 active:bg-vault-800 text-white font-semibold text-sm px-6 py-2.5 rounded-xl transition-all duration-150 shadow-sm hover:shadow-md disabled:opacity-60 disabled:cursor-not-allowed">
              <svg id="btn-icon" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
              </svg>
              <svg id="btn-spinner" class="w-4 h-4 spinner hidden" viewBox="0 0 24 24" fill="none">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
              </svg>
              <span id="btn-text">Upload to Vault</span>
            </button>
          </div>
        </form>
      </div>
    </section>

    <!-- Filter & Search Hub -->
    <section class="space-y-4">
      <div class="relative">
        <svg class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none"
          fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0" />
        </svg>
        <input id="search-input" type="text" placeholder="Search by title, subject…"
          class="w-full pl-10 pr-4 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-vault-400 focus:border-transparent shadow-sm transition" />
        <button id="clear-search" class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-500 hidden transition-colors">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div class="flex flex-wrap gap-2 items-center">
        <span class="text-xs font-semibold text-slate-400 uppercase tracking-wide mr-1">Filter:</span>
        <button class="semester-pill pill-active text-xs font-semibold px-3.5 py-1.5 rounded-full border border-vault-200 transition-all duration-150 hover:shadow-sm" data-semester="ALL">All</button>
        <button class="semester-pill text-xs font-semibold px-3.5 py-1.5 rounded-full border border-slate-200 bg-white text-slate-600 transition-all duration-150 hover:shadow-sm hover:border-vault-300 hover:text-vault-600" data-semester="Y1S1">Y1 – S1</button>
        <button class="semester-pill text-xs font-semibold px-3.5 py-1.5 rounded-full border border-slate-200 bg-white text-slate-600 transition-all duration-150 hover:shadow-sm hover:border-vault-300 hover:text-vault-600" data-semester="Y1S2">Y1 – S2</button>
        <button class="semester-pill text-xs font-semibold px-3.5 py-1.5 rounded-full border border-slate-200 bg-white text-slate-600 transition-all duration-150 hover:shadow-sm hover:border-vault-300 hover:text-vault-600" data-semester="Y2S1">Y2 – S1</button>
        <button class="semester-pill text-xs font-semibold px-3.5 py-1.5 rounded-full border border-slate-200 bg-white text-slate-600 transition-all duration-150 hover:shadow-sm hover:border-vault-300 hover:text-vault-600" data-semester="Y2S2">Y2 – S2</button>
        <button class="semester-pill text-xs font-semibold px-3.5 py-1.5 rounded-full border border-slate-200 bg-white text-slate-600 transition-all duration-150 hover:shadow-sm hover:border-vault-300 hover:text-vault-600" data-semester="Y3S1">Y3 – S1</button>
        <button class="semester-pill text-xs font-semibold px-3.5 py-1.5 rounded-full border border-slate-200 bg-white text-slate-600 transition-all duration-150 hover:shadow-sm hover:border-vault-300 hover:text-vault-600" data-semester="Y3S2">Y3 – S2</button>
      </div>
    </section>

    <!-- Resource Grid -->
    <section>
      <div class="flex items-center justify-between mb-4">
        <p class="text-sm text-slate-500">
          Showing <span id="result-count" class="font-semibold text-slate-700">0</span> resource(s)
        </p>
        <div id="loading-bar" class="hidden flex items-center gap-2 text-xs text-vault-500">
          <svg class="w-3.5 h-3.5 spinner" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
          Loading…
        </div>
      </div>

      <div id="cards-grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"></div>

      <div id="empty-state" class="hidden flex flex-col items-center justify-center py-20 space-y-3 text-center">
        <div class="w-16 h-16 bg-slate-100 rounded-2xl flex items-center justify-center">
          <svg class="w-8 h-8 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        </div>
        <p class="text-slate-500 font-medium text-sm">No resources found</p>
        <p class="text-slate-400 text-xs">Adjust your filters, or upload the first resource above.</p>
      </div>
    </section>

  </main>

  <!-- ══ FOOTER ══════════════════════════════════════════════════════════════ -->
  <footer class="mt-auto border-t border-slate-200 bg-white">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-5 flex flex-col sm:flex-row items-center justify-between gap-2">
      <div class="flex items-center gap-2">
        <div class="w-6 h-6 bg-vault-600 rounded-md flex items-center justify-center">
          <svg class="w-3.5 h-3.5 text-white" fill="currentColor" viewBox="0 0 20 20">
            <path d="M9 4.804A7.968 7.968 0 005.5 4c-1.255 0-2.443.29-3.5.804v10A7.969 7.969 0 015.5 14c1.669 0 3.218.51 4.5 1.385A7.962 7.962 0 0114.5 14c1.255 0 2.443.29 3.5.804v-10A7.968 7.968 0 0014.5 4c-1.255 0-2.443.29-3.5.804V12a1 1 0 11-2 0V4.804z" />
          </svg>
        </div>
        <span class="text-sm text-gray-500 tracking-wide">D.Pharma Study Vault</span>
      </div>
      <div class="flex flex-col sm:flex-row items-center gap-1 sm:gap-4">
        <span class="text-sm text-gray-500 tracking-wide">Built by <strong class="font-semibold text-gray-600">P.o.Riot</strong></span>
        <span class="hidden sm:block text-slate-200">|</span>
        <span class="text-sm text-gray-500 tracking-wide">Credits <strong class="font-semibold text-gray-600">P.o.Riot</strong></span>
      </div>
    </div>
  </footer>

  <!-- ══ TOAST ═══════════════════════════════════════════════════════════════ -->
  <div id="toast" role="alert"
    class="hidden fixed bottom-6 right-6 max-w-sm z-50 rounded-xl shadow-lg px-5 py-3.5 flex items-center gap-3 text-sm font-medium">
    <svg id="toast-icon" class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"></svg>
    <span id="toast-msg"></span>
  </div>

  <!-- ══ DELETE CONFIRMATION MODAL ═══════════════════════════════════════════ -->
  <div id="confirm-modal" class="hidden fixed inset-0 z-40 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
    <div class="bg-white rounded-2xl shadow-2xl max-w-sm w-full p-6 space-y-4">
      <div class="flex items-start gap-3">
        <div class="w-10 h-10 bg-red-50 rounded-xl flex items-center justify-center flex-shrink-0">
          <svg class="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </div>
        <div>
          <h3 class="text-slate-800 font-semibold text-base">Delete Resource?</h3>
          <p class="text-slate-500 text-sm mt-1" id="confirm-msg">This will permanently remove the file and its metadata.</p>
        </div>
      </div>
      <div class="flex justify-end gap-3">
        <button id="confirm-cancel" class="px-4 py-2 text-sm font-medium text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-lg transition">Cancel</button>
        <button id="confirm-delete" class="px-4 py-2 text-sm font-medium text-white bg-red-500 hover:bg-red-600 active:bg-red-700 rounded-lg transition shadow-sm">Delete</button>
      </div>
    </div>
  </div>

  <!-- ══ JAVASCRIPT ══════════════════════════════════════════════════════════ -->
  <script>
  (() => {
    'use strict';

    // ┌─────────────────────────────────────────────────────────────────────┐
    // │  ★  CONFIGURATION  ★                                                │
    // │  After deploying to Render, replace the placeholder below with your │
    // │  actual Render service URL, e.g.:                                   │
    // │  const API_BASE = 'https://dpharma-vault.onrender.com';             │
    // │  Leave empty string '' to use the same origin (local dev).          │
    // └─────────────────────────────────────────────────────────────────────┘
    const API_BASE = 'https://YOUR-RENDER-SERVICE-NAME.onrender.com';

    /* ── State ──────────────────────────────────────────────────────────── */
    let allNotes        = [];
    let activeSemester  = 'ALL';
    let searchQuery     = '';
    let pendingDeleteId = null;

    /* ── DOM refs ───────────────────────────────────────────────────────── */
    const $  = id  => document.getElementById(id);
    const $$ = sel => document.querySelectorAll(sel);

    const uploadForm     = $('upload-form');
    const dropZone       = $('drop-zone');
    const fileInput      = $('file-input');
    const dropIdle       = $('drop-idle');
    const dropSelected   = $('drop-selected');
    const selectedName   = $('selected-name');
    const selectedSize   = $('selected-size');
    const clearFileBtn   = $('clear-file');
    const toggleUpload   = $('toggle-upload');
    const toggleIcon     = $('toggle-icon');
    const uploadBody     = $('upload-body');
    const titleInput     = $('input-title');
    const subjectInput   = $('input-subject');
    const semesterSelect = $('input-semester');
    const submitBtn      = $('submit-btn');
    const btnIcon        = $('btn-icon');
    const btnSpinner     = $('btn-spinner');
    const btnText        = $('btn-text');
    const searchInput    = $('search-input');
    const clearSearch    = $('clear-search');
    const cardsGrid      = $('cards-grid');
    const emptyState     = $('empty-state');
    const resultCount    = $('result-count');
    const headerCount    = $('header-count');
    const loadingBar     = $('loading-bar');
    const toast          = $('toast');
    const toastIcon      = $('toast-icon');
    const toastMsg       = $('toast-msg');
    const confirmModal   = $('confirm-modal');
    const confirmMsg     = $('confirm-msg');
    const confirmCancel  = $('confirm-cancel');
    const confirmDelete  = $('confirm-delete');

    /* ── Helpers ────────────────────────────────────────────────────────── */
    function apiUrl(path) {
      // If API_BASE is set, prepend it; otherwise use relative path (local dev)
      return API_BASE ? `${API_BASE}${path}` : path;
    }

    function formatBytes(bytes) {
      if (bytes < 1024)        return `${bytes} B`;
      if (bytes < 1024*1024)   return `${(bytes/1024).toFixed(1)} KB`;
      return `${(bytes/(1024*1024)).toFixed(1)} MB`;
    }

    function formatDate(iso) {
      return new Date(iso).toLocaleDateString('en-US', { year:'numeric', month:'short', day:'numeric' });
    }

    function extIcon(filename) {
      const ext = filename.split('.').pop().toLowerCase();
      const icons = {
        pdf:  { color:'text-red-500',    bg:'bg-red-50',    label:'PDF'  },
        docx: { color:'text-blue-500',   bg:'bg-blue-50',   label:'DOCX' },
        doc:  { color:'text-blue-500',   bg:'bg-blue-50',   label:'DOC'  },
        pptx: { color:'text-orange-500', bg:'bg-orange-50', label:'PPTX' },
        ppt:  { color:'text-orange-500', bg:'bg-orange-50', label:'PPT'  },
      };
      return icons[ext] || { color:'text-slate-500', bg:'bg-slate-100', label:ext.toUpperCase() };
    }

    function semesterLabel(code) {
      const map = { Y1S1:'Year 1 · S1', Y1S2:'Year 1 · S2', Y2S1:'Year 2 · S1', Y2S2:'Year 2 · S2', Y3S1:'Year 3 · S1', Y3S2:'Year 3 · S2' };
      return map[code] || code;
    }

    function semesterColor(code) {
      const map = {
        Y1S1:'bg-violet-100 text-violet-700', Y1S2:'bg-purple-100 text-purple-700',
        Y2S1:'bg-sky-100 text-sky-700',       Y2S2:'bg-vault-100 text-vault-700',
        Y3S1:'bg-teal-100 text-teal-700',     Y3S2:'bg-emerald-100 text-emerald-700',
      };
      return map[code] || 'bg-slate-100 text-slate-600';
    }

    function escHtml(str) {
      return String(str)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    /* ── Toast ──────────────────────────────────────────────────────────── */
    let toastTimer = null;
    function showToast(msg, type = 'success') {
      const isSuccess = type === 'success';
      const isError   = type === 'error';
      toast.className = `fixed bottom-6 right-6 max-w-sm z-50 rounded-xl shadow-lg px-5 py-3.5 flex items-center gap-3 text-sm font-medium visible ${
        isSuccess ? 'bg-emerald-50 text-emerald-800 border border-emerald-200' :
        isError   ? 'bg-red-50 text-red-800 border border-red-200' :
                    'bg-blue-50 text-blue-800 border border-blue-200'
      }`;
      toastIcon.innerHTML = isSuccess
        ? '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />'
        : '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />';
      toastIcon.setAttribute('stroke', isSuccess ? '#059669' : '#dc2626');
      toastMsg.textContent = msg;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { toast.classList.add('hidden'); }, 4000);
    }

    /* ── Upload panel collapse ──────────────────────────────────────────── */
    let uploadOpen = true;
    toggleUpload.addEventListener('click', () => {
      uploadOpen = !uploadOpen;
      uploadBody.style.display = uploadOpen ? '' : 'none';
      toggleIcon.style.transform = uploadOpen ? '' : 'rotate(180deg)';
    });

    /* ── File selection ─────────────────────────────────────────────────── */
    function setSelectedFile(file) {
      if (!file) return;
      const allowed = ['.pdf','.docx','.doc','.pptx','.ppt'];
      const ext = '.' + file.name.split('.').pop().toLowerCase();
      if (!allowed.includes(ext)) { showToast(`File type "${ext}" is not allowed.`, 'error'); return; }
      selectedName.textContent = file.name;
      selectedSize.textContent = formatBytes(file.size);
      dropIdle.classList.add('hidden');
      dropSelected.classList.remove('hidden');
    }

    dropZone.addEventListener('click', (e) => { if (e.target !== clearFileBtn) fileInput.click(); });
    fileInput.addEventListener('change', () => { if (fileInput.files[0]) setSelectedFile(fileInput.files[0]); });
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault(); dropZone.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (file) { const dt = new DataTransfer(); dt.items.add(file); fileInput.files = dt.files; setSelectedFile(file); }
    });
    clearFileBtn.addEventListener('click', (e) => {
      e.stopPropagation(); fileInput.value = '';
      dropSelected.classList.add('hidden'); dropIdle.classList.remove('hidden');
    });

    /* ── Validation ─────────────────────────────────────────────────────── */
    function validate() {
      let ok = true;
      [[titleInput, $('err-title'), titleInput.value.trim().length < 2],
       [subjectInput, $('err-subject'), subjectInput.value.trim().length === 0],
       [semesterSelect, $('err-semester'), semesterSelect.value === '']
      ].forEach(([input, errEl, failed]) => {
        if (failed) { errEl.classList.remove('hidden'); input.classList.add('border-red-400'); ok = false; }
        else        { errEl.classList.add('hidden');    input.classList.remove('border-red-400'); }
      });
      if (!fileInput.files || fileInput.files.length === 0) { showToast('Please select a file to upload.', 'error'); ok = false; }
      return ok;
    }

    /* ── Upload ─────────────────────────────────────────────────────────── */
    uploadForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!validate()) return;

      submitBtn.disabled = true;
      btnIcon.classList.add('hidden');
      btnSpinner.classList.remove('hidden');
      btnText.textContent = 'Uploading…';

      const fd = new FormData();
      fd.append('file', fileInput.files[0]);
      fd.append('title',    titleInput.value.trim());
      fd.append('subject',  subjectInput.value.trim());
      fd.append('semester', semesterSelect.value);

      try {
        const res  = await fetch(apiUrl('/api/upload'), { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok) { showToast(data.detail || 'Upload failed.', 'error'); return; }
        showToast('Resource uploaded successfully! 🎉', 'success');
        uploadForm.reset(); fileInput.value = '';
        dropSelected.classList.add('hidden'); dropIdle.classList.remove('hidden');
        await loadNotes();
      } catch {
        showToast('Network error — please try again.', 'error');
      } finally {
        submitBtn.disabled = false;
        btnIcon.classList.remove('hidden'); btnSpinner.classList.add('hidden');
        btnText.textContent = 'Upload to Vault';
      }
    });

    /* ── Load notes ─────────────────────────────────────────────────────── */
    async function loadNotes() {
      loadingBar.classList.remove('hidden');
      try {
        const res  = await fetch(apiUrl('/api/notes'));
        const data = await res.json();
        allNotes = data.items || [];
        headerCount.textContent = allNotes.length;
      } catch {
        showToast('Could not connect to the server. Is the backend running?', 'error');
        allNotes = [];
      } finally {
        loadingBar.classList.add('hidden');
        renderCards();
      }
    }

    /* ── Render cards ───────────────────────────────────────────────────── */
    function renderCards() {
      const q = searchQuery.toLowerCase();
      const filtered = allNotes.filter(n => {
        const matchSem  = activeSemester === 'ALL' || n.semester === activeSemester;
        const matchText = !q || n.title.toLowerCase().includes(q) || n.subject.toLowerCase().includes(q);
        return matchSem && matchText;
      });

      resultCount.textContent = filtered.length;
      cardsGrid.innerHTML = '';

      if (filtered.length === 0) { emptyState.classList.remove('hidden'); return; }
      emptyState.classList.add('hidden');

      filtered.forEach((note, i) => {
        const icon = extIcon(note.file_name);
        // File download URL: point directly at the Render backend
        const fileUrl = API_BASE
          ? `${API_BASE}${note.file_path}`
          : note.file_path;

        const card = document.createElement('div');
        card.className = 'card-enter bg-white rounded-2xl border border-slate-200 shadow-sm hover:shadow-md transition-shadow duration-200 flex flex-col overflow-hidden';
        card.style.animationDelay = `${i * 30}ms`;
        card.innerHTML = `
          <div class="p-5 flex-1 space-y-3">
            <div class="flex items-start gap-3">
              <div class="w-10 h-10 ${icon.bg} rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5">
                <span class="${icon.color} text-xs font-bold">${icon.label}</span>
              </div>
              <h3 class="text-slate-800 font-semibold text-sm leading-snug line-clamp-2 flex-1">${escHtml(note.title)}</h3>
            </div>
            <div class="flex flex-wrap gap-2">
              <span class="inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full bg-slate-100 text-slate-600">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
                </svg>
                ${escHtml(note.subject)}
              </span>
              <span class="inline-flex items-center text-xs font-semibold px-2.5 py-1 rounded-full ${semesterColor(note.semester)}">
                ${semesterLabel(note.semester)}
              </span>
            </div>
            <p class="text-slate-400 text-xs flex items-center gap-1">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              ${formatDate(note.upload_date)}
            </p>
          </div>
          <div class="border-t border-slate-100 px-4 py-3 flex items-center justify-between bg-slate-50/50">
            <a href="${fileUrl}" target="_blank" rel="noopener noreferrer"
              class="inline-flex items-center gap-1.5 text-xs font-semibold text-vault-600 hover:text-vault-800 transition-colors">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              View / Download
            </a>
            <button class="delete-btn inline-flex items-center gap-1 text-xs font-medium text-slate-400 hover:text-red-500 transition-colors"
              data-id="${note.id}" data-title="${escHtml(note.title)}">
              <svg class="w-3.5 h-3.5 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              Delete
            </button>
          </div>
        `;
        cardsGrid.appendChild(card);
      });

      $$('.delete-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          pendingDeleteId = parseInt(btn.dataset.id, 10);
          confirmMsg.textContent = `"${btn.dataset.title}" will be permanently removed.`;
          confirmModal.classList.remove('hidden');
        });
      });
    }

    /* ── Semester pills ─────────────────────────────────────────────────── */
    $$('.semester-pill').forEach(pill => {
      pill.addEventListener('click', () => {
        $$('.semester-pill').forEach(p => {
          p.classList.remove('pill-active');
          p.classList.add('bg-white','text-slate-600','border-slate-200');
        });
        pill.classList.add('pill-active');
        pill.classList.remove('bg-white','text-slate-600','border-slate-200');
        activeSemester = pill.dataset.semester;
        renderCards();
      });
    });

    /* ── Search ─────────────────────────────────────────────────────────── */
    searchInput.addEventListener('input', () => {
      searchQuery = searchInput.value;
      clearSearch.classList.toggle('hidden', !searchQuery);
      renderCards();
    });
    clearSearch.addEventListener('click', () => {
      searchInput.value = ''; searchQuery = '';
      clearSearch.classList.add('hidden'); renderCards();
    });

    /* ── Delete modal ───────────────────────────────────────────────────── */
    confirmCancel.addEventListener('click', () => {
      confirmModal.classList.add('hidden'); pendingDeleteId = null;
    });

    confirmDelete.addEventListener('click', async () => {
      if (!pendingDeleteId) return;
      confirmModal.classList.add('hidden');
      try {
        const res  = await fetch(apiUrl(`/api/notes/${pendingDeleteId}`), { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) { showToast(data.detail || 'Delete failed.', 'error'); return; }
        showToast('Resource removed from the vault.', 'success');
        await loadNotes();
      } catch {
        showToast('Network error while deleting.', 'error');
      } finally {
        pendingDeleteId = null;
      }
    });

    confirmModal.addEventListener('click', (e) => {
      if (e.target === confirmModal) { confirmModal.classList.add('hidden'); pendingDeleteId = null; }
    });

    /* ── Boot ───────────────────────────────────────────────────────────── */
    loadNotes();
  })();
  </script>

</body>
</html>
```

---

### 3.4 `render.yaml` — Render Deployment Manifest

Create `render.yaml` at the **repo root**. Render reads this file automatically when you link your repo.

```yaml
# render.yaml
# Render Blueprint — deploys the FastAPI backend as a Web Service
# and provisions a persistent Disk for SQLite + uploaded files.

services:
  - type: web
    name: dpharma-vault
    runtime: python
    region: oregon          # or: frankfurt, singapore, ohio
    plan: free              # upgrade to starter ($7/mo) for always-on

    # ── Build ──────────────────────────────────────────────────────────────
    buildCommand: pip install -r requirements.txt

    # ── Start ──────────────────────────────────────────────────────────────
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT

    # ── Environment Variables ───────────────────────────────────────────────
    envVars:
      - key: PYTHON_VERSION
        value: "3.11.0"

      # Point the DB at the persistent Disk mount path
      - key: DATABASE_URL
        value: sqlite+aiosqlite:////var/data/dpharma_vault.db

      # Point file uploads at the persistent Disk mount path
      - key: UPLOAD_DIR
        value: /var/data/uploaded_notes

      # Replace with your actual GitHub Pages URL after enabling Pages.
      # Format: https://<username>.github.io/<repo-name>
      # You can also add http://localhost:8000 for local dev, comma-separated.
      - key: ALLOWED_ORIGINS
        value: https://YOUR-GITHUB-USERNAME.github.io

    # ── Persistent Disk ─────────────────────────────────────────────────────
    # This keeps your SQLite DB and uploaded files across deploys/restarts.
    # Free tier: Disks are NOT available — see Section 7 for workarounds.
    # Paid tier (starter+): uncomment the block below.
    #
    # disk:
    #   name: dpharma-data
    #   mountPath: /var/data
    #   sizeGB: 1
```

> **Note:** The `disk:` block is commented out because Render Disks require a paid plan (Starter, $7/month). Section 7 explains your options on the free tier.

---

### 3.5 `.env.example`

Create `.env.example` at the repo root. This is a **template only** — never commit a real `.env` file.

```dotenv
# .env.example
# Copy this to .env for local development. Never commit .env to Git.

# ── Application ──────────────────────────────────────────────────────────────
DEBUG=false

# ── Database (local SQLite) ───────────────────────────────────────────────────
# Leave blank to use the default path (dpharma_vault.db at project root)
DATABASE_URL=

# ── File storage (local) ─────────────────────────────────────────────────────
# Leave blank to use the default path (uploaded_notes/ at project root)
UPLOAD_DIR=

# ── CORS ─────────────────────────────────────────────────────────────────────
# Comma-separated list of allowed origins.
# Use * for development. Use your GitHub Pages URL in production.
# Example: https://yourname.github.io,http://localhost:3000
ALLOWED_ORIGINS=*
```

---

## 4. GitHub Repo Setup

### Step 1 — Create the `docs/` folder

From inside your `dpharma-portal/` project directory, run:

```bash
mkdir -p docs
```

Then save the `docs/index.html` file from [Section 3.3](#33-docsindexhtml--standalone-frontend) into it.

---

### Step 2 — Update `.gitignore`

Make sure these lines are present in `.gitignore` so you don't commit secrets or binary blobs:

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/

# Database — never commit the SQLite file
*.db
*.db-shm
*.db-wal

# Uploaded files
uploaded_notes/*
!uploaded_notes/.gitkeep

# Environment secrets
.env

# OS
.DS_Store
Thumbs.db
```

---

### Step 3 — Initialize Git and push to GitHub

```bash
# 1. Navigate into the project folder
cd dpharma-portal

# 2. Initialize a git repo (skip if already done)
git init

# 3. Stage everything
git add .

# 4. First commit
git commit -m "feat: initial D.Pharma Study Vault — FastAPI backend + GitHub Pages frontend"

# 5. Create a new repo on GitHub.
#    Go to https://github.com/new
#    Name it:  dpharma-vault  (or any name you like)
#    Visibility: Public (required for free GitHub Pages)
#    Do NOT initialize with README, .gitignore, or license (you already have them)
#    Click "Create repository"

# 6. Add GitHub as remote origin (replace YOUR-USERNAME)
git remote add origin https://github.com/YOUR-USERNAME/dpharma-vault.git

# 7. Push
git branch -M main
git push -u origin main
```

---

### Step 4 — Enable GitHub Pages from the `docs/` folder

1. On GitHub, open your repository page.
2. Click **Settings** (top navigation bar, the gear icon).
3. In the left sidebar, scroll down and click **Pages**.
4. Under **"Build and deployment"**:
   - **Source**: Deploy from a branch
   - **Branch**: `main`
   - **Folder**: `/docs`
5. Click **Save**.
6. Wait ~60 seconds. Refresh the page.
7. GitHub will display a green banner:
   > **"Your site is live at `https://YOUR-USERNAME.github.io/dpharma-vault/`"**

8. **Copy this URL** — you will need it in the next section.

---

## 5. Render Backend Setup

### Step 1 — Sign up / log in to Render

Go to [https://render.com](https://render.com) and sign up with your GitHub account. This grants Render permission to read your repositories.

---

### Step 2 — Create a new Web Service

1. From the Render Dashboard, click **"New +"** → **"Web Service"**.
2. Click **"Connect a repository"** and select your `dpharma-vault` repo.
3. Render will auto-detect the `render.yaml` blueprint. If it does:
   - Click **"Apply"** to create the service from the blueprint.
   - Skip to Step 4.
4. If Render does **not** detect the blueprint, configure manually:
   - **Name**: `dpharma-vault`
   - **Region**: Oregon (or closest to you)
   - **Branch**: `main`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

---

### Step 3 — Set Environment Variables

In the Render web service settings, go to **"Environment"** and add these key-value pairs:

| Key | Value |
|-----|-------|
| `DATABASE_URL` | `sqlite+aiosqlite:////var/data/dpharma_vault.db` |
| `UPLOAD_DIR` | `/var/data/uploaded_notes` |
| `ALLOWED_ORIGINS` | `https://YOUR-USERNAME.github.io` |
| `PYTHON_VERSION` | `3.11.0` |

> ⚠️ Replace `YOUR-USERNAME` with your actual GitHub username. If your Pages URL includes the repo name (e.g. `https://foo.github.io/dpharma-vault`), paste the **full URL** including the path prefix.

---

### Step 4 — Deploy

1. Click **"Create Web Service"** (or **"Manual Deploy → Deploy latest commit"** if it already exists).
2. Watch the build logs. A successful deploy ends with:
   ```
   INFO:     Application startup complete.
   INFO:     Uvicorn running on http://0.0.0.0:10000
   ```
3. Render assigns your service a URL:
   ```
   https://dpharma-vault.onrender.com
   ```
   Copy this URL.

---

### Step 5 — Test the backend directly

Open your browser and visit:
```
https://dpharma-vault.onrender.com/api/notes
```
You should see:
```json
{"total": 0, "items": []}
```

Also visit the auto-generated API docs:
```
https://dpharma-vault.onrender.com/docs
```

---

## 6. Connecting Frontend to Backend

Now you have both URLs. It's time to wire them together.

### Edit `docs/index.html`

Open `docs/index.html` and find this block near the top of the `<script>` section (around line 5 inside the IIFE):

```javascript
// ┌─────────────────────────────────────────────────────────────────────┐
// │  ★  CONFIGURATION  ★                                                │
// └─────────────────────────────────────────────────────────────────────┘
const API_BASE = 'https://YOUR-RENDER-SERVICE-NAME.onrender.com';
```

Replace the placeholder URL with your actual Render URL:

```javascript
const API_BASE = 'https://dpharma-vault.onrender.com';
```

Save the file, then push to GitHub:

```bash
git add docs/index.html
git commit -m "config: connect frontend to Render backend"
git push
```

GitHub Pages will automatically rebuild and serve the updated file within ~60 seconds.

### Verify the full stack

1. Visit your GitHub Pages URL: `https://YOUR-USERNAME.github.io/dpharma-vault/`
2. The page should load with 0 resources.
3. Upload a PDF using the form.
4. The card should appear in the grid.
5. Click **View / Download** — the file opens from the Render URL.

---

## 7. SQLite Persistence on Render

### The problem with Render's free tier

> **Render free tier Web Services use an ephemeral filesystem.**
> Every time your service restarts (which happens after 15 minutes of inactivity on the free plan), the local disk is wiped. Any SQLite database and uploaded files stored locally will be **lost**.

This means on the free tier, your data resets every cold start.

### Option A — Upgrade to Render Starter ($7/month) + Add a Disk

This is the cleanest solution. With a paid plan:

1. In your Render service, go to **"Disks"** → **"Add Disk"**.
2. Configure:
   - **Name**: `dpharma-data`
   - **Mount Path**: `/var/data`
   - **Size**: `1 GB` (minimum)
3. Click **"Save"**.
4. Make sure your environment variables are:
   ```
   DATABASE_URL = sqlite+aiosqlite:////var/data/dpharma_vault.db
   UPLOAD_DIR   = /var/data/uploaded_notes
   ```
5. Redeploy. Your data now persists across all restarts and redeploys.

Alternatively, uncomment the `disk:` block in `render.yaml`:
```yaml
disk:
  name: dpharma-data
  mountPath: /var/data
  sizeGB: 1
```
Then commit and push — Render will apply the change automatically.

---

### Option B — Use a free external database (Turso / PlanetScale / Supabase)

If you want to stay on the free tier but need persistence, swap SQLite for a hosted database:

**Recommended: [Turso](https://turso.tech)** — it's a hosted libSQL (SQLite-compatible) database with a generous free tier.

1. Install the Turso CLI: `curl -sSfL https://get.tur.so/install.sh | bash`
2. Sign up and create a database:
   ```bash
   turso auth login
   turso db create dpharma-vault
   turso db show dpharma-vault   # note the URL
   turso db tokens create dpharma-vault  # note the auth token
   ```
3. Install the async driver: add `libsql-experimental` or use the `sqla-libsql` adapter to `requirements.txt`.
4. Update `DATABASE_URL` in Render environment:
   ```
   DATABASE_URL=libsql+https://dpharma-vault-<org>.turso.io?authToken=<token>
   ```

> For uploaded **files** on the free tier, consider using [Cloudflare R2](https://developers.cloudflare.com/r2/) (free for up to 10 GB) or [Backblaze B2](https://www.backblaze.com/cloud-storage/pricing) and updating the upload logic in `api/routes.py` to stream to S3-compatible object storage.

---

### Option C — Accept data loss (demo/testing only)

For a demo or portfolio piece where data loss is acceptable:

- Keep the current code as-is.
- Understand that after 15 minutes of inactivity, the free Render service spins down and all data is cleared on wake.
- Add a note on your GitHub Pages site informing users of this behaviour.

---

## 8. Final Checklist

Work through this list top-to-bottom before calling it done.

### Code
- [ ] `core/config.py` updated with `ALLOWED_ORIGINS_STR` and `ALLOWED_ORIGINS` property
- [ ] `main.py` updated to use `settings.ALLOWED_ORIGINS` for CORS
- [ ] `docs/index.html` created with `API_BASE` constant set to Render URL
- [ ] `render.yaml` created at repo root
- [ ] `.env.example` created at repo root
- [ ] `.gitignore` includes `*.db`, `uploaded_notes/*`, `.env`
- [ ] `uploaded_notes/.gitkeep` exists so the empty folder is tracked

### GitHub
- [ ] Repository is **public** (required for free GitHub Pages)
- [ ] All files committed and pushed to `main` branch
- [ ] GitHub Pages enabled from **`main` branch / `/docs` folder**
- [ ] GitHub Pages URL is live and loads the HTML page
- [ ] No Python errors, `500` responses, or import failures in the browser console

### Render
- [ ] Web Service created and linked to your GitHub repo
- [ ] Build succeeded (green checkmark in Render logs)
- [ ] Start command runs without errors
- [ ] `GET https://your-service.onrender.com/api/notes` returns `{"total":0,"items":[]}`
- [ ] `ALLOWED_ORIGINS` env var contains your GitHub Pages URL (exact match)
- [ ] `DATABASE_URL` env var set to the Disk path (or external DB)
- [ ] `UPLOAD_DIR` env var set to the Disk path

### Integration
- [ ] `API_BASE` in `docs/index.html` matches your Render service URL exactly
- [ ] Uploaded a test file through the GitHub Pages UI — card appeared
- [ ] Clicked "View / Download" — file opened correctly
- [ ] Deleted a resource — card disappeared
- [ ] Semester filter pills work correctly
- [ ] Keyword search filters cards on keystroke
- [ ] No CORS errors in browser DevTools → Console
- [ ] Footer reads "Built by P.o.Riot" and "Credits P.o.Riot"

---

## 9. Troubleshooting

### CORS error in the browser console
```
Access to fetch at 'https://dpharma-vault.onrender.com/api/notes' from origin
'https://yourname.github.io' has been blocked by CORS policy
```
**Fix:** Your `ALLOWED_ORIGINS` env var on Render does not match the GitHub Pages origin.
- Check: no trailing slash on either URL
- The value must be exactly `https://YOUR-USERNAME.github.io` (or include the repo path if your site is at a subdirectory)
- After updating the env var, trigger a manual redeploy on Render

---

### Render service URL not loading (504 / "Service Unavailable")
The free tier spins down after 15 minutes of inactivity. The first request after a cold start can take **30–60 seconds**. Refresh the page once and it will wake up.

---

### `uploaded_notes` directory does not exist on Render
Render's ephemeral disk doesn't persist the folder. Fix by ensuring `UPLOAD_DIR` is set to `/var/data/uploaded_notes` and a Render Disk is mounted at `/var/data`. The `config.py` `UPLOAD_DIR.mkdir(parents=True, exist_ok=True)` call creates the subdirectory on first boot.

---

### GitHub Pages shows a blank page or 404
- Confirm the `docs/` folder contains `index.html` (exact name, lowercase)
- Confirm Pages is configured for **`/docs`** folder, not **`/ (root)`**
- Wait 2–3 minutes after enabling Pages for the first build to complete
- Hard-refresh the page (`Ctrl+Shift+R` / `Cmd+Shift+R`)

---

### `ModuleNotFoundError` in Render build logs
Render runs `pip install -r requirements.txt` from the repo root. If it can't find the file, check that `requirements.txt` is at the root of your repo (not inside a subdirectory).

---

### SQLite "database is locked" error
This happens if two Uvicorn workers try to write to SQLite simultaneously. On Render free tier (single worker, single process) this shouldn't occur. If you scale to multiple workers, switch to PostgreSQL (available free on Render) instead of SQLite.

---

*Built by **P.o.Riot** · Credits **P.o.Riot***
