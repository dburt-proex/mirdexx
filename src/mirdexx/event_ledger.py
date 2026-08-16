from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterable
from uuid import uuid4

from .database import connect_database
from .source_registry import BoundaryDenied, SourceRegistry


TRUST_TIERS = frozenset({"trusted", "controlled", "untrusted", "unknown"})
CUSTODY_MODES = frozenset({"metadata_only", "redacted_excerpt", "controlled_content"})
QUARANTINE_STATES = frozenset({"clear", "review", "required", "rejected"})
PROCESSING_STATES = frozenset(
    {"received", "validated", "quarantined", "accepted", "processed", "failed", "superseded"}
)
_CONTEXT_DENY_RESTRICTIONS = frozenset({"no_context", "no_agent_context", "metadata_only_context"})
_QUARANTINE_RANK = {"clear": 0, "review": 1, "required": 2, "rejected": 3}
_SOURCE_CUSTODY = {
    "METADATA_ONLY": "metadata_only",
    "REDACTED_EXCERPT": "redacted_excerpt",
}


class EventIntegrityError(ValueError):
    """Raised when supplied content does not match an event's bound hash."""


class ContextUseDenied(PermissionError):
    """Raised when an event is not eligible to enter downstream agent context."""


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    event_id: str
    idempotency_key: str
    source_id: str
    source_identity_ref: str
    source_event_ref: str
    source_path_or_uri: str
    content_hash: str
    observed_at: str
    received_at: str
    trust_tier: str
    provenance_refs: tuple[str, ...]
    custody_mode: str
    quarantine_state: str
    use_restrictions: tuple[str, ...]
    processing_state: str
    prior_event_ref: str | None
    policy_version: str


