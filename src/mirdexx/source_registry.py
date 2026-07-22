from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4

from .database import connect_database


class BoundaryDenied(PermissionError):
    """Raised before content access when a source or path is not authorized."""


@dataclass(frozen=True, slots=True)
class WatchedSource:
    source_id: str
    source_kind: str
    canonical_root: Path
    enabled: bool
    paused: bool
    custody_mode: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class BoundaryDecision:
    allowed: bool
    canonical_path: Path
    reason: str


class SourceRegistry:
    """SQLite-backed authority for every path Mirdexx may inspect."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def register(
        self,
        root: Path,
        *,
        source_kind: str = "FOLDER",
        custody_mode: str = "METADATA_ONLY",
        policy_version: str = "1",
    ) -> WatchedSource:
        canonical_root = Path(root).expanduser().resolve(strict=False)
        if source_kind not in {"FOLDER", "GIT_REPOSITORY", "MANUAL"}:
            raise ValueError("unsupported source_kind")
        if custody_mode not in {"METADATA_ONLY", "REDACTED_EXCERPT"}:
            raise ValueError("unsupported custody_mode")

        now = datetime.now(timezone.utc).isoformat()
        source_id = str(uuid4())
        try:
            with connect_database(self.database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO watched_sources(
                        source_id, source_kind, canonical_root, enabled, paused,
                        custody_mode, policy_version, created_at, updated_at
                    ) VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        source_kind,
                        str(canonical_root),
                        custody_mode,
                        policy_version,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO control_audit(occurred_at, action, source_id, detail) "
                    "VALUES (?, 'SOURCE_REGISTERED', ?, ?)",
                    (now, source_id, str(canonical_root)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"source already registered: {canonical_root}") from exc

        return self.get(source_id)

    def get(self, source_id: str) -> WatchedSource:
        with connect_database(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM watched_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._from_row(row)

    def list_sources(self) -> list[WatchedSource]:
        with connect_database(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM watched_sources ORDER BY created_at, source_id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def set_paused(self, source_id: str, paused: bool) -> WatchedSource:
        now = datetime.now(timezone.utc).isoformat()
        with connect_database(self.database_path) as connection:
            cursor = connection.execute(
                "UPDATE watched_sources SET paused = ?, updated_at = ? WHERE source_id = ?",
                (int(paused), now, source_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(source_id)
            connection.execute(
                "INSERT INTO control_audit(occurred_at, action, source_id, detail) "
                "VALUES (?, ?, ?, ?)",
                (now, "SOURCE_PAUSED" if paused else "SOURCE_RESUMED", source_id, str(paused)),
            )
        return self.get(source_id)

    def authorize_path(self, source_id: str, candidate: Path) -> BoundaryDecision:
        try:
            source = self.get(source_id)
        except KeyError:
            return BoundaryDecision(False, Path(candidate), "SOURCE_NOT_REGISTERED")

        canonical_path = Path(candidate).expanduser().resolve(strict=False)
        if not source.enabled:
            return BoundaryDecision(False, canonical_path, "SOURCE_DISABLED")
        if source.paused:
            return BoundaryDecision(False, canonical_path, "SOURCE_PAUSED")
        if not canonical_path.is_relative_to(source.canonical_root):
            return BoundaryDecision(False, canonical_path, "PATH_OUTSIDE_APPROVED_ROOT")
        return BoundaryDecision(True, canonical_path, "ALLOWED")

    def read_text(
        self,
        source_id: str,
        candidate: Path,
        *,
        reader: Callable[[Path], str] | None = None,
    ) -> str:
        """Read only after authorization; denied paths never invoke the reader."""

        decision = self.authorize_path(source_id, candidate)
        if not decision.allowed:
            raise BoundaryDenied(decision.reason)
        safe_reader = reader or (lambda path: path.read_text(encoding="utf-8"))
        return safe_reader(decision.canonical_path)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WatchedSource:
        return WatchedSource(
            source_id=row["source_id"],
            source_kind=row["source_kind"],
            canonical_root=Path(row["canonical_root"]),
            enabled=bool(row["enabled"]),
            paused=bool(row["paused"]),
            custody_mode=row["custody_mode"],
            policy_version=row["policy_version"],
        )
