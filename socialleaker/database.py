"""SQLAlchemy engine, session factory and declarative base."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _make_engine():
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        # Ensure the parent directory for the sqlite file exists.
        db_path = settings.resolved_db_path
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        # Allow usage across FastAPI's thread pool.
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args, future=True, echo=False)


engine = _make_engine()


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        """Enable WAL + a busy timeout so the queue worker and the web handlers
        can read/write concurrently without 'database is locked' stalls."""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=8000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def _light_migrate() -> None:
    """Add columns introduced after a database was first created (SQLite)."""
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import text

    wanted = {"tasks": [("frontier_json", "TEXT")]}
    with engine.begin() as conn:
        for table, cols in wanted.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, coltype in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))


def init_db() -> None:
    """Create all tables. Import models so they register on the metadata."""
    from . import models  # noqa: F401  (side-effect import)

    Base.metadata.create_all(bind=engine)
    try:
        _light_migrate()
    except Exception:
        pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
