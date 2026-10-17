# PATCH_<NNN> TODO | <short patch title>

<!-- PURPOSE: Compact record when the governing task explicitly requires PATCH.
     Paths below are repository-root-relative.
     OUTPUT PATH: $REPO_ROOT/.reports/patch_todo_PATCH_<NNN>.md
     PATCH is an artifact specialization, not an independent development lifecycle.
     Common policy: .agentic-work/.work-models/WORK_INSTRUCTIONS.md
     Development transitions: .agentic-work/.work-models/flows/SDD_TDD.md
     Test/conformance contract: .agentic-work/.work-models/profiles/TDD.md -->

## 1) Patch contract

- Semantic impact: `none`
- Work kind: <classification from .agentic-work/.spec-set/AGENTS.md>
- Scope class: `local` | `cross_document`
- Governing accepted specification IDs/revision: <references or justified not applicable>
- Objective: <observable result>
- Dependencies/prerequisites: <items or none>
- Public contract changes allowed by accepted specification: <exact surface or none>
- In scope / out of scope: <boundaries>
- Constraints/invariants: <accepted behavior>
- Failure conditions: <invalidating conditions>
- Evaluation criteria and output contract: <results/artifacts>
- Active TODO_TDD: <embed or reference the stage-owned record>

PATCH records implementation or qualification of accepted semantics. Specification discrepancies and resulting stage changes use the lifecycle's failure routing, not a separate PATCH rule.

## 2) Controlled evidence

- Repository commit: <full SHA>
- Environment/profile: <runtime/input conditions>
- Controlled assets/configuration: <exact values or not applicable>
- Baseline command/result and current applicability: <evidence or not applicable>

Before/after measurements use the same assets, configuration, runtime flags, input mode, and environment as far as practical. Record deviations.

## 3) Required changes or checks

| ORDER | TARGET | REQUIRED_RESULT | STAGE_ITEM_OR_AUTHORITY | VERIFY_BY |
| --- | --- | --- | --- | --- |
| 1 | <path/subsystem> | <result> | <reference> | <focused check> |

## 4) Verification

| EVIDENCE_KIND | REQUIRED_CHECK | RESULT | EVIDENCE |
| --- | --- | --- | --- |
| deterministic | <check or not applicable> | PASS/FAIL/NOT_RUN | <result> |
| integration | <check or not applicable> | PASS/FAIL/NOT_RUN | <result> |
| runtime_qualification | <measurement or not applicable> | PASS/FAIL/NOT_RUN | <comparison> |
| semantic | <agreement with accepted specification> | PASS/FAIL | <findings> |

For measurement-oriented tests under TDD, define metric meaning, state/timestamp origin, and calculation method. Real-time sleep assertions SHOULD be avoided when an injectable clock or controlled event is practical.

## 5) Completion record

- Files changed/generated/deleted: <list>
- Governing requirement results and no-op disposition: <summary>
- Material functional/performance/compatibility risks: <items>
- Commands and environment: <evidence references>
- Failures, unexecuted checks, and uncertainty: <items or none>
- Deferred out-of-scope work: <items or none>
- Delivery status: <commit/push or packaged fallback>
