from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mirdexx.event_ledger import EventLedger

from .models import MediaAnalysis, NotionPublishResult, PipelineResult, TranscriptResult, TranscriptionMode


class MediaPolicyDenied(PermissionError):
    """Raised when media is not authorized for full-content external processing."""


class Transcriber(Protocol):
    def transcribe(self, media_path: Path, *, mode: TranscriptionMode = "standard", language: str | None = None) -> TranscriptResult: ...


class Analyzer(Protocol):
    def analyze(self, transcript: TranscriptResult, *, project_ref: str | None = None) -> MediaAnalysis: ...


class Sink(Protocol):
    def publish(
        self,
        media_path: Path,
        *,
        transcript: TranscriptResult,
        analysis: MediaAnalysis,
        source_sha256: str,
        transcript_sha256: str,
        mirdexx_artifact_id: str,
        transcript_artifact_id: str,
        analysis_artifact_id: str,
        project_ref: str | None,
        project_page_id: str | None,
        data_policy: str,
        source_url: str | None = None,
    ) -> NotionPublishResult: ...


class MediaIntelligencePipeline:
    """Governed media -> transcript -> structured intelligence -> Notion pipeline."""

    def __init__(self, *, ledger: EventLedger, transcriber: Transcriber, analyzer: Analyzer, sink: Sink | None = None) -> None:
        self.ledger = ledger
        self.transcriber = transcriber
        self.analyzer = analyzer
        self.sink = sink

    def ingest(
        self,
        media_path: Path,
        *,
        source_id: str,
        mode: TranscriptionMode = "standard",
        language: str | None = None,
        project_ref: str | None = None,
        project_page_id: str | None = None,
        data_policy: str = "Internal",
        trust_tier: str = "controlled",
        source_url: str | None = None,
    ) -> PipelineResult:
        media_path = Path(media_path).expanduser().resolve(strict=True)
        source = self.ledger.registry.get(source_id)
        if source.custody_mode != "CONTROLLED_CONTENT":
            raise MediaPolicyDenied(
                "full media transcription requires a source registered with CONTROLLED_CONTENT custody"
            )
        if data_policy not in {"Private", "Internal", "Public"}:
            raise ValueError("data_policy must be Private, Internal, or Public")

        original = self.ledger.record_file_event(
            source_id,
            media_path,
            source_identity_ref="operator-supplied-media",
            source_event_ref=f"media:{media_path.name}:{media_path.stat().st_mtime_ns}",
            trust_tier=trust_tier,
            provenance_refs=(f"operator-supplied:{media_path.name}",),
        )

        transcript = self.transcriber.transcribe(media_path, mode=mode, language=language)
        if not transcript.text.strip():
            raise RuntimeError("transcription returned empty text; analysis and publication aborted")

        transcript_payload = transcript.model_dump_json()
        transcript_event = self.ledger.record_derived_content(
            original.event_id,
            transcript_payload,
            source_event_ref=f"transcript:{transcript.model}:{mode}",
            source_path_or_uri=f"derived://media/{original.event_id}/transcript",
            additional_provenance_refs=(f"model:{transcript.model}",),
        )

        # Explicit context gate: a transcript cannot silently enter model context if
        # provenance, quarantine, trust, or restrictions deny it.
        self.ledger.require_context_eligible(transcript_event.event_id)
        analysis = self.analyzer.analyze(transcript, project_ref=project_ref)
        analysis_payload = analysis.model_dump_json()
        analysis_event = self.ledger.record_derived_content(
            transcript_event.event_id,
            analysis_payload,
            source_event_ref="media-intelligence-analysis:v0.1",
            source_path_or_uri=f"derived://media/{original.event_id}/analysis",
            additional_provenance_refs=(f"event:{transcript_event.event_id}",),
        )

        publish_result: NotionPublishResult | None = None
        if self.sink is not None:
            publish_result = self.sink.publish(
                media_path,
                transcript=transcript,
                analysis=analysis,
                source_sha256=original.content_hash,
                transcript_sha256=transcript_event.content_hash,
                mirdexx_artifact_id=original.event_id,
                transcript_artifact_id=transcript_event.event_id,
                analysis_artifact_id=analysis_event.event_id,
                project_ref=project_ref,
                project_page_id=project_page_id,
                data_policy=data_policy,
                source_url=source_url,
            )

        return PipelineResult(
            original_event_id=original.event_id,
            transcript_event_id=transcript_event.event_id,
            analysis_event_id=analysis_event.event_id,
            source_sha256=original.content_hash,
            transcript_sha256=transcript_event.content_hash,
            notion_page_id=publish_result.page_id if publish_result else None,
            notion_page_url=publish_result.page_url if publish_result else None,
            promotion_state=publish_result.promotion_state if publish_result else None,
            promoted_task_ids=publish_result.promoted_task_ids if publish_result else [],
            promoted_decision_ids=publish_result.promoted_decision_ids if publish_result else [],
            promoted_evidence_ids=publish_result.promoted_evidence_ids if publish_result else [],
            analysis=analysis,
            transcript=transcript,
        )