class EventLedger:
    """Append-only normalized event ledger with fail-closed context eligibility."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.registry = SourceRegistry(self.database_path)

    @staticmethod
    def content_hash(content: bytes | str) -> str:
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return sha256(payload).hexdigest()

    def record_file_event(
        self,
        source_id: str,
        candidate: Path,
        *,
        source_identity_ref: str,
        source_event_ref: str,
        observed_at: str | None = None,
        trust_tier: str = "unknown",
        provenance_refs: Iterable[str] = (),
        use_restrictions: Iterable[str] = (),
        quarantine_state: str | None = None,
        expected_hash: str | None = None,
        prior_event_ref: str | None = None,
        reader: Callable[[Path], bytes] | None = None,
    ) -> NormalizedEvent:
        """Authorize a path before reading it, then bind content identity before persistence."""

        source = self.registry.get(source_id)
        decision = self.registry.authorize_path(source_id, candidate)
        if not decision.allowed:
            raise BoundaryDenied(decision.reason)

        safe_reader = reader or (lambda path: path.read_bytes())
        payload = safe_reader(decision.canonical_path)
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("event reader must return bytes")
        payload = bytes(payload)
        digest = self.content_hash(payload)
        self._verify_expected_hash(digest, expected_hash)

        if prior_event_ref is not None:
            prior = self.get(prior_event_ref)
            if prior.source_id != source_id:
                raise ValueError("prior_event_ref must belong to the same source")

        refs = self._normalize_values(provenance_refs, "provenance_refs")
        restrictions = self._normalize_values(use_restrictions, "use_restrictions")
        quarantine = self._resolve_quarantine(trust_tier, refs, quarantine_state)
        processing = "accepted" if quarantine == "clear" else "quarantined"
        custody_mode = _SOURCE_CUSTODY.get(source.custody_mode)
        if custody_mode is None:
            raise ValueError(f"unsupported source custody mode: {source.custody_mode}")

        return self._record(
            source_id=source_id,
            source_identity_ref=source_identity_ref,
            source_event_ref=source_event_ref,
            source_path_or_uri=str(decision.canonical_path),
            content_hash=digest,
            observed_at=observed_at or self._now(),
            trust_tier=trust_tier,
            provenance_refs=refs,
            custody_mode=custody_mode,
            quarantine_state=quarantine,
            use_restrictions=restrictions,
            processing_state=processing,
            prior_event_ref=prior_event_ref,
            policy_version=source.policy_version,
        )

    def record_derived_content(
        self,
        parent_event_id: str,
        content: bytes | str,
        *,
        source_event_ref: str,
        source_path_or_uri: str,
        observed_at: str | None = None,
        expected_hash: str | None = None,
        additional_provenance_refs: Iterable[str] = (),
        additional_use_restrictions: Iterable[str] = (),
    ) -> NormalizedEvent:
        """Record a derived artifact without allowing derivation to improve trust or custody."""

        parent = self.get(parent_event_id)
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        digest = self.content_hash(payload)
        self._verify_expected_hash(digest, expected_hash)

        provenance = self._normalize_values(
            (*parent.provenance_refs, f"event:{parent.event_id}", *additional_provenance_refs),
            "provenance_refs",
        )
        restrictions = self._normalize_values(
            (*parent.use_restrictions, *additional_use_restrictions),
            "use_restrictions",
        )
        processing = "accepted" if parent.quarantine_state == "clear" else "quarantined"

        return self._record(
            source_id=parent.source_id,
            source_identity_ref=parent.source_identity_ref,
            source_event_ref=source_event_ref,
            source_path_or_uri=source_path_or_uri,
            content_hash=digest,
            observed_at=observed_at or self._now(),
            trust_tier=parent.trust_tier,
            provenance_refs=provenance,
            custody_mode=parent.custody_mode,
            quarantine_state=parent.quarantine_state,
            use_restrictions=restrictions,
            processing_state=processing,
            prior_event_ref=None,
            policy_version=parent.policy_version,
        )

    def get(self, event_id: str) -> NormalizedEvent:
        with connect_database(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM normalized_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self._from_row(row)

    def list_for_source(self, source_id: str) -> list[NormalizedEvent]:
        with connect_database(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM normalized_events WHERE source_id = ? ORDER BY received_at, event_id",
                (source_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def context_eligible(self, event_id: str) -> bool:
        event = self.get(event_id)
        if event.trust_tier not in {"trusted", "controlled"}:
            return False
        if event.quarantine_state != "clear":
            return False
        if event.processing_state not in {"accepted", "processed"}:
            return False
        return not _CONTEXT_DENY_RESTRICTIONS.intersection(event.use_restrictions)

    def require_context_eligible(self, event_id: str) -> NormalizedEvent:
        event = self.get(event_id)
        if not self.context_eligible(event_id):
            raise ContextUseDenied(
                f"event {event_id} is not eligible for downstream agent context"
            )
        return event

    def verify_content(self, event_id: str, content: bytes | str) -> NormalizedEvent:
        event = self.get(event_id)
        actual = self.content_hash(content)
        if actual != event.content_hash:
            raise EventIntegrityError(
                f"content hash mismatch for {event_id}: expected {event.content_hash}, got {actual}"
            )
        return event

    def _record(
        self,
        *,
        source_id: str,
        source_identity_ref: str,
        source_event_ref: str,
        source_path_or_uri: str,
        content_hash: str,
        observed_at: str,
        trust_tier: str,
        provenance_refs: tuple[str, ...],
        custody_mode: str,
        quarantine_state: str,
        use_restrictions: tuple[str, ...],
        processing_state: str,
        prior_event_ref: str | None,
        policy_version: str,
    ) -> NormalizedEvent:
        self._validate_record_values(
            source_identity_ref=source_identity_ref,
            source_event_ref=source_event_ref,
            source_path_or_uri=source_path_or_uri,
            trust_tier=trust_tier,
            custody_mode=custody_mode,
            quarantine_state=quarantine_state,
            processing_state=processing_state,
            policy_version=policy_version,
        )
        idempotency_key = self._idempotency_key(
            source_id=source_id,
            source_identity_ref=source_identity_ref,
            source_event_ref=source_event_ref,
            source_path_or_uri=source_path_or_uri,
            content_hash=content_hash,
        )
        event_id = str(uuid4())
        received_at = self._now()

        with connect_database(self.database_path) as connection:
            existing = connection.execute(
                "SELECT * FROM normalized_events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return self._from_row(existing)

            try:
                connection.execute(
                    """
                    INSERT INTO normalized_events(
                        event_id, idempotency_key, source_id, source_identity_ref,
                        source_event_ref, source_path_or_uri, content_hash, observed_at,
                        received_at, trust_tier, provenance_refs, custody_mode,
                        quarantine_state, use_restrictions, processing_state,
                        prior_event_ref, policy_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        idempotency_key,
                        source_id,
                        source_identity_ref,
                        source_event_ref,
                        source_path_or_uri,
                        content_hash,
                        observed_at,
                        received_at,
                        trust_tier,
                        json.dumps(provenance_refs),
                        custody_mode,
                        quarantine_state,
                        json.dumps(use_restrictions),
                        processing_state,
                        prior_event_ref,
                        policy_version,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM normalized_events WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return self._from_row(existing)
                raise

        return self.get(event_id)

    @classmethod
    def _resolve_quarantine(
        cls,
        trust_tier: str,
        provenance_refs: tuple[str, ...],
        requested: str | None,
    ) -> str:
        if trust_tier not in TRUST_TIERS:
            raise ValueError(f"unsupported trust_tier: {trust_tier}")
        minimum = "review" if trust_tier in {"untrusted", "unknown"} or not provenance_refs else "clear"
        if requested is None:
            return minimum
        if requested not in QUARANTINE_STATES:
            raise ValueError(f"unsupported quarantine_state: {requested}")
        if _QUARANTINE_RANK[requested] < _QUARANTINE_RANK[minimum]:
            raise ValueError("quarantine_state cannot weaken provenance/trust requirements")
        return requested

    @staticmethod
    def _verify_expected_hash(actual: str, expected: str | None) -> None:
        if expected is None:
            return
        if expected != actual:
            raise EventIntegrityError(f"content hash mismatch: expected {expected}, got {actual}")

    @staticmethod
    def _normalize_values(values: Iterable[str], field: str) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            item = str(value).strip()
            if not item:
                raise ValueError(f"{field} cannot contain empty values")
            normalized.append(item)
        return tuple(dict.fromkeys(normalized))

    @staticmethod
    def _idempotency_key(
        *,
        source_id: str,
        source_identity_ref: str,
        source_event_ref: str,
        source_path_or_uri: str,
        content_hash: str,
    ) -> str:
        canonical = json.dumps(
            {
                "content_hash": content_hash,
                "source_event_ref": source_event_ref,
                "source_id": source_id,
                "source_identity_ref": source_identity_ref,
                "source_path_or_uri": source_path_or_uri,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(canonical).hexdigest()

    @staticmethod
    def _validate_record_values(
        *,
        source_identity_ref: str,
        source_event_ref: str,
        source_path_or_uri: str,
        trust_tier: str,
        custody_mode: str,
        quarantine_state: str,
        processing_state: str,
        policy_version: str,
    ) -> None:
        for field, value in {
            "source_identity_ref": source_identity_ref,
            "source_event_ref": source_event_ref,
            "source_path_or_uri": source_path_or_uri,
            "policy_version": policy_version,
        }.items():
            if not str(value).strip():
                raise ValueError(f"{field} is required")
        if trust_tier not in TRUST_TIERS:
            raise ValueError(f"unsupported trust_tier: {trust_tier}")
        if custody_mode not in CUSTODY_MODES:
            raise ValueError(f"unsupported custody_mode: {custody_mode}")
        if quarantine_state not in QUARANTINE_STATES:
            raise ValueError(f"unsupported quarantine_state: {quarantine_state}")
        if processing_state not in PROCESSING_STATES:
            raise ValueError(f"unsupported processing_state: {processing_state}")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> NormalizedEvent:
        return NormalizedEvent(
            event_id=row["event_id"],
            idempotency_key=row["idempotency_key"],
            source_id=row["source_id"],
            source_identity_ref=row["source_identity_ref"],
            source_event_ref=row["source_event_ref"],
            source_path_or_uri=row["source_path_or_uri"],
            content_hash=row["content_hash"],
            observed_at=row["observed_at"],
            received_at=row["received_at"],
            trust_tier=row["trust_tier"],
            provenance_refs=tuple(json.loads(row["provenance_refs"])),
            custody_mode=row["custody_mode"],
            quarantine_state=row["quarantine_state"],
            use_restrictions=tuple(json.loads(row["use_restrictions"])),
            processing_state=row["processing_state"],
            prior_event_ref=row["prior_event_ref"],
            policy_version=row["policy_version"],
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
