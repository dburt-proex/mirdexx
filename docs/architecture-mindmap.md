# Mirdexx Architecture Mind Map

This document gives OpenHands and future contributors a compact architectural view of Mirdexx before runtime implementation begins.

## System mind map

```mermaid
mindmap
  root((Mirdexx))
    Purpose
      Preserve valuable work automatically
      Filter operational noise
      Build searchable artifact memory
      Keep provenance and evidence
      Remain local-first
    Inputs
      Approved folders
        Markdown
        Text
        PDF
        DOCX
        JSON
        YAML
        Python
        JavaScript
        TypeScript
        HTML
        CSS
      Approved Git repositories
        Commits
        Working-tree diffs
        Branch changes
        Security-sensitive changes
      Manual capture API
    Boundary controls
      Explicit source allowlist
      Pause monitoring
      No reads outside approved paths
      No covert surveillance
      No external transmission
      Secret redaction
      Minimum-content storage
    Event pipeline
      Observe
        Filesystem watcher
        Git observer
        Manual capture endpoint
      Normalize
        Path
        Timestamp
        Source type
        Event type
        Content hash
        Previous hash
      Extract
        Metadata
        Text
        Diff
        Selected excerpts
      Filter
        Excluded paths
        Generated files
        Temporary files
        Duplicate hashes
        Trivial autosaves
        Unsupported formats
      Score
        Novelty
        Project relevance
        Durability
        Evidence value
        Reuse potential
        Decision significance
        Represented effort
        Penalties
      Route
        High value
          Retain
        Medium value
          Review
        Low value
          Reject as noise
      Classify
        CODE_CHANGE
        DOCUMENT
        DECISION
        RESEARCH
        ARCHITECTURE
        PROMPT
        EVIDENCE
        PROJECT_MILESTONE
        REFERENCE
        UNKNOWN
      Persist
        Source event
        Artifact
        Score breakdown
        Artifact relationship
        Provenance
      Expose
        FastAPI
        Local dashboard
        Daily summary
        Metrics
        JSON export
    Storage
      SQLite
        source_events
        artifacts
        score_breakdown
        artifact_links
        watched_sources
        processing_history
      File references
        Hashes
        Paths
        Excerpts
        Summaries
    Relationships
      REVISES
      SUPPORTS
      DERIVED_FROM
      DUPLICATES
      RELATED_TO
      SUPERSEDES
    Services
      watcher.py
      git_observer.py
      extractor.py
      noise_filter.py
      scorer.py
      classifier.py
      deduplicator.py
      summarizer.py
      redactor.py
      provenance.py
    API
      Health
      Configuration
      Sources
      Capture
      Events
      Artifacts
      Projects
      Daily summary
      Metrics
    Dashboard
      Captured today
      High-value count
      Review queue
      Ignored-noise count
      Active sources
      Project groups
      Recent decisions
      Recent code changes
      Score explanations
      Pause control
    Governance
      Deterministic first
      Explainable scores
      Human review boundary
      Immutable provenance
      Reversible changes
      Honest confidence
      No silent policy mutation
    Testing
      Retain meaningful document
      Reject cache file
      Reject duplicate
      Retain substantial Git diff
      Review destructive diff
      Block unapproved source
      Redact credential
      Ignore trivial autosave
      Link meaningful revision
      Return governed daily summary
    Delivery phases
      Phase 1 Foundation
        Repository scaffold
        Configuration
        Database
      Phase 2 Capture
        Source allowlist
        Filesystem watcher
        Git observer
      Phase 3 Intelligence
        Noise filter
        Deterministic scorer
        Classifier
        Deduplication
      Phase 4 Access
        API
        Dashboard
        Daily summary
      Phase 5 Assurance
        Regression tests
        Privacy documentation
        Operating guide
        Final validation
```

## Primary component flow

```mermaid
flowchart LR
    A[Approved source event] --> B[Source boundary check]
    B -->|Denied| X[Reject without reading]
    B -->|Allowed| C[Normalize metadata]
    C --> D[Extract content or diff]
    D --> E[Redact likely secrets]
    E --> F[Noise and duplicate filter]
    F -->|Noise| G[Record rejection reason]
    F -->|Candidate| H[Deterministic value scoring]
    H --> I{Route}
    I -->|70-100| J[Retain artifact]
    I -->|45-69| K[Review queue]
    I -->|0-44| G
    J --> L[Classify and link]
    K --> L
    L --> M[(SQLite artifact ledger)]
    M --> N[Local API]
    M --> O[Dashboard]
    M --> P[Daily summary]
    M --> Q[JSON export and backup]
```

## Trust boundary model

```mermaid
flowchart TB
    U[User] -->|Explicit approval| S[Watched source registry]
    S --> W[Watcher and Git observer]
    W --> P[Processing pipeline]
    P --> D[(Local SQLite)]
    D --> A[Local API and dashboard]

    S -. blocks .-> O[Unapproved paths]
    P -. redacts .-> C[Credentials and secrets]
    P -. no transmission .-> N[External network]
    U -->|Pause / remove / delete / export| A
```

## Architectural invariants

1. **No observation without explicit source approval.**
2. **No artifact without an explainable score or manual capture record.**
3. **No silent external transmission.**
4. **No covert monitoring features.**
5. **No inferred decision or milestone without source evidence and confidence.**
6. **No duplicate artifact when the content hash and provenance are unchanged.**
7. **No meaningful revision without a relationship to prior state when one exists.**
8. **No behavioral increment without regression evidence.**
9. **No claim of completion without runnable validation.**
10. **No probabilistic dependency in the MVP when deterministic logic is sufficient.**

## Initial repository target structure

```text
mirdexx/
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   └── mirdexx.example.yaml
├── app/
│   ├── main.py
│   ├── api/
│   ├── core/
│   ├── models/
│   ├── services/
│   │   ├── watcher.py
│   │   ├── git_observer.py
│   │   ├── extractor.py
│   │   ├── noise_filter.py
│   │   ├── scorer.py
│   │   ├── classifier.py
│   │   ├── deduplicator.py
│   │   ├── redactor.py
│   │   └── provenance.py
│   ├── templates/
│   └── static/
├── tests/
│   ├── fixtures/
│   ├── test_noise_filter.py
│   ├── test_scoring.py
│   ├── test_deduplication.py
│   ├── test_source_boundaries.py
│   ├── test_redaction.py
│   └── test_api.py
├── scripts/
│   ├── run_dev.py
│   └── export_artifacts.py
└── docs/
    ├── architecture-mindmap.md
    ├── architecture.md
    ├── privacy-boundaries.md
    ├── scoring-model.md
    └── operating-guide.md
```

## OpenHands handoff note

OpenHands should use this mind map together with the root README as the initial implementation contract.

The first bounded build increment should create only the project scaffold, configuration model, database bootstrap, and tests proving the source allowlist boundary. It should not add watchers, external AI services, cloud infrastructure, or hidden background persistence in the first increment.
