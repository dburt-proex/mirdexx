from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Callable, Iterable
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
    include_patterns: tuple[str, ...]
    exclude_patterns: tuple[str, ...]


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
        include_patterns: Iterable[str] = (),
        exclude_patterns: Iterable[str] = (),
    ) -> WatchedSource:
        canonical_root = Path(root).expanduser().resolve(strict=False)
        if source_kind not in {"FOLDER", "GIT_REPOSITORY", "MANUAL"}:
            raise ValueError("unsupported source_kind")
        if custody_mode not in {"METADATA_ONLY", "REDACTED_EXCERPT", "CONTROLLED_CONTENT"}:
            raise ValueError("unsupported custody_mode")

        includes = self._normalize_patterns(include_patterns)
        excludes = self._normalize_patterns(exclude_patterns)
        now = datetime.now(timezone.utc).isoformat()
        source_id = str(uuid4())
        try:
            with connect_database(self.database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO watched_sources(
                        source_id, source_kind, canonical_root, enabled, paused,
                        custody_mode, policy_version, include_patterns, exclude_patterns,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        source_kind,
                        str(canonical_root),
                        custody_mode,
                        policy_version,
                        json.dumps(includes),
                        json.dumps(excludes),
                        now,
                        now,
                    ),
                )
                self._append_audit(
                    connection,
                    now,
                    "SOURCE_REGISTERED",
                    source_id,
                    json.dumps(
                        {
                            "canonical_root": str(canonical_root),
                            "include_patterns": includes,
                            "exclude_patterns": excludes,
                            "policy_version": policy_version,
                        },
                        sort_keys=True,
                    ),
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
        return self._set_flag(
            source_id,
            column="paused",
            value=paused,
            action="SOURCE_PAUSED" if paused else "SOURCE_RESUMED",
        )

    def set_enabled(self, source_id: str, enabled: bool) -> WatchedSource:
        return self._set_flag(
            source_id,
            column="enabled",
            value=enabled,
            action="SOURCE_ENABLED" if enabled else "SOURCE_DISABLED",
        )

    def set_path_policy(
        self,
        source_id: str,
        *,
        include_patterns: Iterable[str],
        exclude_patterns: Iterable[str],
        policy_version: str,
    ) -> WatchedSource:
        if not policy_version:
            raise ValueError("policy_version is required")
        includes = self._normalize_patterns(include_patterns)
        excludes = self._normalize_patterns(exclude_patterns)
        now = datetime.now(timezone.utc).isoformat()
        with connect_database(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE watched_sources
                SET include_patterns = ?, exclude_patterns = ?, policy_version = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (json.dumps(includes), json.dumps(excludes), policy_version, now, source_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(source_id)
            self._append_audit(
                connection,
                now,
                "SOURCE_POLICY_UPDATED",
                source_id,
                json.dumps(
                    {
                        "include_patterns": includes,
                        "exclude_patterns": excludes,
                        "policy_version": policy_version,
                    },
                    sort_keys=True,
                ),
            )
        return self.get(source_id)

    def authorize_path(self, source_id: str, candidate: Path) -> BoundaryDecision:
        try:
            source = self.get(source_id)
        except KeyError:
            return BoundaryDecision(False, Path(candidate), "SOURCE_NOT_REGISTERED")

        candidate_path = Path(candidate).expanduser()
        lexical_path = Path(os.path.abspath(os.fspath(candidate_path)))
        canonical_path = candidate_path.resolve(strict=False)

        if not source.enabled:
            return BoundaryDecision(False, canonical_path, "SOURCE_DISABLED")
        if source.paused:
            return BoundaryDecision(False, canonical_path, "SOURCE_PAUSED")
        if not canonical_path.is_relative_to(source.canonical_root):
            reason = (
                "PATH_ESCAPE_VIA_LINK"
                if lexical_path.is_relative_to(source.canonical_root)
                else "PATH_OUTSIDE_APPROVED_ROOT"
            )
            return BoundaryDecision(False, canonical_path, reason)

        relative_path = canonical_path.relative_to(source.canonical_root).as_posix()
        if self._matches_any(relative_path, source.exclude_patterns):
            return BoundaryDecision(False, canonical_path, "PATH_EXCLUDED")
        if source.include_patterns and not self._matches_any(relative_path, source.include_patterns):
            return BoundaryDecision(False, canonical_path, "PATH_NOT_INCLUDED")
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

    def _set_flag(self, source_id: str, *, column: str, value: bool, action: str) -> WatchedSource:
        if column not in {"enabled", "paused"}:
            raise ValueError("unsupported source flag")
        now = datetime.now(timezone.utc).isoformat()
        with connect_database(self.database_path) as connection:
            cursor = connection.execute(
                f"UPDATE watched_sources SET {column} = ?, updated_at = ? WHERE source_id = ?",
                (int(value), now, source_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(source_id)
            self._append_audit(connection, now, action, source_id, str(value))
        return self.get(source_id)

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        occurred_at: str,
        action: str,
        source_id: str,
        detail: str,
    ) -> None:
        connection.execute(
            "INSERT INTO control_audit(occurred_at, action, source_id, detail) VALUES (?, ?, ?, ?)",
            (occurred_at, action, source_id, detail),
        )

    @staticmethod
    def _normalize_patterns(patterns: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for pattern in patterns:
            value = str(pattern).strip().replace("\\", "/")
            if not value:
                raise ValueError("path patterns cannot be empty")
            pure = PurePosixPath(value)
            if value.startswith("/") or ":" in pure.parts[0] or ".." in pure.parts:
                raise ValueError("path patterns must be relative and cannot traverse parents")
            normalized.append(value)
        return tuple(dict.fromkeys(normalized))

    @classmethod
    def _matches_any(cls, relative_path: str, patterns: tuple[str, ...]) -> bool:
        return any(cls._matches_pattern(relative_path, pattern) for pattern in patterns)

    @staticmethod
    def _matches_pattern(relative_path: str, pattern: str) -> bool:
        candidates = {pattern}
        if pattern.startswith("**/"):
            candidates.add(pattern[3:])
        return any(fnmatchcase(relative_path, candidate) for candidate in candidates)

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
            include_patterns=tuple(json.loads(row["include_patterns"])),
            exclude_patterns=tuple(json.loads(row["exclude_patterns"])),
        )
