from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from .models import TranscriptResult, TranscriptSegment, TranscriptionMode

SUPPORTED_EXTENSIONS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}


class OpenAITranscriber:
    """Normalize OpenAI transcription variants behind one contract."""

    def __init__(self, client: OpenAI | None = None) -> None:
        self.client = client or OpenAI()
        self.standard_model = os.getenv("MIRDEXX_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
        self.timestamp_model = os.getenv("MIRDEXX_TIMESTAMP_MODEL", "whisper-1")
        self.diarize_model = os.getenv("MIRDEXX_DIARIZE_MODEL", "gpt-4o-transcribe-diarize")

    def transcribe(self, media_path: Path, *, mode: TranscriptionMode = "standard", language: str | None = None) -> TranscriptResult:
        media_path = Path(media_path)
        if media_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported media extension: {media_path.suffix}")
        if not media_path.is_file():
            raise FileNotFoundError(media_path)

        with media_path.open("rb") as media:
            if mode == "diarized":
                response = self.client.audio.transcriptions.create(
                    file=media,
                    model=self.diarize_model,
                    response_format="diarized_json",
                    chunking_strategy="auto",
                    **({"language": language} if language else {}),
                )
                model = self.diarize_model
            elif mode == "timestamped":
                response = self.client.audio.transcriptions.create(
                    file=media,
                    model=self.timestamp_model,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    **({"language": language} if language else {}),
                )
                model = self.timestamp_model
            else:
                response = self.client.audio.transcriptions.create(
                    file=media,
                    model=self.standard_model,
                    response_format="json",
                    **({"language": language} if language else {}),
                )
                model = self.standard_model

        data = self._as_dict(response)
        return TranscriptResult(
            text=str(data.get("text") or "").strip(),
            model=model,
            mode=mode,
            language=data.get("language") or language,
            duration_seconds=self._float_or_none(data.get("duration")),
            segments=self._segments(data),
        )

    @staticmethod
    def _as_dict(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "to_dict"):
            return response.to_dict()
        raise TypeError("unsupported transcription response type")

    @staticmethod
    def _segments(data: dict[str, Any]) -> list[TranscriptSegment]:
        raw_segments = data.get("segments") or []
        normalized: list[TranscriptSegment] = []
        for item in raw_segments:
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            normalized.append(
                TranscriptSegment(
                    start=OpenAITranscriber._float_or_none(item.get("start")),
                    end=OpenAITranscriber._float_or_none(item.get("end")),
                    speaker=str(item.get("speaker")) if item.get("speaker") is not None else None,
                    text=text,
                )
            )
        return normalized

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
