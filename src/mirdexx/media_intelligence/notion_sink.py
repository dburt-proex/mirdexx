from __future__ import annotations

from datetime import datetime, timezone
import math
import mimetypes
import os
from pathlib import Path
from typing import Iterable

import httpx

from .models import MediaAnalysis, NotionPublishResult, PromotionState, TranscriptResult

NOTION_VERSION = "2026-03-11"
_SINGLE_PART_LIMIT = 20 * 1024 * 1024
_MULTI_PART_SIZE = 10 * 1024 * 1024
_TEXT_CHUNK = 1800


class NotionVideoIntelligenceSink:
    """Upload original media, write Video Intelligence, and optionally promote governed candidates."""

    def __init__(
        self,
        *,
        token: str | None = None,
        data_source_id: str | None = None,
        projects_data_source_id: str | None = None,
        tasks_data_source_id: str | None = None,
        decisions_data_source_id: str | None = None,
        evidence_data_source_id: str | None = None,
        promote_candidates: bool = False,
        client: httpx.Client | None = None,
        base_url: str = "https://api.notion.com",
    ) -> None:
        self.token = token or os.getenv("NOTION_API_KEY")
        self.data_source_id = data_source_id or os.getenv("NOTION_VIDEO_INTELLIGENCE_DATA_SOURCE_ID")
        self.projects_data_source_id = projects_data_source_id or os.getenv("NOTION_PROJECTS_DATA_SOURCE_ID")
        self.tasks_data_source_id = tasks_data_source_id or os.getenv("NOTION_TASKS_DATA_SOURCE_ID")
        self.decisions_data_source_id = decisions_data_source_id or os.getenv("NOTION_DECISIONS_DATA_SOURCE_ID")
        self.evidence_data_source_id = evidence_data_source_id or os.getenv("NOTION_EVIDENCE_DATA_SOURCE_ID")
        self.promote_candidates = promote_candidates
        if not self.token:
            raise ValueError("NOTION_API_KEY is required")
        if not self.data_source_id:
            raise ValueError("NOTION_VIDEO_INTELLIGENCE_DATA_SOURCE_ID is required")
        if self.promote_candidates:
            missing = [
                name
                for name, value in {
                    "NOTION_PROJECTS_DATA_SOURCE_ID": self.projects_data_source_id,
                    "NOTION_TASKS_DATA_SOURCE_ID": self.tasks_data_source_id,
                    "NOTION_DECISIONS_DATA_SOURCE_ID": self.decisions_data_source_id,
                    "NOTION_EVIDENCE_DATA_SOURCE_ID": self.evidence_data_source_id,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"canonical promotion requires: {', '.join(missing)}")
        self.client = client or httpx.Client(base_url=base_url, timeout=120.0)
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
        }

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
    ) -> NotionPublishResult:
        media_path = Path(media_path)
        upload_id = self.upload_media(media_path)
        initial_state: PromotionState = (
            "Rejected"
            if self.promote_candidates and analysis.casa_routing == "HALT"
            else "Candidate"
            if self.promote_candidates
            else "Not Promoted"
        )
        page = self._create_page(
            media_path,
            upload_id=upload_id,
            transcript=transcript,
            analysis=analysis,
            source_sha256=source_sha256,
            mirdexx_artifact_id=mirdexx_artifact_id,
            project_ref=project_ref,
            project_page_id=project_page_id,
            data_policy=data_policy,
            source_url=source_url,
            promotion_state=initial_state,
        )
        page_id = str(page["id"])
        self._append_page_content(
            page_id,
            media_path,
            upload_id,
            transcript,
            analysis,
            source_sha256,
            mirdexx_artifact_id,
        )

        receipt = NotionPublishResult(
            page_id=page_id,
            page_url=page.get("url"),
            promotion_state=initial_state,
        )
        if not self.promote_candidates or analysis.casa_routing == "HALT":
            return receipt

        try:
            task_ids, decision_ids, evidence_ids = self._promote_candidates(
                media_page_id=page_id,
                analysis=analysis,
                transcript_sha256=transcript_sha256,
                transcript_artifact_id=transcript_artifact_id,
                analysis_artifact_id=analysis_artifact_id,
                project_page_id=project_page_id,
                data_policy=data_policy,
            )
            final_state: PromotionState = "Promoted" if analysis.casa_routing == "ALLOW" else "REVIEW"
            self._set_promotion_state(page_id, final_state)
            return NotionPublishResult(
                page_id=page_id,
                page_url=page.get("url"),
                promotion_state=final_state,
                promoted_task_ids=task_ids,
                promoted_decision_ids=decision_ids,
                promoted_evidence_ids=evidence_ids,
            )
        except Exception:
            # Promotion is not transactional in Notion. Mark the source record for review
            # and do not silently retry; inspect backlinks before rerunning to avoid duplicates.
            self._set_promotion_state(page_id, "REVIEW")
            raise

    def upload_media(self, media_path: Path) -> str:
        media_path = Path(media_path)
        size = media_path.stat().st_size
        content_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
        if size <= _SINGLE_PART_LIMIT:
            created = self._json_request(
                "POST",
                "/v1/file_uploads",
                json={"mode": "single_part", "filename": media_path.name, "content_type": content_type},
            )
            upload_id = str(created["id"])
            with media_path.open("rb") as handle:
                self._multipart_request(
                    "POST",
                    f"/v1/file_uploads/{upload_id}/send",
                    files={"file": (media_path.name, handle, content_type)},
                )
            return upload_id

        parts = math.ceil(size / _MULTI_PART_SIZE)
        created = self._json_request(
            "POST",
            "/v1/file_uploads",
            json={
                "mode": "multi_part",
                "number_of_parts": parts,
                "filename": media_path.name,
                "content_type": content_type,
            },
        )
        upload_id = str(created["id"])
        with media_path.open("rb") as handle:
            for part_number in range(1, parts + 1):
                chunk = handle.read(_MULTI_PART_SIZE)
                self._multipart_request(
                    "POST",
                    f"/v1/file_uploads/{upload_id}/send",
                    data={"part_number": str(part_number)},
                    files={"file": (media_path.name, chunk, content_type)},
                )
        self._json_request("POST", f"/v1/file_uploads/{upload_id}/complete", json={})
        return upload_id

    def _create_page(
        self,
        media_path: Path,
        *,
        upload_id: str,
        transcript: TranscriptResult,
        analysis: MediaAnalysis,
        source_sha256: str,
        mirdexx_artifact_id: str,
        project_ref: str | None,
        project_page_id: str | None,
        data_policy: str,
        source_url: str | None,
        promotion_state: PromotionState,
    ) -> dict:
        properties: dict = {
            "Name": self._title(media_path.stem),
            "Status": {"select": {"name": "Analyzed"}},
            "Promotion State": {"select": {"name": promotion_state}},
            "Media Type": {"select": {"name": "Video" if media_path.suffix.lower() in {".mp4", ".mpeg", ".webm", ".mov", ".m4v", ".mkv"} else "Audio"}},
            "Media File": {"files": [{"name": media_path.name, "type": "file_upload", "file_upload": {"id": upload_id}}]},
            "Project Ref": self._rich_text(project_ref or ""),
            "Language": self._rich_text(transcript.language or ""),
            "Transcription Model": self._rich_text(transcript.model),
            "Diarized": {"checkbox": transcript.mode == "diarized"},
            "Source SHA256": self._rich_text(source_sha256),
            "Mirdexx Artifact ID": self._rich_text(mirdexx_artifact_id),
            "CASA Routing": {"select": {"name": analysis.casa_routing}},
            "Decision Count": {"number": len(analysis.decisions)},
            "Task Count": {"number": len(analysis.tasks)},
            "Evidence Count": {"number": len(analysis.evidence)},
            "Data Policy": {"select": {"name": data_policy}},
            "Processed At": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        }
        if project_page_id:
            properties["Project"] = self._relation(project_page_id)
        if transcript.duration_seconds is not None:
            properties["Duration Seconds"] = {"number": transcript.duration_seconds}
        if source_url:
            properties["Source URL"] = {"url": source_url}
        return self._create_record(self.data_source_id, properties)

    def _promote_candidates(
        self,
        *,
        media_page_id: str,
        analysis: MediaAnalysis,
        transcript_sha256: str,
        transcript_artifact_id: str,
        analysis_artifact_id: str,
        project_page_id: str | None,
        data_policy: str,
    ) -> tuple[list[str], list[str], list[str]]:
        task_ids: list[str] = []
        decision_ids: list[str] = []
        evidence_ids: list[str] = []

        for task in analysis.tasks:
            notes = [task.description.strip()]
            if task.owner:
                notes.append(f"Detected owner: {task.owner}")
            if task.evidence_refs:
                notes.append(f"Evidence refs: {', '.join(task.evidence_refs)}")
            properties: dict = {
                "Name": self._title(task.title),
                "Status": {"select": {"name": "Backlog"}},
                "Priority": {"select": {"name": task.priority}},
                "Notes": self._rich_text("\n".join(value for value in notes if value)),
                "Source Type": {"select": {"name": "Transcript"}},
                "CASA Routing": {"select": {"name": analysis.casa_routing}},
                "Governance State": {"select": {"name": "Reviewed" if analysis.casa_routing == "ALLOW" else "Candidate"}},
                "Mirdexx Artifact ID": self._rich_text(analysis_artifact_id),
                "Origin Media": self._relation(media_page_id),
            }
            if project_page_id:
                properties["Project"] = self._relation(project_page_id)
            if task.due_date:
                properties["Due Date"] = {"date": {"start": task.due_date.isoformat()}}
            record = self._create_record(self.tasks_data_source_id, properties)
            task_ids.append(str(record["id"]))

        for decision in analysis.decisions:
            properties = {
                "Decision": self._title(decision.decision),
                "Status": {"select": {"name": "Proposed" if analysis.casa_routing == "ALLOW" else "REVIEW"}},
                "Rationale": self._rich_text(decision.rationale),
                "Assumptions": self._rich_text("\n".join(decision.assumptions)),
                "Risks": self._rich_text("\n".join(decision.risks)),
                "CASA Routing": {"select": {"name": analysis.casa_routing}},
                "Mirdexx Artifact ID": self._rich_text(analysis_artifact_id),
                "Origin Media": self._relation(media_page_id),
            }
            if project_page_id:
                properties["Project"] = self._relation(project_page_id)
            record = self._create_record(self.decisions_data_source_id, properties)
            decision_ids.append(str(record["id"]))

        for evidence in analysis.evidence:
            support = evidence.support
            if evidence.speaker:
                support = f"Speaker {evidence.speaker}: {support}"
            properties = {
                "Evidence": self._title(evidence.claim),
                "Evidence Type": {"select": {"name": "Timestamp" if evidence.start is not None else "Transcript"}},
                "Support": self._rich_text(support),
                "Confidence": {"number": evidence.confidence},
                "Integrity SHA256": self._rich_text(transcript_sha256),
                "Data Policy": {"select": {"name": data_policy}},
                "Mirdexx Artifact ID": self._rich_text(transcript_artifact_id),
                "Origin Media": self._relation(media_page_id),
            }
            if project_page_id:
                properties["Project"] = self._relation(project_page_id)
            if evidence.start is not None:
                properties["Timestamp Start"] = {"number": evidence.start}
            if evidence.end is not None:
                properties["Timestamp End"] = {"number": evidence.end}
            record = self._create_record(self.evidence_data_source_id, properties)
            evidence_ids.append(str(record["id"]))

        return task_ids, decision_ids, evidence_ids

    def _set_promotion_state(self, page_id: str, state: PromotionState) -> None:
        self._json_request(
            "PATCH",
            f"/v1/pages/{page_id}",
            json={"properties": {"Promotion State": {"select": {"name": state}}}},
        )

    def _create_record(self, data_source_id: str | None, properties: dict) -> dict:
        if not data_source_id:
            raise ValueError("target Notion data source is not configured")
        return self._json_request(
            "POST",
            "/v1/pages",
            json={"parent": {"data_source_id": data_source_id}, "properties": properties},
        )

    def _append_page_content(
        self,
        page_id: str,
        media_path: Path,
        upload_id: str,
        transcript: TranscriptResult,
        analysis: MediaAnalysis,
        source_sha256: str,
        mirdexx_artifact_id: str,
    ) -> None:
        media_type = "video" if media_path.suffix.lower() in {".mp4", ".mpeg", ".webm", ".mov", ".m4v", ".mkv"} else "audio"
        blocks: list[dict] = [
            {"object": "block", "type": media_type, media_type: {"type": "file_upload", "file_upload": {"id": upload_id}}},
            self._heading("Transcript", 2),
        ]
        blocks.extend(self._transcript_blocks(transcript))
        blocks.extend([self._heading("Analysis", 2), self._paragraph(analysis.summary), self._heading("Decisions", 3)])
        if analysis.decisions:
            for item in analysis.decisions:
                blocks.append(self._bulleted(f"[{item.status.upper()} | {item.confidence:.2f}] {item.decision} — {item.rationale}"))
        else:
            blocks.append(self._paragraph("No supported decisions extracted."))
        blocks.append(self._heading("Tasks", 3))
        if analysis.tasks:
            for item in analysis.tasks:
                owner = f" | owner: {item.owner}" if item.owner else ""
                due = f" | due: {item.due_date.isoformat()}" if item.due_date else ""
                blocks.append(self._todo(f"[{item.priority}] {item.title}{owner}{due} — {item.description}"))
        else:
            blocks.append(self._paragraph("No supported tasks extracted."))
        blocks.append(self._heading("Evidence", 3))
        if analysis.evidence:
            for item in analysis.evidence:
                location = self._location(item.start, item.end, item.speaker)
                blocks.append(self._bulleted(f"{location}{item.claim} — {item.support} (confidence {item.confidence:.2f})"))
        else:
            blocks.append(self._paragraph("No discrete evidence items extracted."))
        blocks.extend([
            self._heading("CASA Routing", 3),
            self._paragraph(f"{analysis.casa_routing}: {analysis.casa_rationale}"),
            self._heading("Provenance", 3),
            self._paragraph(f"Mirdexx original event: {mirdexx_artifact_id}"),
            self._paragraph(f"Source SHA256: {source_sha256}"),
        ])
        for batch in self._batches(blocks, 100):
            self._json_request("PATCH", f"/v1/blocks/{page_id}/children", json={"children": batch})

    def _transcript_blocks(self, transcript: TranscriptResult) -> list[dict]:
        if transcript.segments:
            return [self._paragraph(f"{self._location(s.start, s.end, s.speaker)}{s.text}") for s in transcript.segments]
        return [self._paragraph(chunk) for chunk in self._chunks(transcript.text)]

    def _json_request(self, method: str, path: str, *, json: dict) -> dict:
        response = self.client.request(method, path, headers={**self.headers, "Content-Type": "application/json"}, json=json)
        response.raise_for_status()
        return response.json()

    def _multipart_request(self, method: str, path: str, *, files: dict, data: dict | None = None) -> dict:
        response = self.client.request(method, path, headers=self.headers, files=files, data=data or {})
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _title(text: str) -> dict:
        return {"title": [{"type": "text", "text": {"content": text[:1900]}}]}

    @staticmethod
    def _rich_text(text: str) -> dict:
        return {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]} if text else {"rich_text": []}

    @staticmethod
    def _relation(page_id: str) -> dict:
        return {"relation": [{"id": page_id}]}

    @staticmethod
    def _text(text: str) -> list[dict]:
        return [{"type": "text", "text": {"content": text[:_TEXT_CHUNK]}}]

    @classmethod
    def _paragraph(cls, text: str) -> dict:
        return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": cls._text(text)}}

    @classmethod
    def _bulleted(cls, text: str) -> dict:
        return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": cls._text(text)}}

    @classmethod
    def _todo(cls, text: str) -> dict:
        return {"object": "block", "type": "to_do", "to_do": {"rich_text": cls._text(text), "checked": False}}

    @classmethod
    def _heading(cls, text: str, level: int) -> dict:
        key = f"heading_{level}"
        return {"object": "block", "type": key, key: {"rich_text": cls._text(text)}}

    @staticmethod
    def _chunks(text: str, size: int = _TEXT_CHUNK) -> Iterable[str]:
        text = text.strip()
        if not text:
            return ["(empty transcript)"]
        return [text[i : i + size] for i in range(0, len(text), size)]

    @staticmethod
    def _batches(items: list[dict], size: int) -> Iterable[list[dict]]:
        for index in range(0, len(items), size):
            yield items[index : index + size]

    @staticmethod
    def _timestamp(value: float | None) -> str:
        if value is None:
            return ""
        total = max(0, int(value))
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @classmethod
    def _location(cls, start: float | None, end: float | None, speaker: str | None) -> str:
        bits: list[str] = []
        if start is not None:
            stamp = cls._timestamp(start)
            if end is not None:
                stamp += f"–{cls._timestamp(end)}"
            bits.append(stamp)
        if speaker:
            bits.append(speaker)
        return f"[{' | '.join(bits)}] " if bits else ""
