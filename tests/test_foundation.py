from pathlib import Path
import sqlite3

import pytest

from mirdexx.config import AppConfig
from mirdexx.database import bootstrap_database
from mirdexx.source_registry import BoundaryDenied, SourceRegistry


def test_config_defaults_are_local_first(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path)
    assert config.api_host == "127.0.0.1"
    assert config.database_path == tmp_path.resolve() / "mirdexx.db"


def test_config_rejects_non_loopback_binding(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        AppConfig(data_dir=tmp_path, api_host="0.0.0.0")


def test_sqlite_bootstrap_creates_governed_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "mirdexx.db"
    bootstrap_database(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert {"schema_meta", "watched_sources", "normalized_events", "control_audit"}.issubset(tables)
    assert version == "4"


def test_source_registry_round_trip_and_pause(tmp_path: Path) -> None:
    database_path = tmp_path / "mirdexx.db"
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    bootstrap_database(database_path)
    registry = SourceRegistry(database_path)

    source = registry.register(approved_root)
    assert source.canonical_root == approved_root.resolve()
    assert registry.list_sources() == [source]

    paused = registry.set_paused(source.source_id, True)
    assert paused.paused is True
    decision = registry.authorize_path(source.source_id, approved_root / "note.md")
    assert decision.allowed is False
    assert decision.reason == "SOURCE_PAUSED"


def test_unapproved_path_is_rejected_before_reader_invocation(tmp_path: Path) -> None:
    database_path = tmp_path / "mirdexx.db"
    approved_root = tmp_path / "approved"
    outside_root = tmp_path / "outside"
    approved_root.mkdir()
    outside_root.mkdir()
    outside_file = outside_root / "secret.txt"
    outside_file.write_text("must never be read", encoding="utf-8")

    bootstrap_database(database_path)
    registry = SourceRegistry(database_path)
    source = registry.register(approved_root)
    calls: list[Path] = []

    def forbidden_reader(path: Path) -> str:
        calls.append(path)
        raise AssertionError("reader must not be invoked for an unapproved path")

    with pytest.raises(BoundaryDenied, match="PATH_OUTSIDE_APPROVED_ROOT"):
        registry.read_text(source.source_id, outside_file, reader=forbidden_reader)

    assert calls == []


def test_approved_path_is_read_after_boundary_allow(tmp_path: Path) -> None:
    database_path = tmp_path / "mirdexx.db"
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    approved_file = approved_root / "architecture.md"
    approved_file.write_text("durable architecture", encoding="utf-8")

    bootstrap_database(database_path)
    registry = SourceRegistry(database_path)
    source = registry.register(approved_root)

    assert registry.read_text(source.source_id, approved_file) == "durable architecture"
