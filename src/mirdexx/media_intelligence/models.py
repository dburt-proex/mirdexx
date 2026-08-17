from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


TranscriptionMode = Literal["standard", "timestamped", "diarized"]
CasaRouting = Literal["ALLOW", "REVIEW", "HALT"]
PromotionState = Literal["Not Promoted", "Candidate", "REVIEW", "Promoted", "Rejected"]


class TranscriptSegment(BaseModel):
    start: float | None = None
    end: float | None = None
    speaker: str | None = None
    text: str


class TranscriptResult(BaseModel):
    text: str
    model: str
    mode: TranscriptionMode
    language: str | None = None
    duration_seconds: float | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    decision: str
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    status: Literal["proposed", "confirmed", "rejected", "unknown"] = "unknown"
    confidence: float = Field(ge=0, le=1)


class TaskRecord(BaseModel):
    title: str
    description: str = ""
    priority: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    owner: str | None = None
    due_date: date | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    claim: str
    support: str
    start: float | None = None
    end: float | None = None
    speaker: str | None = None
    confidence: float = Field(ge=0, le=1)


class MediaAnalysis(BaseModel):
    summary: str
    decisions: list[DecisionRecord] = Field(default_factory=list)
    tasks: list[TaskRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    casa_routing: CasaRouting
    casa_rationale: str
    project_ref: str | None = None


class NotionPublishResult(BaseModel):
    page_id: str
    page_url: str | None = None
    promotion_state: PromotionState = "Not Promoted"
    promoted_task_ids: list[str] = Field(default_factory=list)
    promoted_decision_ids: list[str] = Field(default_factory=list)
    promoted_evidence_ids: list[str] = Field(default_factory=list)


class PipelineResult(BaseModel):
    original_event_id: str
    transcript_event_id: str
    analysis_event_id: str
    source_sha256: str
    transcript_sha256: str
    notion_page_id: str | None = None
    notion_page_url: str | None = None
    promotion_state: PromotionState | None = None
    promoted_task_ids: list[str] = Field(default_factory=list)
    promoted_decision_ids: list[str] = Field(default_factory=list)
    promoted_evidence_ids: list[str] = Field(default_factory=list)
    analysis: MediaAnalysis
    transcript: TranscriptResult
