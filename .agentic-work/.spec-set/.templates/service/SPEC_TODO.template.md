# SPEC TODO: [unique_task_name]

<!-- PURPOSE: Specification-change and governed-propagation record.
     Paths below are repository-root-relative.
     OUTPUT PATH: $REPO_ROOT/.reports/spec_todo_[unique_task_name].md
     Common policy: .agentic-work/.work-models/WORK_INSTRUCTIONS.md
     Stage contract: .agentic-work/.work-models/profiles/SDD.md
     Artifact integration: .agentic-work/.spec-set/AGENTS.md -->

## 1) Task contract

- Objective and classification: <outcome; semantic impact, work kind, scope>
- Governing requirements: <paths/section IDs>
- Validated desirable end result: <record/reference>
- Repository commit at start: <full SHA>
- Scope, failure conditions, evaluation, output contract: <SPEC_PRE/reference>
- TODO_SDD: <embed the stage-owned record here or reference its sole location>
- Comparison/change-plan review: <result and defect dispositions>

## 2) Dependency-ordered artifact changes

| ORDER | AUTHORITATIVE_OWNER | TARGET | REQUIRED_CHANGE | INVALIDATED_CONSUMERS_OR_DIMENSIONS |
| --- | --- | --- | --- | --- |
| 1 | <owner> | <path/section> | <change or unchanged disposition> | <affected consumers/reviews> |

Use Spec Set's owning graph and templates for propagation ordering.

## 3) Work items

### 3.1) <specification delta>

- TODO_SDD item and target IDs: <references>
- Required normative result: <result>
- Candidate revision: <identity>
- Verify: <semantic review and applicable mechanical check>
- Status/evidence: <result>

## 4) Validation and cascade closure

| CRITERION_ID | EVIDENCE_KIND | METHOD | RESULT | EVIDENCE |
| --- | --- | --- | --- | --- |
| <criterion> | semantic/deterministic | <method> | PASS/FAIL/NOT_RUN | <evidence> |

- Cascade closure: <downstream artifacts revised/regenerated/reviewed unchanged>
- File-scope check: <result>
- Remaining candidate defects: <items or none>

## 5) Acceptance and completion record

- Validated candidate revision: <identity>
- Acceptance mechanism and authority: <reference>
- Acceptance result and scope: <accepted/pending/rejected; evidence>
- Files changed/generated/deleted: <list>
- Residual findings or uncertainty: <items or none>
- Delivery status: <commit/push or packaged fallback>

Report candidate validation and specification acceptance separately under SDD.
