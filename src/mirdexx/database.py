from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = "1"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watched_sources (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('FOLDER', 'GIT_REPOSITORY', 'MANUAL')),
    canonical_root TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    custody_mode TEXT NOT NULL DEFAULT 'METADATA_ONLY'
        CHECK (custody_mode IN ('METADATA_ONLY', 'REDACTED_EXCERPT')),
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_watched_sources_root
ON watched_sources(canonical_root);

CREATE TABLE IF NOT EXISTS control_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    action TEXT NOT NULL,
    source_id TEXT,
    detail TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES watched_sources(source_id)
);
"""


def connect_database(database_path: Path) -> sqlite3.Connection:
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def bootstrap_database(database_path: Path) -> None:
    """Create the first local schema without destructive migration behavior."""

    with connect_database(database_path) as connection:
        connection.executescript(_SCHEMA)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEMA_VERSION,),
        )
