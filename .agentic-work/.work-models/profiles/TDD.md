# Test-Driven Development

## Stage Contract

This profile owns `TODO_TDD`, tests-first development, evidence applicability, and TDD completion. Inputs are accepted authoritative specifications, current implementation, existing tests and qualification evidence, the environment, and the authorized work boundary.

Apply [Work Instructions](../WORK_INSTRUCTIONS.md) and the [SDD authority and acceptance contract](SDD.md#acceptance). This profile does not decide cross-stage transitions.

## TODO_TDD

Before affected implementation, record and validate:

| Field | Required content |
| --- | --- |
| Authority | Applicable accepted requirements and revision; changed versus unchanged requirements |
| Existing coverage | Tests and qualification evidence already covering each requirement |
| Applicability | Whether that evidence remains valid for current implementation, environment, and specification authority |
| Required work | New or changed tests, implementation/conformance work, and expected observable results |
| Regression boundary | Materially affected preserved behavior and compatibility coverage |
| RED exception | Concrete reason and affected scope, with reproducible qualification criteria and procedures where meaningful pre-implementation RED is unsuitable |
| Completion | Required passing evidence and unresolved blockers |

Review coverage, expected results, evidence applicability, and scope; correct defects before the record is used. The record MAY reside in a project service artifact without duplicating its authority.

## Tests-First Change

For a requirement needing implementation change where a meaningful test can precede it:

1. Add or update the smallest relevant test.
2. Observe its expected failure and establish that it expresses the accepted requirement rather than an unrelated fixture or environment problem.
3. Implement the required behavior.
4. Execute the applicable checks and inspect their evidence.
5. Refactor only when useful and authorized, preserving conformance.

For a defect, reproduce it with a meaningful regression test under the same rule. Tests contradicting changed accepted requirements MUST be corrected or removed rather than preserved as authoritative regressions.

When meaningful pre-implementation RED is unsuitable, state a concrete reason, bound the exception to the affected scope, and define reproducible qualification procedures and expected results before implementation. Execute applicable verification afterward; meaningful RED remains required elsewhere. An unavailable required execution mechanism is a capability block, not by itself a RED exception.

Low-value tests or artificial failures MUST NOT be created merely to satisfy workflow form. Already-conformant implementation uses applicable evidence without manufacturing a RED phase.

## Evidence

Existing tests or evidence MAY establish conformance only when their coverage, result, implementation revision, qualification conditions (including environment), and specification authority remain applicable and sufficient. Unknown or stale applicability requires verification, not an invented passing result.

Qualification criteria precede implementation; qualification evidence is collected during or after execution, or reused after its applicability is established.

Validation scope and repetition follow governing policy. Record failures and unavailable evidence with their actual scope; a command or planned check is not execution evidence.

## Completion

TDD is complete when required conformance work and relevant regression checks have sufficient passing evidence against the accepted requirements, with no unresolved introduced defect. Output the requirement-to-evidence result, implementation changes or no-op disposition, and any blocked evidence.

Cross-stage disposition of a failure or completion is supplied by the active lifecycle, not inferred independently here.
