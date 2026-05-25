"""
core/database.py
----------------
Async SQLAlchemy engine + session factory.
Dynamically handles local SQLite (aiosqlite) or production PostgreSQL (asyncpg).
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.config import settings

# ── Dynamic Engine Selection ───────────────────────────────────────────────────
db_url = settings.DATABASE_URL

# Standardize older cloud/Render postgres schemas to asyncpg dialect if needed
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Apply specific driver connection requirements depending on the target DB type
if "sqlite" in db_url:
    connect_args = {"check_same_thread": False}
else:
    connect_args = {}  # PostgreSQL crashes if passed SQLite arguments

engine = create_async_engine(
    db_url,
    echo=settings.DEBUG,
    connect_args=connect_args,
)

# ── Session factory ────────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── Base declarative class ─────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency: yields a DB session and always closes it ──────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── One-time schema creation helper (called from main.py on startup) ───────────
async def init_db() -> None:
    from models import resource  # noqa: F401 – registers the ORM models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

