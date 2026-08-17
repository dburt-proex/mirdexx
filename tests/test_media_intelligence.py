from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from mirdexx.database import bootstrap_database, connect_database
from mirdexx.event_ledger import EventLedger
from mirdexx.media_intelligence.models import (
    DecisionRecord,
    EvidenceRecord,
    MediaAnalysis,
    NotionPublishResult,
    TaskRecord,
    TranscriptResult,
    TranscriptSegment,
)
from mirdexx.media_intelligence.notion_sink import NotionVideoIntelligenceSink
from mirdexx.media_intelligence.pipeline import MediaIntelligencePipeline, MediaPolicyDenied
from mirdexx.media_intelligence.transcribe import OpenAITranscriber
from mirdexx.source_registry import SourceRegistry


class FakeTranscriber:
    def transcribe(self, media_path, *, mode="standard", language=None):
        return TranscriptResult(
            text="Decision: ship the governed media pipeline.",
            model="fake-transcriber",
            mode=mode,
            language=language or "en",
            duration_seconds=12.5,
            segments=[TranscriptSegment(start=0, end=4, speaker="A", text="Decision: ship the governed media pipeline.")],
        )


class EmptyTranscriber:
    def transcribe(self, media_path, *, mode="standard", language=None):
        return TranscriptResult(text="   ", model="fake-transcriber", mode=mode, language=language or "en")


class FakeAnalyzer:
    def analyze(self, transcript, *, project_ref=None):
        return MediaAnalysis(
            summary="A governed media pipeline is approved for bounded implementation.",
            decisions=[DecisionRecord(decision="Ship the bounded pipeline", rationale="Explicit commitment", evidence=["00:00-00:04"], confidence=0.95, status="confirmed")],
            tasks=[TaskRecord(title="Run acceptance test", description="Use one controlled media file", priority="High")],
            evidence=[EvidenceRecord(claim="Pipeline was approved", support="Explicit speaker decision", start=0, end=4, speaker="A", confidence=0.95)],
            casa_routing="REVIEW",
            casa_rationale="Promotion into canonical CASA/project records is a material integration gate.",
            project_ref=project_ref,
        )


class FakeSink:
    def __init__(self):
        self.calls = []

    def publish(self, media_path, **kwargs):
        self.calls.append((media_path, kwargs))
        return NotionPublishResult(
            page_id="notion-page",
            page_url="https://notion.example/page",
            promotion_state="REVIEW",
            promoted_task_ids=["task-1"],
            promoted_decision_ids=["decision-1"],
            promoted_evidence_ids=["evidence-1"],
        )


def test_controlled_content_migration_and_registration(tmp_path):
    db = tmp_path / "mirdexx.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE watched_sources (
              source_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL,
              canonical_root TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              paused INTEGER NOT NULL DEFAULT 0,
              custody_mode TEXT NOT NULL CHECK (custody_mode IN ('METADATA_ONLY','REDACTED_EXCERPT')),
              policy_version TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
    bootstrap_database(db)
    root = tmp_path / "media"
    root.mkdir()
    source = SourceRegistry(db).register(root, custody_mode="CONTROLLED_CONTENT")
    assert source.custody_mode == "CONTROLLED_CONTENT"
    with connect_database(db) as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "4"


def test_pipeline_is_fail_closed_without_controlled_content(tmp_path):
    db = tmp_path / "db.sqlite"
    bootstrap_database(db)
    root = tmp_path / "media"
    root.mkdir()
    media = root / "sample.m4a"
    media.write_bytes(b"audio")
    source = SourceRegistry(db).register(root, custody_mode="METADATA_ONLY")
    pipeline = MediaIntelligencePipeline(ledger=EventLedger(db), transcriber=FakeTranscriber(), analyzer=FakeAnalyzer())
    with pytest.raises(MediaPolicyDenied):
        pipeline.ingest(media, source_id=source.source_id)


def test_pipeline_aborts_on_empty_transcription(tmp_path):
    db = tmp_path / "db.sqlite"
    bootstrap_database(db)
    root = tmp_path / "media"
    root.mkdir()
    media = root / "silent.m4a"
    media.write_bytes(b"audio")
    source = SourceRegistry(db).register(root, custody_mode="CONTROLLED_CONTENT")
    pipeline = MediaIntelligencePipeline(ledger=EventLedger(db), transcriber=EmptyTranscriber(), analyzer=FakeAnalyzer())
    with pytest.raises(RuntimeError, match="empty text"):
        pipeline.ingest(media, source_id=source.source_id)


