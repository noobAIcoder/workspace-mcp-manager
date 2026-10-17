# REPORT GAP: <scope>

<!-- PURPOSE: Informational gap or audit report.
     Paths below are repository-root-relative.
     OUTPUT PATH: $REPO_ROOT/.reports/report_gap_[scope].md
     This report is evidence only and does not replace accepted authority or execution artifacts.
     Severity and review-dimension definitions: .agentic-work/.spec-set/AGENTS.md -->

## 1) Scope and provenance

- Authoritative source(s): <paths/sections>
- Evidence scope: <repository paths, generated artifacts, runtime evidence>
- Repository commit: <full SHA>
- Verified at: <YYYY-MM-DDTHH:MM:SSZ>
- Environment/profile: <environment>
- Exclusions: <out-of-scope areas>

## 2) Summary

- Overall status: `on_track` | `at_risk` | `blocked`
- Implemented/covered: <count or concise list>
- Partial: <count or concise list>
- Missing: <count or concise list>
- Principal consequence: <summary>

## 3) Coverage

| AUTHORITY_OR_REQUIREMENT_ID | STATUS | EVIDENCE | NOTES |
| --- | --- | --- | --- |
| <current Spec Set ID or authority section> | implemented/partial/missing/not_applicable | <paths/results> | <notes> |

## 4) Finding ledger

### GAP_<NNN> | <title>

- severity: `P0` | `P1` | `P2` | `P3`
- confidence: `confirmed` | `high` | `tentative`
- category: <authority, semantic, grammar, enforcement, maintainability, behavior, or other>
- authority/file/section: <owner and location>
- evidence: <concrete path, excerpt, command, or result>
- consequence: <impact and propagation radius>
- authoritative_owner: <single owner>
- semantic_impact: `none` | `clarification` | `semantic_change`
- work_kind: <central classification>
- recommended_correction: <root correction before mirrors/consumers>
- affected_review_dimensions: <authority,semantic_architecture,enforcement,completeness,instruction_economy,behavioral_qualification subset>

## 5) Validation and qualification

| EVIDENCE_KIND | RESULT | EVIDENCE_OR_LIMIT |
| --- | --- | --- |
| deterministic | <result/not run> | <evidence> |
| semantic | <result/not run> | <evidence> |
| integration | <result/not applicable> | <evidence> |
| runtime_qualification | <result/not applicable> | <evidence> |
| manual_inspection | <result/not applicable> | <evidence> |

## 6) Open questions and next action

- <question or none>
- Recommended remediation grouping: <owner-based batch>
- Re-review dimensions: <affected named dimensions only>
