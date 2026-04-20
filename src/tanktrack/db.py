"""SQLite connection helper.

Single database file. WAL mode enabled so one writer (AIS stream) and
multiple readers (CLI, FastAPI) can share the file without blocking each
other. Connections are cheap — we open/close per operation rather than
pool, which keeps threading trivial.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tanktrack.config import DB_PATH, ROOT

log = logging.getLogger(__name__)

SCHEMA_PATH = ROOT / "schema.sql"


def _configure(c: sqlite3.Connection) -> None:
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA foreign_keys=ON;")
    c.execute("PRAGMA synchronous=NORMAL;")
    c.row_factory = sqlite3.Row


def connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
        isolation_level=None,         # autocommit mode
        check_same_thread=False,      # safe under WAL for single-writer pattern
    )
    _configure(c)
    return c


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    c = connect()
    try:
        yield c
    finally:
        c.close()


def init_schema() -> None:
    """Create the schema if it doesn't exist yet. Safe to call repeatedly."""
    schema = SCHEMA_PATH.read_text()
    with conn() as c:
        c.executescript(schema)
    log.info("schema initialized at %s", DB_PATH)


def healthcheck() -> bool:
    try:
        with conn() as c:
            row = c.execute("SELECT 1").fetchone()
            return bool(row and row[0] == 1)
    except Exception as e:
        log.error("DB healthcheck failed: %s", e)
        return False


def utcnow_iso() -> str:
    """Canonical ISO-8601 UTC string matching schema.sql's default."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
