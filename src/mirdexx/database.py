from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = "4"

_WATCHED_SOURCES_V4 = """
CREATE TABLE IF NOT EXISTS watched_sources (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('FOLDER', 'GIT_REPOSITORY', 'MANUAL')),
    canonical_root TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    custody_mode TEXT NOT NULL DEFAULT 'METADATA_ONLY'
        CHECK (custody_mode IN ('METADATA_ONLY', 'REDACTED_EXCERPT', 'CONTROLLED_CONTENT')),
    policy_version TEXT NOT NULL,
    include_patterns TEXT NOT NULL DEFAULT '[]',
    exclude_patterns TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

{_WATCHED_SOURCES_V4}

CREATE UNIQUE INDEX IF NOT EXISTS ux_watched_sources_root
ON watched_sources(canonical_root);

CREATE TABLE IF NOT EXISTS normalized_events (
    event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    source_identity_ref TEXT NOT NULL,
    source_event_ref TEXT NOT NULL,
    source_path_or_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    trust_tier TEXT NOT NULL
        CHECK (trust_tier IN ('trusted', 'controlled', 'untrusted', 'unknown')),
    provenance_refs TEXT NOT NULL DEFAULT '[]',
    custody_mode TEXT NOT NULL
        CHECK (custody_mode IN ('metadata_only', 'redacted_excerpt', 'controlled_content')),
    quarantine_state TEXT NOT NULL
        CHECK (quarantine_state IN ('clear', 'review', 'required', 'rejected')),
    use_restrictions TEXT NOT NULL DEFAULT '[]',
    processing_state TEXT NOT NULL
        CHECK (processing_state IN (
            'received', 'validated', 'quarantined', 'accepted',
            'processed', 'failed', 'superseded'
        )),
    prior_event_ref TEXT,
    policy_version TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES watched_sources(source_id),
    FOREIGN KEY (prior_event_ref) REFERENCES normalized_events(event_id)
);

CREATE INDEX IF NOT EXISTS ix_normalized_events_source
ON normalized_events(source_id, received_at);

CREATE INDEX IF NOT EXISTS ix_normalized_events_hash
ON normalized_events(content_hash);

CREATE TRIGGER IF NOT EXISTS normalized_events_no_update
BEFORE UPDATE ON normalized_events
BEGIN
    SELECT RAISE(ABORT, 'normalized_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS normalized_events_no_delete
BEFORE DELETE ON normalized_events
BEGIN
    SELECT RAISE(ABORT, 'normalized_events is append-only');
END;

CREATE TABLE IF NOT EXISTS control_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    action TEXT NOT NULL,
    source_id TEXT,
    detail TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES watched_sources(source_id)
);

CREATE TRIGGER IF NOT EXISTS control_audit_no_update
BEFORE UPDATE ON control_audit
BEGIN
    SELECT RAISE(ABORT, 'control_audit is append-only');
END;

CREATE TRIGGER IF NOT EXISTS control_audit_no_delete
BEFORE DELETE ON control_audit
BEGIN
    SELECT RAISE(ABORT, 'control_audit is append-only');
END;
"""


def connect_database(database_path: Path) -> sqlite3.Connection:
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_watched_sources_custody(database_path: Path) -> None:
    """Expand custody policy without weakening or deleting existing source records."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='watched_sources'"
        ).fetchone()
        if row is None or "CONTROLLED_CONTENT" in (row["sql"] or ""):
            return
        columns = {r["name"] for r in connection.execute("PRAGMA table_info(watched_sources)").fetchall()}
        if "include_patterns" not in columns:
            connection.execute("ALTER TABLE watched_sources ADD COLUMN include_patterns TEXT NOT NULL DEFAULT '[]'")
        if "exclude_patterns" not in columns:
            connection.execute("ALTER TABLE watched_sources ADD COLUMN exclude_patterns TEXT NOT NULL DEFAULT '[]'")
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE watched_sources_v4 (
                source_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL CHECK (source_kind IN ('FOLDER', 'GIT_REPOSITORY', 'MANUAL')),
                canonical_root TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
                custody_mode TEXT NOT NULL DEFAULT 'METADATA_ONLY'
                    CHECK (custody_mode IN ('METADATA_ONLY', 'REDACTED_EXCERPT', 'CONTROLLED_CONTENT')),
                policy_version TEXT NOT NULL,
                include_patterns TEXT NOT NULL DEFAULT '[]',
                exclude_patterns TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO watched_sources_v4(
                source_id, source_kind, canonical_root, enabled, paused, custody_mode,
                policy_version, include_patterns, exclude_patterns, created_at, updated_at
            )
            SELECT source_id, source_kind, canonical_root, enabled, paused, custody_mode,
                   policy_version, include_patterns, exclude_patterns, created_at, updated_at
            FROM watched_sources;
            DROP TABLE watched_sources;
            ALTER TABLE watched_sources_v4 RENAME TO watched_sources;
            COMMIT;
            """
        )
    finally:
        connection.close()


def bootstrap_database(database_path: Path) -> None:
    """Create or safely advance the local schema without destructive data loss."""
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    if database_path.exists():
        _migrate_watched_sources_custody(database_path)

    with connect_database(database_path) as connection:
        connection.executescript(_SCHEMA)
        _ensure_column(connection, "watched_sources", "include_patterns", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "watched_sources", "exclude_patterns", "TEXT NOT NULL DEFAULT '[]'")
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEMA_VERSION,),
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key integrity check failed after schema migration: {violations}")
