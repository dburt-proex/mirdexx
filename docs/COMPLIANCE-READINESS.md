# Mirdexx Compliance Readiness Baseline

Status: REVIEW  
Assessment date: 2026-08-16  
Canonical control registry: `dburt-proex/casa/governance/CONTROL-REGISTRY.yaml` v0.1

## Claim boundary

Mirdexx may describe local-first custody controls, source allowlisting, provenance, ledger behavior and tested boundaries where supported. It must not claim ISO/IEC certification, SOC 2 attestation or full compliance without independent assurance.

## Scope

Mirdexx is assessed as the evidence/provenance substrate: approved-source registry, local custody, artifact/event ledger, provenance, database persistence, privacy boundaries and regression tests.

## Evidence-backed strengths

- Explicit approved-source allowlist and prohibited surveillance behavior.
- Local-first/no-external-transmission design boundary.
- Event ledger, source registry and provenance implementation.
- Dedicated event-ledger and boundary-hardening regression tests.
- Secret-redaction, deletion, backup and minimum-content requirements in the product contract.

## Gap register

| Priority | Control | Gap | Closure evidence |
|---|---|---|---|
| P0 | INC-001 | Incident-response lifecycle absent | IR SOP + tabletop + RCA + corrective-action/retest records |
| P0 | DAT-001 | Formal classification/retention schedule and exception process incomplete | approved data inventory/classification/retention/deletion policy |
| P0 | BCM-001 | Backup requirement lacks tested recovery proof | backup record + restore test + RTO/RPO/recovery receipt |
| P0 | RSK-001 | Boundary logic is not formal organizational risk treatment | risk register + treatment + residual-risk acceptance |
| P1 | IAM-001 | Source allowlisting is not identity/access governance | identity/privilege matrix + access review |
| P1 | CHG-001 | CI evidence is not canonical compliance evidence | PR/CI evidence receipt + commit binding |
| P1 | SEC-001 | Formal threat model/independent review incomplete | threat model + security assessment + closure register |
| P1 | SUP-001 | No formal dependency/supplier risk process | dependency inventory + supplier assessment/review |
| P1 | REV-001 | Tests are not periodic management review | audit report + management review + CAPA status |

## AI applicability

`AI-001` is currently `NOT_APPLICABLE` to the MVP because the repository explicitly excludes external AI dependencies. This status must be reopened if probabilistic/model-based classification, summarization or external AI services enter scope.

## Validation workflow

1. Resolve manifest evidence paths against the assessed commit.
2. Execute the repository test workflow and event-ledger/boundary-hardening tests in an authorized environment.
3. Capture results as canonical CASA evidence receipts, including source commit and artifact hash where applicable.
4. Run a backup/restore test before raising BCM-001 beyond PARTIAL.
5. Close or formally accept P0 risks.
6. Conduct internal readiness review before external assurance.

## Phase 10 entry criteria

External assurance is blocked until P0 findings are closed or formally risk-treated, exact applicable framework requirements are mapped, evidence retention/recovery is demonstrated, and management/operator review is recorded.
