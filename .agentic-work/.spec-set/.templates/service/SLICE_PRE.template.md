# SLICE PRE: SLICE_###

<!-- PURPOSE: Determine artifact and repository readiness for a materialized slice.
     INPUT: One materialized SLICE specification.
     Paths below are repository-root-relative.
     OUTPUT PATH: $REPO_ROOT/.reports/slice_pre_SLICE_###.md
     Common policy: .agentic-work/.work-models/WORK_INSTRUCTIONS.md
     Development transitions: .agentic-work/.work-models/flows/SDD_TDD.md
     Artifact integration: .agentic-work/.spec-set/AGENTS.md -->

## 1) Slice identity and provenance

- Slice key/title/class: <from SX-INP-INPRT>
- Specification revision and acceptance evidence: <identity/reference>
- Acceptance anchors and guardrails: <from SX-INP-INPRT>
- Migration keys: <from SX-INP-INPRT or none>
- Dependencies and touched surfaces: <slice inputs and Slices Map edges>
- Repository commit inspected: <full SHA>
- Environment/profile: <environment>
- Evidence source: <inspection commands/paths>

## 2) Contract-driven specification review

Use `.agentic-work/.spec-set/.templates/codegen/SLICE_XXX.specset.template.md` as owner of readiness, typed fields, applicability, composition, and semantic review.

| REVIEW_KIND | AUTHORITY | RESULT | EVIDENCE |
| --- | --- | --- | --- |
| Deterministic mechanics | owning template + index + ruleset | PASS/FAIL/NOT_RUN | <evidence/reason> |
| Semantic completeness | owning template semantic_review | PASS/FAIL | <findings> |
| Composition fidelity | owning template composition | PASS/FAIL | <upstream context fidelity> |

Artifact readiness, specification acceptance, and current repository readiness are separate results.

## 3) Repository-state verification

| EXPECTATION_FROM_SLICE | ACTUAL_EVIDENCE | RESULT | CONSEQUENCE |
| --- | --- | --- | --- |
| <prerequisite or expected state> | <path/command/output> | MATCHES/IMPLEMENTABLE_DELTA/BLOCKING_DIVERGENCE | <impact> |

- `MATCHES`: prerequisite state agrees with the slice.
- `IMPLEMENTABLE_DELTA`: the target change is absent, but prerequisites are compatible.
- `BLOCKING_DIVERGENCE`: a prerequisite conflicts with the slice and needs resolution.
- Existing test baseline and applicability: <evidence or reason not run>
- Unrelated local changes: <paths to preserve>

## 4) Implementation/conformance preparation

- Active TODO_TDD: <embed or reference the record owned by .agentic-work/.work-models/profiles/TDD.md>

| STEP | SLICE_CHANGE_OR_TEST_REFERENCE | REQUIRED_RESULT | FILES | VERIFY_BY |
| --- | --- | --- | --- | --- |
| 1 | <SX reference> | <result> | <paths> | <check> |

Sequence by slice dependencies; use lifecycle failure routing for any specification defect.

## 5) Verdict

- Blocking ambiguity: <items or none>
- Material assumptions and risks: <items>
- Specification readiness: `READY` | `BLOCKED`
- Specification acceptance: <accepted/pending/rejected and revision>
- Repository readiness: `MATCHES` | `IMPLEMENTABLE_DELTA` | `BLOCKING_DIVERGENCE`
- Applicable lifecycle gate: <satisfied/blocked and evidence>
- Overall verdict: `READY` | `BLOCKED`

Overall `READY` requires artifact and repository readiness plus the applicable lifecycle gate. Record the next authorized stage; readiness alone is not acceptance.
