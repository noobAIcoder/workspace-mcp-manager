# SDD/TDD Lifecycle

## Contract and Terms

This flow owns desirable-end-result planning, stage transitions, reassessment, implementation/conformance no-ops, failure routing, and final qualification. It consumes [Work Instructions](../WORK_INSTRUCTIONS.md), the [SDD stage](../profiles/SDD.md), and the [TDD stage](../profiles/TDD.md).

**Governing requirements** are authoritative constraints the requested change must preserve. **Current affected specifications** are authority subject to authorized review or revision. A **desirable end result** is a validated planning interpretation, not parallel implementation authority.

A **material change** affects semantics, authority, scope, architecture, data, public contracts, compatibility, acceptance criteria, or externally observable behavior.

## Establish the Desirable End Result

Derive the intended end state from the authorized requested outcome and governing requirements. Define scope, constraints, failure conditions, evaluation criteria, and output contract.

Review that interpretation for completeness, coherence, contradiction, redundancy, scope, and authority. Correct material defects and re-review affected conclusions before specification comparison.

The desirable result and success criteria MUST conform to governing requirements. Material ambiguity or conflict MUST block affected progression until resolved within existing authority or through an authorized requirements change. Produced artifacts MUST NOT silently redefine the result.

## Stage Transitions

For each affected scope, apply this sequence. The coordinating agent assesses progression against the rules below; specification acceptance uses SDD's designated decision-maker.

| Transition | Decision rule and assessed evidence |
| --- | --- |
| Desirable result → specification comparison | Reviewed result and success criteria adequately represent the requested outcome and governing requirements |
| Specification delta → candidate validation | Reviewed `TODO_SDD` covers affected discrepancies and governed consequences under [Specification Comparison](../profiles/SDD.md#specification-comparison) |
| Validated or unchanged specifications → accepted authority | Representation of the result, artifact validation, and applicable acceptance satisfy [SDD Completion](../profiles/SDD.md#completion); unchanged content needs no artificial edit |
| Accepted authority → implementation/conformance work | SDD is complete and remaining work is authorized under Work Instructions; specification-only requests exit at their requested artifact state |
| Work remains → TDD execution | Reviewed [TODO_TDD](../profiles/TDD.md#todo_tdd) covers affected requirements, expected results, preserved behavior, and applicable evidence; apply TDD's tests-first rules |
| TDD complete or implementation already conformant → final qualification | Applicable evidence demonstrates achievement of the result and authoritative conformance under Final Qualification below |

The primary operation describes the requested deliverable; entering a stage does not replace it or broaden authorization. Read-only requests apply relevant analysis and verification within Work Instructions.

These assessments MUST NOT add operator approval requirements. Apply expressly required approvals, including [Dependency Change Flow](DEPENDENCIES.md#operator-gate) only within that flow's trigger and approval scope. Automated checks MUST NOT create approvals or replace required human approval unless its governing rule expressly permits substitution. Artifact readiness and validation supply evidence to these transitions, not overlapping workflow gates.

## Reassessment

A material change to an accepted artifact, governing requirements, or assumptions supporting acceptance MUST trigger reassessment of affected acceptance, revalidation of affected evidence, and suspension of dependent progression until applicable conditions are satisfied.

Reuse unaffected acceptance and evidence where their applicability remains established under [SDD Acceptance](../profiles/SDD.md#acceptance) and [TDD Evidence](../profiles/TDD.md#evidence). Reassessment MUST NOT restart unrelated stages.

## No-Op Handling

When current implementation already conforms and sufficient applicable evidence establishes it under TDD's evidence contract, implementation MUST NOT be modified solely to advance workflow state.

Missing or stale evidence creates conformance-verification work, not necessarily implementation work. An authorized run MAY establish conformance without code changes.

Unchanged specifications or skipped implementation do not remove applicable qualification obligations. When no implementation qualification applies, complete only the authorized artifact scope and report that boundary.

## Failure Routing

Classify the cause before selecting a correction; preserve uncertainty while unresolved.

| Cause | Disposition |
| --- | --- |
| Implementation defect | Remain in TDD against accepted requirements |
| Test defect | Correct the test against accepted specifications and resume TDD |
| Validation environment, fixture, or infrastructure defect | Correct only if authorized and in scope; otherwise block affected evidence without inferring specification or implementation failure |
| Material specification defect or ambiguity | Return affected scope to SDD; reconcile specifications, acceptance, and affected `TODO_TDD` before resuming TDD |

For independent authorized work during a block, apply the shared engineering guidance designated by Work Instructions → Governing Policy. A planning omission MUST be reconciled with governing requirements and accepted specifications before it can require implementation.

## Final Qualification

The coordinating agent MUST establish demonstrated achievement of the desirable result and conformance to accepted authoritative specifications, materially affected observable behavior, required compatibility behavior, and applicable acceptance criteria.

A discrepancy between the result, specifications, and qualification evidence MUST be resolved through the owning specification or governing-authority path; the planning interpretation MUST NOT override accepted requirements.

Use TDD's evidence contract for applicability and reuse. Record criteria, evidence, authority revision, results, and unresolved limitations distinctly. Apply governing completion policy only after required qualification is satisfied.
