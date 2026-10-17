# SLICE TODO: SLICE_###

<!-- PURPOSE: Execution and evidence record for one materialized slice.
     INPUT: The accepted slice plus current repository evidence from SLICE_PRE.
     Paths below are repository-root-relative.
     OUTPUT PATH: $REPO_ROOT/.reports/slice_todo_SLICE_###.md
     Common policy: .agentic-work/.work-models/WORK_INSTRUCTIONS.md
     Development transitions: .agentic-work/.work-models/flows/SDD_TDD.md
     Artifact integration: .agentic-work/.spec-set/AGENTS.md
     Test/conformance record: .agentic-work/.work-models/profiles/TDD.md -->

## 1) Task contract

- Slice key/path/revision and acceptance evidence: <references>
- Objective and acceptance anchors: <from slice>
- Guardrails and migration keys: <from slice>
- In-scope/out-of-scope work: <from slice>
- Resolved blocking decisions: <decision or none>
- Repository commit at preflight: <full SHA>
- Environment/profile: <environment>
- Unrelated local changes to preserve: <paths>
- TODO_TDD: <embed the stage-owned record here or reference its sole location>

## 2) Deliverables

| PATH | ACTION | SLICE_REFERENCE | EXPECTED_RESULT |
| --- | --- | --- | --- |
| <path> | create/modify/delete/unchanged | <SX section/change key> | <result> |

## 3) Baseline

| CHECK | COMMAND_OR_INSPECTION | RESULT |
| --- | --- | --- |
| Existing tests/evidence and applicability | <check or not-run reason> | <result> |
| Repository state | git status --short | <result> |
| Preconditions | <slice-declared state check> | <result> |

## 4) Ordered work

### 4.1) <slice-derived change or conformance check>

- TODO_TDD item and slice references: <IDs>
- Files: <paths>
- Required result: <behavior/artifact>
- Tests/qualification criteria: <active record/reference>
- Status/evidence: <pending or result>

## 5) Final qualification

| ACCEPTANCE_GUARDRAIL_OR_TEST_ID | EVIDENCE_KIND | METHOD | RESULT | EVIDENCE |
| --- | --- | --- | --- | --- |
| <ID> | deterministic/semantic/integration/runtime_qualification/manual_inspection | <method> | PASS/FAIL/NOT_RUN | <evidence> |

- Materially affected regression: <result or not-run reason>
- File-scope check: <result>
- Semantic agreement with the slice: <result/findings>
- No-op/reused evidence: <disposition under active lifecycle>

## 6) Completion record

- Files changed/generated/deleted: <list>
- Acceptance/guardrail results: <summary>
- Validation not run and uncertainty: <summary>
- Deferred work explicitly outside the slice: <items or none>
- Delivery status: <commit/push or packaged fallback>
