# Work Models

Work Models defines agent workflow behavior independently of execution topology.

## Entry Points

| Need | Owner |
| --- | --- |
| Bootstrap, applicability, composition, authority map | [WORK_MODELS.md](WORK_MODELS.md) |
| Common authority and engineering policy | [WORK_INSTRUCTIONS.md](WORK_INSTRUCTIONS.md) |
| Per-instruction routing | [ROUTER.md](ROUTER.md), using [PRE_ROUTER.md](PRE_ROUTER.md) |
| Canonical development lifecycle and defect transitions | [flows/SDD_TDD.md](flows/SDD_TDD.md) |
| Specification delta, validation, acceptance | [profiles/SDD.md](profiles/SDD.md) |
| Tests-first work and conformance evidence | [profiles/TDD.md](profiles/TDD.md) |
| Operation-specific responsibilities | [operations/](operations/) |
| Operator-guided interaction | [profiles/OPERATOR_INSTRUCT.md](profiles/OPERATOR_INSTRUCT.md) |
| Dependency approval | [flows/DEPENDENCIES.md](flows/DEPENDENCIES.md) |
| Spec Set artifact integration | [frameworks/SPEC_SET.md](frameworks/SPEC_SET.md) |

The manifest selects required workflow modules; the router dispatches them.

Specification-only, no-change, evidence-reuse, and defect loop-back cases are defined by the lifecycle and its stage owners.

This README is descriptive navigation, not a separate workflow authority.
