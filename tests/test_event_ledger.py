from pathlib import Path
import sqlite3

import pytest

from mirdexx.database import bootstrap_database, connect_database
from mirdexx.event_ledger import ContextUseDenied, EventIntegrityError, EventLedger
from mirdexx.source_registry import BoundaryDenied, SourceRegistry


def _fixture(tmp_path: Path):
    database_path = tmp_path / "mirdexx.db"
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    bootstrap_database(database_path)
    registry = SourceRegistry(database_path)
    source = registry.register(approved_root, policy_version="policy-1")
    ledger = EventLedger(database_path)
    return database_path, approved_root, registry, source, ledger


def _record_controlled(ledger: EventLedger, source_id: str, path: Path, ref: str = "evt-1"):
    return ledger.record_file_event(
        source_id,
        path,
        source_identity_ref="fixture-source",
        source_event_ref=ref,
        trust_tier="controlled",
        provenance_refs=("fixture:test",),
    )


def test_duplicate_replay_returns_same_event(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "note.md"
    path.write_text("stable content", encoding="utf-8")

    first = _record_controlled(ledger, source.source_id, path)
    replay = _record_controlled(ledger, source.source_id, path)

    assert replay.event_id == first.event_id
    assert replay.idempotency_key == first.idempotency_key
    assert len(ledger.list_for_source(source.source_id)) == 1


def test_restart_safe_duplicate_prevention(tmp_path: Path) -> None:
    database_path, root, _, source, ledger = _fixture(tmp_path)
    path = root / "restart.md"
    path.write_text("survives restart", encoding="utf-8")
    first = _record_controlled(ledger, source.source_id, path)

    restarted = EventLedger(database_path)
    replay = _record_controlled(restarted, source.source_id, path)

    assert replay.event_id == first.event_id
    assert len(restarted.list_for_source(source.source_id)) == 1


def test_changed_content_creates_new_hash_event(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "changing.md"
    path.write_text("version one", encoding="utf-8")
    first = _record_controlled(ledger, source.source_id, path, "source-event")

    path.write_text("version two", encoding="utf-8")
    second = _record_controlled(ledger, source.source_id, path, "source-event")

    assert second.event_id != first.event_id
    assert second.content_hash != first.content_hash
    assert len(ledger.list_for_source(source.source_id)) == 2


def test_disabled_source_denies_before_reader_invocation(tmp_path: Path) -> None:
    _, root, registry, source, ledger = _fixture(tmp_path)
    path = root / "secret.md"
    path.write_text("do not read", encoding="utf-8")
    registry.set_enabled(source.source_id, False)
    calls: list[Path] = []

    def forbidden_reader(candidate: Path) -> bytes:
        calls.append(candidate)
        raise AssertionError("reader must not run for a disabled source")

    with pytest.raises(BoundaryDenied, match="SOURCE_DISABLED"):
        ledger.record_file_event(
            source.source_id,
            path,
            source_identity_ref="fixture-source",
            source_event_ref="disabled",
            reader=forbidden_reader,
        )

    assert calls == []
    assert ledger.list_for_source(source.source_id) == []


def test_unknown_provenance_is_quarantined_and_not_context_eligible(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "unknown.md"
    path.write_text("unknown origin", encoding="utf-8")

    event = ledger.record_file_event(
        source.source_id,
        path,
        source_identity_ref="unknown-source",
        source_event_ref="unknown-1",
        trust_tier="unknown",
    )

    assert event.quarantine_state == "review"
    assert event.processing_state == "quarantined"
    assert ledger.context_eligible(event.event_id) is False
    with pytest.raises(ContextUseDenied):
        ledger.require_context_eligible(event.event_id)


def test_rejected_event_cannot_enter_downstream_context(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "rejected.md"
    path.write_text("known but rejected", encoding="utf-8")

    event = ledger.record_file_event(
        source.source_id,
        path,
        source_identity_ref="fixture-source",
        source_event_ref="rejected-1",
        trust_tier="controlled",
        provenance_refs=("fixture:test",),
        quarantine_state="rejected",
    )

    assert event.quarantine_state == "rejected"
    assert ledger.context_eligible(event.event_id) is False
    with pytest.raises(ContextUseDenied):
        ledger.require_context_eligible(event.event_id)


def test_replay_cannot_upgrade_existing_trust(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "replay.md"
    path.write_text("same bytes", encoding="utf-8")

    first = ledger.record_file_event(
        source.source_id,
        path,
        source_identity_ref="fixture-source",
        source_event_ref="same-event",
        trust_tier="unknown",
    )
    attempted_upgrade = ledger.record_file_event(
        source.source_id,
        path,
        source_identity_ref="fixture-source",
        source_event_ref="same-event",
        trust_tier="trusted",
        provenance_refs=("later:claim",),
    )

    assert attempted_upgrade.event_id == first.event_id
    assert attempted_upgrade.trust_tier == "unknown"
    assert attempted_upgrade.quarantine_state == "review"


def test_supersession_creates_new_event_without_mutating_prior(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "correction.md"
    path.write_text("incorrect", encoding="utf-8")
    first = _record_controlled(ledger, source.source_id, path, "correction-1")

    path.write_text("corrected", encoding="utf-8")
    correction = ledger.record_file_event(
        source.source_id,
        path,
        source_identity_ref="fixture-source",
        source_event_ref="correction-2",
        trust_tier="controlled",
        provenance_refs=("fixture:test", "correction:operator"),
        prior_event_ref=first.event_id,
    )

    preserved = ledger.get(first.event_id)
    assert correction.event_id != first.event_id
    assert correction.prior_event_ref == first.event_id
    assert preserved.content_hash == first.content_hash
    assert preserved.prior_event_ref is None
    assert len(ledger.list_for_source(source.source_id)) == 2


def test_expected_hash_mismatch_fails_closed_without_persistence(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "hash.md"
    path.write_text("integrity", encoding="utf-8")

    with pytest.raises(EventIntegrityError, match="content hash mismatch"):
        ledger.record_file_event(
            source.source_id,
            path,
            source_identity_ref="fixture-source",
            source_event_ref="hash-1",
            trust_tier="controlled",
            provenance_refs=("fixture:test",),
            expected_hash="0" * 64,
        )

    assert ledger.list_for_source(source.source_id) == []


def test_verify_content_fails_closed_after_record(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "verify.md"
    path.write_text("original", encoding="utf-8")
    event = _record_controlled(ledger, source.source_id, path)

    assert ledger.verify_content(event.event_id, b"original") == event
    with pytest.raises(EventIntegrityError, match="content hash mismatch"):
        ledger.verify_content(event.event_id, b"tampered")


def test_derived_artifact_preserves_provenance_trust_and_restrictions(tmp_path: Path) -> None:
    _, root, _, source, ledger = _fixture(tmp_path)
    path = root / "parent.md"
    path.write_text("parent", encoding="utf-8")
    parent = ledger.record_file_event(
        source.source_id,
        path,
        source_identity_ref="fixture-source",
        source_event_ref="parent-1",
        trust_tier="controlled",
        provenance_refs=("fixture:test",),
        use_restrictions=("redact_external",),
    )

    derived = ledger.record_derived_content(
        parent.event_id,
        b"derived",
        source_event_ref="derived-1",
        source_path_or_uri="derived://summary/1",
        additional_provenance_refs=("transform:test",),
    )

    assert derived.source_identity_ref == parent.source_identity_ref
    assert derived.trust_tier == parent.trust_tier
    assert derived.quarantine_state == parent.quarantine_state
    assert derived.custody_mode == parent.custody_mode
    assert derived.use_restrictions == parent.use_restrictions
    assert f"event:{parent.event_id}" in derived.provenance_refs
    assert "fixture:test" in derived.provenance_refs
    assert "transform:test" in derived.provenance_refs


def test_normalized_event_history_is_append_only(tmp_path: Path) -> None:
    database_path, root, _, source, ledger = _fixture(tmp_path)
    path = root / "immutable.md"
    path.write_text("immutable", encoding="utf-8")
    event = _record_controlled(ledger, source.source_id, path)

    with connect_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE normalized_events SET trust_tier = 'trusted' WHERE event_id = ?",
                (event.event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM normalized_events WHERE event_id = ?", (event.event_id,))
