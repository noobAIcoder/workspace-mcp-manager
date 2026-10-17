# Spec Set Integration

## Applicability

Apply this module when the user or applicable project authority designates Spec Set, or the task creates, updates, interprets, validates, or implements Spec Set-governed artifacts.

## Artifact Authorities

Use the local [Spec Set agent contract](../../.spec-set/AGENTS.md) for artifact integration and service-record conventions. Resolve only the needed owners:

| Concern | Owner |
| --- | --- |
| Document topology, shared registries, propagation, manifest | [Spec Set index](../../.spec-set/.templates/codegen/SPEC_FILES_INDEX.specset.md) |
| Shared structural, identifier, and reference grammar | [ID ruleset](../../.spec-set/.templates/codegen/ID_RULESET.specset.md) |
| Document-local authoring, readiness, composition, semantic review | Owning template in [.spec-set/.templates/codegen/](../../.spec-set/.templates/codegen/) |
| Intended project behavior | Applicable accepted materialized project specifications |

## Integration Responsibilities

For specification creation or revision, obtain artifact shape, governed derivations, and validation obligations from those owners. For implementation, supply the accepted materialized artifact required by the selected operation.

A self-contained slice remains the sole project-specification input where its owning template requires that property; this does not remove active workflow obligations.

Use the Spec Set agent contract's service-record integration for active `TODO_SDD` and `TODO_TDD` data.

Generic development behavior remains with the active Work Models. This integration MUST NOT copy Spec Set's document graph, identifiers, readiness rules, or propagation matrices.
