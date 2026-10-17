# Spec Set Agent Contract

## Activation boundary

This contract integrates Spec Set artifacts only after selection through [Work Models' Spec Set integration](../.work-models/frameworks/SPEC_SET.md). That module owns applicability. Inspecting it or this entry point MUST NOT activate Spec Set independently.

Paths and Markdown links here are component-relative unless stated otherwise. Materialized project artifacts and service-record output paths are repository-root-relative. Repository-wide fact interpretation belongs to [Agentic Work integration](../AGENTS.md#repository-context), not Spec Set.

## Authority navigation

Resolve the relevant detailed owners without copying their normative semantics:

| Concern | Owner |
| --- | --- |
| Document graph, namespaces and registries, materialization relationships, propagation, project manifest, global validation routing | [.templates/codegen/SPEC_FILES_INDEX.specset.md](.templates/codegen/SPEC_FILES_INDEX.specset.md) |
| Shared structure, identifiers, references, path/list grammar, placeholders, table syntax | [.templates/codegen/ID_RULESET.specset.md](.templates/codegen/ID_RULESET.specset.md) |
| Document-local authoring, applicability, readiness, typed fields, composition, change areas, semantic review | Owning specification template in [.templates/codegen/](.templates/codegen/) |
| Intended project behavior | Applicable accepted materialized project specifications |

Work Models retain workflow progression and acceptance-gate ownership. Spec Set readiness or a validator result MUST NOT be treated as specification acceptance; use the selected Work Model's acceptance contract.

## Artifact resolution

Identify the active project and applicable materialized artifacts using the index's naming, document registry, manifest, and identity contracts. For initial materialization, derive project identity under those contracts; for existing artifacts, preserve their established identity. A generated manifest is a navigation mirror, not a new authority.

Resolve each applicable artifact to its owning template or authority in the index. Distinguish framework templates and authoring scaffolds from materialized specifications. Consumers MUST NOT treat scaffold values or template examples as accepted project requirements. Missing or conflicting specification inputs return to the active operation rather than being supplied from observed repository facts.

## Authoring and propagation

For the active specification operation, obtain document shape, authoring-region handling, identifiers, local applicability, and readiness from the relevant index, ruleset, and templates. Materialize under those owners; do not copy authoring-only material into project artifacts.

Determine affected downstream artifacts from the index's derivation relationships and the owning templates' change areas. Record each required revision, regeneration, or reviewed-unchanged disposition against the active specification work. Service records and validator code MUST NOT establish another graph or propagation policy.

## Implementation inputs

Supply the accepted materialized specification inputs required by the active implementation operation, with their identities and revisions. Honor template-defined slice self-containment: where required, the slice is the sole project-specification input, without removing active Work Model, integration, or execution obligations.

A missing, stale, or unaccepted required input MUST remain visible to the owning workflow. Neither a derived TODO nor an observed-fact shard may replace or broaden accepted specification authority.

## Service-record integration

Service families are artifact choices within the active Work Model, not workflow routes. Select only records required by the active task and existing authorization. All materialized service records use the repository-root-relative `.reports/` output locations declared by their templates; choosing a template does not itself authorize persistent record creation.

| Family | Templates under `.templates/service/` | Use |
| --- | --- | --- |
| GENERAL | `GENERAL_PRE.agent.template.md`, `GENERAL_TODO.template.md` | Ordinary task contract, plan, and evidence when a durable record applies |
| SPEC | `SPEC_PRE.template.md`, `SPEC_TODO.template.md` | Specification changes and governed propagation |
| SLICE | `SLICE_PRE.template.md`, `SLICE_TODO.template.md` | Work on one materialized slice |
| PATCH | `PATCH_TODO.template.md` | Explicitly requested work on already accepted semantics |
| GAP | `REPORT_GAP.template.md` | Informational audit |

Embed or reference the active `TODO_SDD` in specification record fields and `TODO_TDD` in implementation/conformance fields as applicable. Each stage record MUST have one authoritative location, with artifact identity/revision and references from dependent records. Dependent records MUST NOT create competing copies. Retain the distinction between candidate validation, acceptance, implementation, and qualification evidence.

Template verdicts, checklists, and record families MUST NOT create a second lifecycle, approval system, or completion gate. Their consequences come from the active Work Models.

## Artifact validation

Use [tools/specset_validate.py](tools/specset_validate.py) and applicable [tests](tests/) for the index-routed structural, reference, manifest, and deterministic readiness checks. Framework-root arguments refer to this component, not its enclosing installation; checks MUST remain independent of the parent directory name and of installed repository facts.

Apply each owning template's semantic-review criteria separately. Deterministic checks establish their declared mechanics, not semantic correctness, workflow acceptance, or runtime qualification. Review owning artifact contracts before their mirrors and consumers, and recheck affected dimensions when revisions invalidate prior evidence.

### Review classification and evidence

Preserve these independent classification axes in applicable service records and artifact reviews:

| Axis | Values |
| --- | --- |
| `semantic_impact` | `none`, `clarification`, `semantic_change` |
| `work_kind` | `defect_fix`, `simplification`, `refactor`, `behavior`, `performance`, `contract`, `infra_test`, `docs_only`, `migration` |
| `scope` | `local`, `cross_document`, `framework_wide` |

`none` preserves accepted semantics; `clarification` makes existing intent explicit; `semantic_change` changes normative intent. Stage consequences remain with Work Models.

| Severity | Artifact-review meaning |
| --- | --- |
| P0 | Immediate destructive, unsafe, or framework-unusable defect |
| P1 | Material contradiction or missing contract capable of producing incorrect specifications, plans, claims, or implementation |
| P2 | Bounded material completeness, maintainability, universality, evidence, or instruction-economy defect |
| P3 | Local clarity or style defect without material semantic or execution consequence |

Artifact-review dimensions are `authority`, `semantic_architecture`, `enforcement`, `completeness`, `instruction_economy`, and `behavioral_qualification`. In an applicable review record, identify the finding, severity/confidence, owner and location, evidence, consequence, classification, correction, and affected dimensions. Distinguish duplicate authority from required mirrors, references, examples, and stale copies.

Keep deterministic, semantic, integration, runtime/qualification, and manual evidence distinguishable, including unavailable checks and unresolved uncertainty. Review findings and evidence inform the active workflow; this contract does not define an independent acceptance verdict or require an extra maintenance artifact.
