# New Implementation

## Contract

Specialize the [SDD/TDD lifecycle](../flows/SDD_TDD.md) for behavior not yet implemented under the requested contract. Inputs include requested scope, current repository/environment evidence, and any existing or missing specification authority.

## Responsibilities

Map accepted requirements to the new implementation surfaces and their integration boundaries. Include prerequisites and interaction with preserved existing behavior in the [TDD record](../profiles/TDD.md#todo_tdd).

When an enclosed specification framework is active, obtain implementation inputs and traceability from its owning artifact contracts. New implementation MUST NOT require reconstructing context that the selected framework requires the specification artifact to contain.

## Output and Exit

Produce the authorized implementation and conformance evidence. Specification readiness and acceptance, test sequencing, no-op disposition, defect loop-backs, and qualification follow the lifecycle and its stage owners without an independent operation-level gate.
