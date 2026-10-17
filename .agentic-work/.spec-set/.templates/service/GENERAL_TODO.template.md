# GENERAL TODO: [unique_task_name]

<!-- PURPOSE: Execution and evidence record for a nontrivial authorized ordinary task.
     Paths below are repository-root-relative.
     OUTPUT PATH: $REPO_ROOT/.reports/general_todo_[unique_task_name].md
     Common policy: .agentic-work/.work-models/WORK_INSTRUCTIONS.md
     Development transitions: .agentic-work/.work-models/flows/SDD_TDD.md
     Record classification: .agentic-work/.spec-set/AGENTS.md -->

## 1) Task contract

- Objective: <observable end state>
- Semantic impact / work kind / scope class: <classification>
- Scope, constraints, failure conditions, evaluation, output contract: <preflight/reference>
- Active Work Models and stage: <keys/stage>
- Governing requirements and applicable accepted specifications: <paths/revisions>
- TODO_SDD / TODO_TDD: <one active record per applicable stage, embedded here or linked>
- Repository commit at start: <full SHA>
- Environment/profile: <environment>

## 2) Affected surface

| PATH_OR_INTERFACE | CURRENT EVIDENCE | REQUIRED STATE | ACTION |
| --- | --- | --- | --- |
| <path/interface> | <evidence> | <target> | create/modify/delete/review/unchanged |

## 3) Ordered work

### 3.1) <coherent change or conformance check>

- Target: <paths>
- Required result: <result>
- Stage-record item / authority: <reference>
- Verify: <check and expected observation>
- Status/evidence: <pending or result>

## 4) Verification

| CRITERION_ID | EVIDENCE_KIND | METHOD | RESULT | EVIDENCE |
| --- | --- | --- | --- | --- |
| <criterion> | deterministic/semantic/integration/runtime_qualification/manual_inspection | <method> | PASS/FAIL/NOT_RUN | <result/reason> |

- Unintended-change check: <result>
- Validation not run: <reason/impact or none>
- No-op or evidence-reuse disposition: <active stage record/reference>

## 5) Completion record

- Files changed/generated/deleted: <list>
- Verification by evidence kind: <summary>
- Residual findings or uncertainty: <items or none>
- Delivery status: <commit/push or packaged fallback>
