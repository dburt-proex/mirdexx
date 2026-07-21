# Mirdexx

**Mirdexx remembers the work worth keeping.**

Mirdexx is a local-first artifact intelligence layer that observes explicitly approved work surfaces, filters operational noise, and preserves valuable decisions, evidence, revisions, code changes, research, and project assets automatically.

The system is designed to reduce the manual burden of saving, naming, organizing, and documenting meaningful work without becoming a surveillance tool or unrestricted activity logger.

## Core problem

Modern AI-assisted work produces a large volume of activity, but only a small portion becomes durable value.

Important artifacts are often lost inside:

- temporary files;
- repeated downloads;
- autosaves;
- fragmented notes;
- uncommitted code changes;
- long chat histories;
- duplicated documents;
- local folders with weak naming;
- project decisions that were never recorded.

Mirdexx addresses this by observing approved sources, evaluating candidate activity, rejecting noise, and creating a searchable artifact ledger for work that crosses a defined value threshold.

## Product principle

Mirdexx does not attempt to shadow every action.

It monitors only explicitly approved work surfaces and records only activity that meets deterministic value, evidence, and relevance requirements.

```text
Approved source event
→ normalize metadata
→ extract content or diff
→ evaluate value
→ reject noise or create artifact record
→ classify artifact
→ connect it to a project
→ preserve provenance
→ expose it through a local API and dashboard
```

## MVP objective

The first release should answer one practical question:

> What valuable artifacts, decisions, ideas, project changes, and evidence did I create today?

Version 0.1 is intentionally narrow:

- selected local folders;
- selected Git repositories;
- deterministic artifact scoring;
- SQLite artifact ledger;
- daily summary;
- local dashboard;
- no external AI dependency;
- no cloud infrastructure.

## Approved capture sources

The MVP may observe only sources explicitly added by the user:

1. Selected local folders
2. Selected Git repositories
3. Supported local files
4. Git commits and meaningful working-tree changes
5. A manual capture API endpoint for future integrations

Initial supported file types:

- Markdown and plain text
- PDF and DOCX
- JSON and YAML
- Python
- JavaScript and TypeScript
- HTML and CSS

## Explicitly prohibited behavior

Mirdexx must not implement:

- keylogging;
- covert screenshots;
- unrestricted clipboard monitoring;
- microphone or webcam recording;
- hidden persistence;
- employee monitoring;
- browser-history collection;
- email ingestion in the MVP;
- cloud synchronization in the MVP;
- automatic publishing;
- automatic deletion;
- external transmission of captured content;
- monitoring outside user-approved paths.

## Artifact classifications

The initial artifact taxonomy is:

- `CODE_CHANGE`
- `DOCUMENT`
- `DECISION`
- `RESEARCH`
- `ARCHITECTURE`
- `PROMPT`
- `EVIDENCE`
- `PROJECT_MILESTONE`
- `REFERENCE`
- `UNKNOWN`

## Deterministic value model

Every candidate event receives a score from 0 to 100.

| Dimension | Maximum |
|---|---:|
| Novelty | 20 |
| Project relevance | 20 |
| Durability | 15 |
| Evidence value | 15 |
| Reuse potential | 15 |
| Decision significance | 10 |
| User effort represented | 5 |

Penalties:

| Condition | Penalty |
|---|---:|
| Duplicate or near duplicate | -30 |
| Temporary or generated file | -30 |
| Trivial edit | -20 |
| Unsupported file type | -20 |
| Excluded path | Immediate rejection |

Default routing:

- `70–100`: retain as a high-value artifact
- `45–69`: retain for review
- `0–44`: discard as noise

Every score must remain explainable. The system should store the contributing dimensions, penalties, confidence, and rejection reasons.

## Noise filtering defaults

Mirdexx should ignore:

- `.git/`
- `node_modules/`
- `.venv/` and `venv/`
- `__pycache__/`
- `.pytest_cache/`
- `dist/`
- `build/`
- `.next/`
- coverage output
- temporary and swap files
- files beginning with `~$`
- unchanged content hashes
- repeated autosaves
- generated dependency churn without meaningful source changes

## Privacy and custody boundaries

Mirdexx is local-first by default.

Required controls:

