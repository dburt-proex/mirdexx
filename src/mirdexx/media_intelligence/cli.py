from __future__ import annotations

import argparse
import json
from pathlib import Path

from mirdexx.database import bootstrap_database
from mirdexx.event_ledger import EventLedger
from mirdexx.source_registry import SourceRegistry

from .analyze import OpenAITranscriptAnalyzer
from .notion_sink import NotionVideoIntelligenceSink
from .pipeline import MediaIntelligencePipeline
from .transcribe import OpenAITranscriber


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mirdexx-media", description="Governed audio/video intelligence ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register-root", help="Authorize a media root for controlled-content processing")
    register.add_argument("root", type=Path)
    register.add_argument("--db", type=Path, default=Path(".mirdexx/mirdexx.db"))
    register.add_argument("--include", action="append", default=[])
    register.add_argument("--exclude", action="append", default=[])

    ingest = sub.add_parser("ingest", help="Transcribe, analyze, ledger, and publish one media file")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--source-id", required=True)
    ingest.add_argument("--db", type=Path, default=Path(".mirdexx/mirdexx.db"))
    ingest.add_argument("--mode", choices=["standard", "timestamped", "diarized"], default="standard")
    ingest.add_argument("--language")
    ingest.add_argument("--project")
    ingest.add_argument("--data-policy", choices=["Private", "Internal", "Public"], default="Internal")
    ingest.add_argument("--source-url")
    ingest.add_argument("--no-notion", action="store_true", help="Run through analysis without publishing to Notion")
    return parser


def main() -> int:
    args = _parser().parse_args()
    bootstrap_database(args.db)
    if args.command == "register-root":
        source = SourceRegistry(args.db).register(
            args.root,
            source_kind="FOLDER",
            custody_mode="CONTROLLED_CONTENT",
            policy_version="media-intelligence-v0.1",
            include_patterns=args.include,
            exclude_patterns=args.exclude,
        )
        print(json.dumps({"source_id": source.source_id, "root": str(source.canonical_root), "custody_mode": source.custody_mode}, indent=2))
        return 0

    sink = None if args.no_notion else NotionVideoIntelligenceSink()
    pipeline = MediaIntelligencePipeline(
        ledger=EventLedger(args.db),
        transcriber=OpenAITranscriber(),
        analyzer=OpenAITranscriptAnalyzer(),
        sink=sink,
    )
    result = pipeline.ingest(
        args.file,
        source_id=args.source_id,
        mode=args.mode,
        language=args.language,
        project_ref=args.project,
        data_policy=args.data_policy,
        source_url=args.source_url,
    )
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
