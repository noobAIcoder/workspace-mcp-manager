# Work Models

## Responsibility

This manifest owns generic Work Model applicability and composition. A project-designated execution topology consumes the resulting active obligations; Work Models do not select execution topology.

Each normative rule MUST have exactly one authoritative definition within its applicability scope. References and specializations MUST NOT redefine or alter the owner's semantics. A specialization MAY add a narrower compatible obligation only within its stated scope. Normative authority dependencies MUST be acyclic; runtime stage loops are permitted.

Registry and navigation entries locate owners. They do not make every listed file a normative dependency of the registry or of other listed files.

## Registry

Paths are relative to this directory. Core entries are bootstrap/navigation references.

| Key | Class | Owner |
| --- | --- | --- |
| BASELINE | Core | [BASELINE_INSTRUCTIONS.md](BASELINE_INSTRUCTIONS.md) |
| WORK | Core | [WORK_INSTRUCTIONS.md](WORK_INSTRUCTIONS.md) |
| PRE_ROUTER | Core | [PRE_ROUTER.md](PRE_ROUTER.md) |
| ROUTER | Core | [ROUTER.md](ROUTER.md) |
| SPEC_CREATE | Operation | [operations/SPEC_CREATE.md](operations/SPEC_CREATE.md) |
| SPEC_UPDATE | Operation | [operations/SPEC_UPDATE.md](operations/SPEC_UPDATE.md) |
| SPEC_IMPLEMENT_NEW | Operation | [operations/SPEC_IMPLEMENT_NEW.md](operations/SPEC_IMPLEMENT_NEW.md) |
| SPEC_IMPLEMENT_UPDATE | Operation | [operations/SPEC_IMPLEMENT_UPDATE.md](operations/SPEC_IMPLEMENT_UPDATE.md) |
| SDD | Methodology | [profiles/SDD.md](profiles/SDD.md) |
| TDD | Methodology | [profiles/TDD.md](profiles/TDD.md) |
| OPERATOR_INSTRUCT | Execution profile | [profiles/OPERATOR_INSTRUCT.md](profiles/OPERATOR_INSTRUCT.md) |
| SPEC_SET | Enclosed framework | [frameworks/SPEC_SET.md](frameworks/SPEC_SET.md) |
| SDD_TDD | Lifecycle flow | [flows/SDD_TDD.md](flows/SDD_TDD.md) |
| DEPENDENCIES | Conditional flow | [flows/DEPENDENCIES.md](flows/DEPENDENCIES.md) |

The dependency record template is [templates/AUTHORIZED_DEPENDENCIES.md](templates/AUTHORIZED_DEPENDENCIES.md); dependency approval semantics belong to `DEPENDENCIES`.

## Applicability and Composition

Select from the authorized outcome and material context, not terminology alone.

| Work class | Primary operation | Required development modules |
| --- | --- | --- |
| Create specification authority | SPEC_CREATE | SDD_TDD, SDD |
| Revise existing specification authority | SPEC_UPDATE | SDD_TDD, SDD |
| Implement new behavior | SPEC_IMPLEMENT_NEW | SDD_TDD, SDD, TDD |
| Change, fix, refactor, migrate, or assess existing implementation | SPEC_IMPLEMENT_UPDATE | SDD_TDD, SDD, TDD |
| Plan or review development without modification | Operation matching the subject, or none | SDD_TDD and relevant stages as analysis inputs only |
| Other work | Applicable registered operation, or none | Only modules with satisfied applicability |

For combined specification and implementation work, select the implementation operation; specification stages remain part of its lifecycle. Missing specifications MUST NOT prevent selection of the required lifecycle.

Explicit SDD use requires `SDD_TDD` and `SDD`; implementation or conformance work also requires `TDD`. Explicit TDD use requires all three. Methodology activation MUST NOT bypass composition closure.

An active route contains at most one primary operation and any applicable frameworks, methodologies, execution profiles, and flows. The remaining modules own their local applicability: `SPEC_SET`, `OPERATOR_INSTRUCT`, and `DEPENDENCIES`. Their selection composes with, rather than replaces, required development modules.

Read-only and validation-only authorization remains governed by the shared guidance designated in [Work Instructions → Governing Policy](WORK_INSTRUCTIONS.md#governing-policy); selecting an operation is not permission to modify its subject.

## Authority Map

| Rule family | Authoritative owner |
| --- | --- |
| SDD/TDD applicability and required composition | This manifest |
| Desirable end result, canonical lifecycle, cross-stage transitions and reassessment, conformance no-ops, failure routing, qualification | SDD_TDD |
| Specification comparison, TODO_SDD, candidates, validation versus acceptance, specification no-op, SDD completion | SDD |
| TODO_TDD, tests-first behavior, current evidence applicability, bounded RED exceptions, TDD completion | TDD |
| Creation, revision, new/existing implementation specializations | Corresponding operation |
| Routing decisions and dispatch | PRE_ROUTER and ROUTER, respectively |
| Common project authority context | WORK |
| Specification-framework artifact semantics | Selected enclosed framework's owning artifacts |

## Extension

A new module MUST have a distinct owner responsibility, be registered by class, and declare applicability either here for development composition or in its own local contract for an independent concern.

Enclosed frameworks retain their internal document graph, identifiers, templates, readiness, materialization, propagation, and validation semantics. Work Models MUST NOT create competing definitions of those artifact contracts.