- explicit source allowlist;
- visible pause control;
- no reading outside approved paths;
- no external network transmission;
- secret and credential redaction;
- immutable provenance fields;
- visible processing history;
- manual artifact deletion;
- JSON export;
- database backup;
- clear uninstall instructions;
- minimum-content storage for large or sensitive files.

For sensitive or large files, Mirdexx should retain metadata, hashes, selected excerpts, and a summary rather than silently duplicating full content.

## Proposed technical stack

- Python 3.11+
- FastAPI
- Pydantic
- SQLAlchemy or SQLModel
- SQLite
- `watchdog` for filesystem events
- GitPython or controlled subprocess-based Git inspection
- Pytest
- Server-rendered HTML or minimal vanilla JavaScript

The MVP should avoid unnecessary frontend frameworks and external model dependencies.

## Core services

```text
app/
├── api/             # FastAPI routes and request models
├── core/            # configuration, security, logging, lifecycle
├── models/          # database and domain models
├── services/
│   ├── watcher.py
│   ├── git_observer.py
│   ├── extractor.py
│   ├── noise_filter.py
│   ├── scorer.py
│   ├── classifier.py
│   ├── deduplicator.py
│   └── summarizer.py
├── templates/       # local dashboard
└── static/          # minimal dashboard assets
```

## Planned API

- `GET /health`
- `GET /config`
- `POST /sources`
- `GET /sources`
- `DELETE /sources/{id}`
- `POST /capture`
- `GET /events`
- `GET /artifacts`
- `GET /artifacts/{id}`
- `PATCH /artifacts/{id}`
- `GET /projects`
- `GET /daily-summary`
- `GET /metrics`

## Required data domains

### Source events

Preserve the raw observation boundary:

- timestamp;
- source type;
- source path;
- event type;
- content hash;
- previous hash;
- raw metadata;
- processing status.

### Artifacts

Preserve the durable asset record:

- title;
- artifact type;
- summary;
- project;
- source path;
- content hash;
- version;
- value score;
- confidence;
- status;
- provenance;
- tags.

### Score breakdown

Preserve the decision explanation:

- component scores;
- penalties;
- matched evidence;
- final route;
- explanation.

### Artifact relationships

Initial relationships:

- `REVISES`
- `SUPPORTS`
- `DERIVED_FROM`
- `DUPLICATES`
- `RELATED_TO`
- `SUPERSEDES`

## Required regression cases

The MVP is not complete until tests prove that:

1. A meaningful architecture document is retained.
2. A generated cache file is rejected.
3. An unchanged duplicate is rejected.
4. A substantial Git diff is retained as `CODE_CHANGE`.
5. A destructive or security-sensitive diff is routed for review.
6. A source outside the allowlist is never read.
7. Credential-like values are redacted.
8. A trivial autosave does not create a new artifact.
9. A meaningful revision links to the earlier artifact.
10. The daily summary returns only retained and reviewable artifacts.

## OpenHands build contract

OpenHands should treat this repository as a governed implementation workspace.

Build in this order:

1. Repository scaffold
2. Configuration and database
3. Source allowlist
4. Filesystem watcher
5. Noise filtering
6. Deterministic scoring
7. Artifact storage
8. Git observer
9. API
10. Dashboard
11. Regression tests
12. Documentation
13. Final validation

Rules for implementation:

- preserve local-first architecture;
- do not add surveillance features;
- do not add cloud infrastructure;
- do not add external model dependencies during the MVP;
- do not monitor unapproved locations;
- do not silently store secrets;
- prefer deterministic logic before probabilistic classification;
- add tests with every behavioral increment;
- keep each change bounded and reversible;
- document known limitations honestly;
- do not claim completion unless acceptance checks pass.

At the end of each build increment, report:

- files changed;
- behavior added;
- tests executed and results;
- privacy implications;
- known limitations;
- rollback path;
- next bounded increment.

## Architecture reference

See [`docs/architecture-mindmap.md`](docs/architecture-mindmap.md) for the system mind map and component boundaries.

## Current status

**Stage:** foundation specification  
**Runtime implementation:** not started  
**Maturity:** concept-to-build handoff  
**Canonical repository:** `dburt-proex/mirdexx`

## License

No license has been selected yet. Treat the repository as all rights reserved until an explicit license is added.
