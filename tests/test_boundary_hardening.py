from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from mirdexx.database import bootstrap_database, connect_database
from mirdexx.source_registry import BoundaryDenied, SourceRegistry


def _registry(tmp_path: Path) -> tuple[SourceRegistry, Path]:
    database_path = tmp_path / "mirdexx.db"
    bootstrap_database(database_path)
    return SourceRegistry(database_path), database_path


def test_symlink_or_junction_escape_is_rejected_before_reader_invocation(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    approved_root = tmp_path / "approved"
    outside_root = tmp_path / "outside"
    approved_root.mkdir()
    outside_root.mkdir()
    secret = outside_root / "secret.txt"
    secret.write_text("never read", encoding="utf-8")
    escape = approved_root / "escape"

    try:
        escape.symlink_to(outside_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links or junction-like links are not available")

    source = registry.register(approved_root)
    calls: list[Path] = []

    def forbidden_reader(path: Path) -> str:
        calls.append(path)
        raise AssertionError("reader must not be invoked for a path escaping through a link")

    with pytest.raises(BoundaryDenied, match="PATH_ESCAPE_VIA_LINK"):
        registry.read_text(source.source_id, escape / secret.name, reader=forbidden_reader)

    assert calls == []


def test_include_and_exclude_policies_are_enforced_before_read(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    approved_root = tmp_path / "approved"
    docs = approved_root / "docs"
    private = approved_root / "private"
    docs.mkdir(parents=True)
    private.mkdir()
    allowed = docs / "architecture.md"
    excluded = private / "decision.md"
    not_included = approved_root / "script.py"
    allowed.write_text("architecture", encoding="utf-8")
    excluded.write_text("private", encoding="utf-8")
    not_included.write_text("print('no')", encoding="utf-8")

    source = registry.register(
        approved_root,
        include_patterns=("**/*.md",),
        exclude_patterns=("private/**",),
    )

    assert registry.authorize_path(source.source_id, allowed).reason == "ALLOWED"
    assert registry.authorize_path(source.source_id, excluded).reason == "PATH_EXCLUDED"
    assert registry.authorize_path(source.source_id, not_included).reason == "PATH_NOT_INCLUDED"

    calls: list[Path] = []
    with pytest.raises(BoundaryDenied, match="PATH_EXCLUDED"):
        registry.read_text(source.source_id, excluded, reader=lambda path: calls.append(path) or "")
    assert calls == []


def test_path_policy_update_requires_new_version_and_round_trips(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    source = registry.register(approved_root)

    updated = registry.set_path_policy(
        source.source_id,
        include_patterns=("docs/**",),
        exclude_patterns=("docs/private/**",),
        policy_version="2",
    )

    assert updated.include_patterns == ("docs/**",)
    assert updated.exclude_patterns == ("docs/private/**",)
    assert updated.policy_version == "2"


def test_disabled_source_is_denied_before_reader_invocation(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    candidate = approved_root / "note.md"
    candidate.write_text("value", encoding="utf-8")
    source = registry.register(approved_root)
    registry.set_enabled(source.source_id, False)
    calls: list[Path] = []

    with pytest.raises(BoundaryDenied, match="SOURCE_DISABLED"):
        registry.read_text(source.source_id, candidate, reader=lambda path: calls.append(path) or "")

    assert calls == []


def test_control_audit_is_append_only(tmp_path: Path) -> None:
    registry, database_path = _registry(tmp_path)
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    source = registry.register(approved_root)
    registry.set_paused(source.source_id, True)

    with connect_database(database_path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM control_audit").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE control_audit SET detail = 'tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM control_audit")
        after = connection.execute("SELECT COUNT(*) FROM control_audit").fetchone()[0]

    assert before == 2
    assert after == before


def test_version_one_database_is_advanced_non_destructively(tmp_path: Path) -> None:
    database_path = tmp_path / "mirdexx.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1');
            CREATE TABLE watched_sources (
                source_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                canonical_root TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                paused INTEGER NOT NULL DEFAULT 0,
                custody_mode TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE control_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                action TEXT NOT NULL,
                source_id TEXT,
                detail TEXT NOT NULL
            );
            """
        )

    bootstrap_database(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(watched_sources)")}
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert {"include_patterns", "exclude_patterns"}.issubset(columns)
    assert "normalized_events" in tables
    assert version == "3"