def test_pipeline_preserves_provenance_and_promotion_receipt(tmp_path):
    db = tmp_path / "db.sqlite"
    bootstrap_database(db)
    root = tmp_path / "media"
    root.mkdir()
    media = root / "sample.m4a"
    media.write_bytes(b"audio bytes")
    source = SourceRegistry(db).register(root, custody_mode="CONTROLLED_CONTENT")
    sink = FakeSink()
    ledger = EventLedger(db)
    result = MediaIntelligencePipeline(ledger=ledger, transcriber=FakeTranscriber(), analyzer=FakeAnalyzer(), sink=sink).ingest(
        media,
        source_id=source.source_id,
        mode="diarized",
        project_ref="CASA",
        project_page_id="project-page",
    )
    assert result.notion_page_id == "notion-page"
    assert result.promotion_state == "REVIEW"
    assert result.promoted_task_ids == ["task-1"]
    assert result.promoted_decision_ids == ["decision-1"]
    assert result.promoted_evidence_ids == ["evidence-1"]
    assert result.analysis.casa_routing == "REVIEW"
    original = ledger.get(result.original_event_id)
    transcript = ledger.get(result.transcript_event_id)
    analysis = ledger.get(result.analysis_event_id)
    assert result.transcript_sha256 == transcript.content_hash
    assert original.custody_mode == "controlled_content"
    assert f"event:{original.event_id}" in transcript.provenance_refs
    assert f"event:{transcript.event_id}" in analysis.provenance_refs
    assert sink.calls[0][1]["source_sha256"] == original.content_hash
    assert sink.calls[0][1]["project_page_id"] == "project-page"


def test_transcriber_normalizes_diarized_segments(tmp_path):
    path = tmp_path / "x.m4a"
    path.write_bytes(b"x")
    calls = []

    class Transcriptions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return {"text": "hello", "language": "en", "duration": 3.2, "segments": [{"start": 0, "end": 3.2, "speaker": "A", "text": "hello"}]}

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=Transcriptions()))
    result = OpenAITranscriber(client=client).transcribe(path, mode="diarized")
    assert result.segments[0].speaker == "A"
    assert calls[0]["chunking_strategy"] == "auto"
    assert calls[0]["response_format"] == "diarized_json"


def test_notion_single_part_upload_uses_file_upload_api(tmp_path):
    path = tmp_path / "x.m4a"
    path.write_bytes(b"small audio")
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.headers.get("notion-version")))
        if request.url.path == "/v1/file_uploads":
            return httpx.Response(200, json={"id": "upload-1", "status": "pending"})
        if request.url.path == "/v1/file_uploads/upload-1/send":
            return httpx.Response(200, json={"id": "upload-1", "status": "uploaded"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.notion.com")
    sink = NotionVideoIntelligenceSink(token="test", data_source_id="ds", client=client)
    assert sink.upload_media(path) == "upload-1"
    assert seen[0] == ("POST", "/v1/file_uploads", "2026-03-11")
    assert seen[1][1].endswith("/send")


def test_canonical_promotion_requires_all_target_data_sources():
    with pytest.raises(ValueError, match="canonical promotion requires"):
        NotionVideoIntelligenceSink(token="test", data_source_id="media", promote_candidates=True)


def test_candidate_promotion_creates_related_task_decision_and_evidence_records():
    class RecordingSink(NotionVideoIntelligenceSink):
        def __init__(self):
            super().__init__(
                token="test",
                data_source_id="media-ds",
                projects_data_source_id="projects-ds",
                tasks_data_source_id="tasks-ds",
                decisions_data_source_id="decisions-ds",
                evidence_data_source_id="evidence-ds",
                promote_candidates=True,
            )
            self.created = []

        def _create_record(self, data_source_id, properties):
            record_id = f"record-{len(self.created) + 1}"
            self.created.append((data_source_id, properties))
            return {"id": record_id}

    sink = RecordingSink()
    analysis = FakeAnalyzer().analyze(FakeTranscriber().transcribe(None), project_ref="CASA")
    task_ids, decision_ids, evidence_ids = sink._promote_candidates(
        media_page_id="media-page",
        analysis=analysis,
        transcript_sha256="a" * 64,
        transcript_artifact_id="transcript-event",
        analysis_artifact_id="analysis-event",
        project_page_id="project-page",
        data_policy="Internal",
    )
    assert task_ids == ["record-1"]
    assert decision_ids == ["record-2"]
    assert evidence_ids == ["record-3"]
    assert sink.created[0][0] == "tasks-ds"
    assert sink.created[0][1]["Project"]["relation"][0]["id"] == "project-page"
    assert sink.created[1][1]["Origin Media"]["relation"][0]["id"] == "media-page"
    assert sink.created[2][1]["Integrity SHA256"]["rich_text"][0]["text"]["content"] == "a" * 64
