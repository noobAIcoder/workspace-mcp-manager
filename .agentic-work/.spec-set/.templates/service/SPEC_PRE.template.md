# SPEC PRE: <TITLE>

<!-- PURPOSE: Artifact-specific preflight for an authorized specification change.
     Paths below are repository-root-relative.
     OUTPUT PATH: $REPO_ROOT/.reports/spec_pre_[unique_task_name].md
     Common policy: .agentic-work/.work-models/WORK_INSTRUCTIONS.md
     Stage contract: .agentic-work/.work-models/profiles/SDD.md
     Artifact integration: .agentic-work/.spec-set/AGENTS.md -->

## 1) Change contract

- Objective: <canonical-spec outcome>
- Trigger: <request, accepted change, or discovered defect>
- Semantic impact / work kind / scope class: <classification>
- Governing requirements: <paths/sections>
- Validated desirable end result: <active record/reference>
- Specification comparison and TODO_SDD: <active record/reference>
- Scope and non-goals: <boundaries>
- Repository commit: <full SHA>
- Applicable acceptance mechanism: <authority/mechanism>

## 2) Artifact cascade

| DOCUMENT | ROLE | CHANGE_OR_REVIEW | IMMEDIATE CAUSE | DOWNSTREAM EFFECT |
| --- | --- | --- | --- | --- |
| docs/... | origin/derived/mirror | create/modify/review/regenerate/unchanged | <authority> | <effect> |

- Required mirrors/consumers: <manifest, validator, tests, generated artifacts, or none>
- Affected slices: <paths or none>
- Dependency-closure rationale: <owning graph and affected change areas>

## 3) Failure and evaluation

- Failure conditions: <unclosed cascade, hidden semantic change, unsupported invention>

| CRITERION_ID | OBSERVABLE RESULT | VERIFY_BY |
| --- | --- | --- |
| SPEC_PRE_001 | <result> | <semantic review or applicable mechanical check> |

- Expected artifacts: <paths>
- Required evidence: <semantic review, cascade closure, mechanical checks>

## 4) Verdict

- Blocking ambiguity: <unresolved decision or none>
- Material assumptions and risks: <items>
- Artifact preflight: `READY` | `BLOCKED`
- Next record: <SPEC_TODO or existing TODO_SDD location>

Preflight readiness is an artifact result; subsequent production, validation, and acceptance follow SDD.
