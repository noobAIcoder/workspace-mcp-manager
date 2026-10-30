# Existing Implementation and Conformance

## Contract

Specialize the [SDD/TDD lifecycle](../flows/SDD_TDD.md) for changes to, repair of, or conformance assessment of existing implementation. Inputs include current and applicable prior specification revisions, implementation, tests, evidence, and compatibility boundaries.

This specialization includes unchanged-specification defect fixes, refactors, and validation-only conformance work; the requested authorization still controls permitted actions.

## Responsibilities

Distinguish changed requirements, preserved requirements, already-conformant behavior, nonconformance, obsolete test expectations, and affected compatibility boundaries.

The semantic delta and demonstrated nonconformance MUST determine implementation scope, not textual diff size. Carry that distinction into the [TDD record](../profiles/TDD.md#todo_tdd).

Use selected framework artifact relationships to identify affected implementation authority and required traceability.

## Output and Exit

Report the reconciled requirement-to-evidence result and material preserved behavior. Apply the lifecycle's specification gate, no-op paths, failure routing, and qualification rather than defining separate operation-level transitions.
