# IX || Spec Set files index

## IX-OVR || Define canonical authority

This file is the canonical owner of Spec Set document-framework topology, active Spec Set file classes, naming and materialization relationships, cross-document graph semantics, shared reference namespaces, applicability vocabulary, project-manifest rules, and global validation routing.

Document-local readiness, applicability declarations, field bindings, change-area vocabulary, composition, and semantic authoring rules belong to the owning `*.specset.template.md`. Shared structural and identifier grammar belongs to `ID_RULESET.specset.md`. Agent policy, service workflows, and repository-fact templates are separate authorities outside this Spec Set file registry. Validator code and tests consume these authorities and do not create framework semantics.

Product Brief is non-normative source provenance. Product Spec is the first normative project artifact and the only Origin Document. `source_docs` remains distinct from normative ancestry.

## IX-NAM || Define naming and materialization

### IX-NAM-SLUG || Define project slug

Derive `<project_slug>` once using `^[a-z0-9]+(?:-[a-z0-9]+)*$`, with a maximum of 63 characters. Once materialized, the slug is immutable for that Spec Set.

### IX-NAM-FILE || Define file classes

| FILE_CLASS | PATH_PATTERN | ROLE | NOTES |
| --- | --- | --- | --- |
| specset_template | `<NAME>.specset.template.md` | active_template | codegen_relative |
| specset_authority | `<NAME>.specset.md` | active_authority | codegen_relative |
| generated_document | `docs/<NAME>.{project_slug}.md` | project_artifact | marker_omitted |
| generated_slice | `docs/slices/SLICE_<NNN>.{project_slug}.md` | project_slice | three_digit_ordinal |
| generated_manifest | `docs/SPEC_FILES_INDEX.{project_slug}.md` | project_manifest | mirror_only |
| historical | `migration/baseline/**` | evidence | excluded_from_active_validation |

Active Spec Set Markdown files in the codegen directory MUST use `.specset`. Generated project filenames MUST contain exactly one project slug and MUST NOT contain `.specset`.

`filename(slice_key, project_slug) = <slice_key>.<project_slug>.md`; `slice_key` is an internal registry key, not a filename.

### IX-NAM-PATH || Route path grammar

This index owns project path relationships and materialized path patterns. Repository-relative path grammar is owned by `RS-REF-PATH` in `ID_RULESET.specset.md`.

### IX-NAM-LIST || Route list grammar

This index owns which graph-derived link values are emitted. Typed-value, list, and `(none)` grammar is owned by `RS-REF-LIST` in `ID_RULESET.specset.md`.

## IX-FWK || Register active Spec Set files

### IX-FWK-FILRT || Spec Set file registry

| SPECSET_KEY | ACTIVE_PATH | CLASS | VALIDATION_PROFILE | STATUS |
| --- | --- | --- | --- | --- |
| IX | SPEC_FILES_INDEX.specset.md | specset_authority | framework_index | active |
| RS | ID_RULESET.specset.md | specset_authority | framework_rules | active |
| PB | PRODUCT_BRIEF.specset.template.md | specset_template | product_brief | active |
| PS | PRODUCT_SPEC.specset.template.md | specset_template | normative | active |
| DS | DOMAIN_SPEC.specset.template.md | specset_template | normative | active |
| TS | TECHNICAL_SPEC.specset.template.md | specset_template | normative | active |
| UI | UI_SPEC.specset.template.md | specset_template | normative | active |
| CS | CODEBASE_SPEC.specset.template.md | specset_template | normative | active |
| SM | SLICES_MAP.specset.template.md | specset_template | normative | active |
| SX | SLICE_XXX.specset.template.md | specset_template | normative | active |

Validator tooling and tests are consumers of this registry rather than registered Spec Set files.

## IX-DOC || Register materialized project documents

### IX-DOC-DOCRT || Document registry

| DOCUMENT_KEY | SPECSET_SOURCE | MATERIALIZED_PATH | ROLE | PROFILE | GENERATION_ORDER | APPLICABILITY |
| --- | --- | --- | --- | --- | --- | --- |
| PB | PRODUCT_BRIEF.specset.template.md | docs/PRODUCT_BRIEF.{project_slug}.md | non_normative_source | product_brief | 1 | required |
| PS | PRODUCT_SPEC.specset.template.md | docs/PRODUCT_SPEC.{project_slug}.md | normative_origin | normative | 2 | required |
| DS | DOMAIN_SPEC.specset.template.md | docs/DOMAIN_SPEC.{project_slug}.md | normative_derived | normative | 3 | required |
| TS | TECHNICAL_SPEC.specset.template.md | docs/TECHNICAL_SPEC.{project_slug}.md | normative_derived | normative | 4 | required |
| UI | UI_SPEC.specset.template.md | docs/UI_SPEC.{project_slug}.md | normative_derived | normative | 5 | required_with_not_applicable_mode |
| CS | CODEBASE_SPEC.specset.template.md | docs/CODEBASE_SPEC.{project_slug}.md | normative_derived | normative | 6 | required |
| SM | SLICES_MAP.specset.template.md | docs/SLICES_MAP.{project_slug}.md | normative_derived | normative | 7 | required |
| SX | SLICE_XXX.specset.template.md | docs/slices/SLICE_<NNN>.{project_slug}.md | normative_derived | normative | 8 | one_or_more |
| MF | (generated) | docs/SPEC_FILES_INDEX.{project_slug}.md | non_normative_manifest | project_manifest | 9 | required |

### IX-DOC-EDGRT || Document edge registry

| EDGE_ID | FROM_KEY | TO_KEY | EDGE_KIND | CHANGE_EFFECT | NOTES |
| --- | --- | --- | --- | --- | --- |
| EDG_001 | PB | PS | source_review | review_target | no_normative_ancestry |
| EDG_002 | PS | DS | normative | review_or_regenerate | first_normative_edge |
| EDG_003 | DS | TS | normative | review_or_regenerate | immediate_ancestry |
| EDG_004 | DS | UI | normative | review_or_regenerate | immediate_ancestry |
| EDG_005 | TS | UI | normative | review_or_regenerate | immediate_ancestry |
| EDG_006 | TS | CS | normative | review_or_regenerate | immediate_ancestry |
| EDG_007 | UI | CS | normative | review_or_regenerate | immediate_ancestry |
| EDG_008 | DS | SM | normative | review_or_regenerate | immediate_ancestry |
| EDG_009 | TS | SM | normative | review_or_regenerate | immediate_ancestry |
| EDG_010 | UI | SM | normative | review_or_regenerate | immediate_ancestry |
| EDG_011 | CS | SM | normative | review_or_regenerate | immediate_ancestry |
| EDG_012 | SM | SX | normative | recompose_affected_slices | immediate_ancestry |
| EDG_013 | DS | SX | normative | recompose_affected_slices | slice_upstream_expansion |
| EDG_014 | TS | SX | normative | recompose_affected_slices | slice_upstream_expansion |
| EDG_015 | UI | SX | normative | recompose_affected_slices | slice_upstream_expansion |
| EDG_016 | CS | SX | normative | recompose_affected_slices | slice_upstream_expansion |
| EDG_017 | IX | MF | mirror | rematerialize_manifest | generated_mirror |

### IX-DOC-LINK || Derive document links

For a materialized project document:

- `source_docs` is the ordered set of concrete materialized `FROM_KEY` paths on inbound `source_review` edges.
- `upstream_docs` is the ordered set of concrete materialized `FROM_KEY` paths on inbound `normative` edges.
- `downstream_docs` is the ordered set of concrete materialized `TO_KEY` paths on outbound `normative` edges.
- Edge order in `IX-DOC-EDGRT` defines mirror order.
- Repeated SX edges MUST expand to every concrete materialized slice path for the active project.
- Empty sets materialize as `(none)`.

Template link tables MAY use the registered repeated-document path pattern for SX because concrete slice instances do not yet exist. Materialized link fields MUST NOT contain `<NNN>` or any other document-class pattern.

Link tables and generated-manifest links are mirrors of this derivation and MUST NOT create graph authority. Artifact identity is the actual repository path constrained by `IX-DOC-DOCRT`; materialized documents MUST NOT duplicate their own path in a self-identity field.

## IX-AUT || Define template-local authoring contracts

### IX-AUT-SCHE || Define authoring contract schema

Each active `*.specset.template.md` MUST contain exactly one authoring-only region delimited by:

```text
<!-- specset-authoring:start
{...}
specset-authoring:end -->
```

The region body MUST be one JSON object with `schema_version` equal to `1` and these required fields:

| FIELD | VALUE_KIND | PURPOSE |
| --- | --- | --- |
| document_key | scalar | owning document class |
| purpose | scalar | document responsibility |
| authoritative_inputs | list | allowed project inputs |
| non_goals | list | concerns owned elsewhere |
| readiness | list | deterministic or explicitly semantic local completion rules |
| applicability | list | conditional-section applicability declarations |
| typed_fields | list | local field-to-namespace bindings |
| change_areas | list | local changes that can trigger downstream review |
| materialization | object | authoring-only stripping and placeholder handling |
| semantic_review | list | non-mechanical completeness criteria |

A template MAY add machine-readable document-local fields such as `composition` or `typed_discriminators`. Such extensions remain authoritative only for that template.

For schema version `1`, a readiness entry is deterministic unless it sets `enforcement` to `semantic`. A deterministic readiness rule MUST be mechanically enforced from its declared rule metadata; an unsupported deterministic rule MUST be reported as a deterministic validation issue rather than silently ignored.

Each `typed_fields` entry MUST declare `context`, `field`, `kind`, `namespace`, and `empty`. `namespace` MUST exist in `IX-REF-NSPRT`. A typed binding constrains matching field occurrences; required structural presence belongs in deterministic readiness metadata.

### IX-AUT-APPL || Define applicability vocabulary

A section whose presence is not unconditional MUST be declared in the owning template's `applicability` list using one of these modes:

- `required` — the section is always present;
- `optional_omittable` — the section MAY be omitted when genuinely not applicable and MUST contain no unresolved scaffold when present;
- `required_with_not_applicable_branch` — the concern is always resolved, either through its applicable structure or a declared normative not-applicable branch.

An applicability entry MUST identify `section` and `mode`. It MAY declare a `condition` or `not_applicable_requirement_group` when needed. Applicability metadata MUST NOT create project behavior; it only governs document structure.

### IX-AUT-CMNT || Define authoring comments

Section-local authoring guidance MAY use comments beginning with `<!-- specset-authoring:` when the guidance does not duplicate machine-readable contract data. These comments are authoring-only and do not create separate authority.

### IX-AUT-MATL || Materialize templates

Template mode permits authoring regions, declared authoring comments, and placeholder Requirement Items under `RS-TMP-PLCH`. Materialization strips the complete authoring region and every `specset-authoring:` comment before producing project content.

Materialized mode MUST reject surviving framework-authoring markers, unresolved placeholders, placeholder Requirement Items, and document-class path patterns. Ordinary project HTML comments are not framework-authoring comments.

Template registry tables are authoring scaffolds unless structural presence or exact headers are declared by deterministic readiness metadata. Materialized documents MAY re-author non-governed scaffolding while preserving project semantics. Typed-field validation applies whenever a declared field occurs.

## IX-CHG || Derive downstream review

A document-local change triggers propagation only when its change class is declared in that document template's `change_areas`.

For a normative document, required downstream review is the deduplicated transitive closure of outgoing `normative` edges in `IX-DOC-EDGRT`; each traversed edge's `CHANGE_EFFECT` defines the required action. For Product Brief, an in-scope local change follows the direct `source_review` edge to Product Spec only; further normative propagation occurs only if Product Spec changes during that review.

Changes to this index, the ruleset, agent policy, service workflows, or template contracts require framework validation and review of affected consumers. No second per-document propagation-target registry is authoritative.

## IX-REF || Register shared reference namespaces

### IX-REF-NSPRT || Namespace registry

`DECLARATION` uses one of these forms: `generated_paths`, `requirements:*`, `requirements:<prefix>`, `registry:<DOCUMENT_KEY>:<SECTION_ID>:<FIELD>`, `union:<namespace>,...`, or `syntax:path_glob`.

| NAMESPACE | VALUE_FORM | OWNER_LOCATION | DECLARATION | NOTES |
| --- | --- | --- | --- | --- |
| document_path | normalized_repo_relative_path | materialized_project | generated_paths | concrete_generated_paths_only |
| requirement_id | normative_requirement_identifier | requirements_tables | requirements:* | immediate_ancestry_where_applicable |
| entity_requirement_id | DS_ENT_requirement_identifier | DOMAIN_SPEC | requirements:DS-ENT- | entity_target_only |
| flow_requirement_id | DS_FLW_requirement_identifier | DOMAIN_SPEC | requirements:DS-FLW- | flow_target_only |
| screen_requirement_id | DS_SCR_requirement_identifier | DOMAIN_SPEC | requirements:DS-SCR- | domain_screen_requirement_only |
| acceptance_requirement_id | DS_ACS_requirement_identifier | DOMAIN_SPEC | requirements:DS-ACS- | acceptance_target_only |
| invariant_requirement_id | DS_INV_requirement_identifier | DOMAIN_SPEC | requirements:DS-INV- | invariant_target_only |
| entity_key | ENT_registry_key | DS-ENT-ENTRT | registry:DS:DS-ENT-ENTRT:ENTITY_KEY | domain_entity |
| error_code | ERR_registry_key | DS-ERR-ERRRT | registry:DS:DS-ERR-ERRRT:ERROR_CODE | domain_error |
| layout_key | LAY_registry_key | UI-LAY-LAYRT | registry:UI:UI-LAY-LAYRT:LAYOUT_KEY | ui_layout |
| directory_key | DIR_registry_key | CS-LAY-DIRRT | registry:CS:CS-LAY-DIRRT:DIRECTORY_KEY | code_directory |
| module_key | MOD_registry_key | CS-MOD-MODRT | registry:CS:CS-MOD-MODRT:MODULE_KEY | code_module |
| interface_key | IFACE_registry_key | TS-IFC-IFCRT | registry:TS:TS-IFC-IFCRT:INTERFACE_KEY | technical_interface |
| screen_key | SCR_registry_key | DS-SCR-SCRRT | registry:DS:DS-SCR-SCRRT:SCREEN_KEY | domain_owned_screen_identity_reused_by_UI |
| component_key | CMP_registry_key | UI-CMP-CMPRT | registry:UI:UI-CMP-CMPRT:COMPONENT_KEY | ui_component |
| slice_key | SLICE_three_digit_key | SM-CAT-CATRT | registry:SM:SM-CAT-CATRT:SLICE_KEY | internal_key_not_filename |
| migration_key | MIG_registry_key | DS-MIG-MIGRT | registry:DS:DS-MIG-MIGRT:MIGRATION_KEY | schema_migration |
| command_key | command_registry_key | TS-CMD-STRRT | registry:TS:TS-CMD-STRRT:COMMAND_KEY | project_command |
| stack_key | stack_registry_key | TS-STK-LANRT | registry:TS:TS-STK-LANRT:STACK_KEY | language_or_runtime_selection |
| test_key | TST_registry_key | SX-VER-TSTRT | registry:SX:SX-VER-TSTRT:TEST_ID | slice_test |
| change_key | CHG_registry_key | SX-CHG-CHGRT | registry:SX:SX-CHG-CHGRT:CHANGE_KEY | slice_change |
| reference_union | requirement_or_registered_key | project_namespaces | union:requirement_id,entity_key,error_code,layout_key,directory_key,module_key,interface_key,screen_key,component_key,slice_key,migration_key,command_key,stack_key,test_key,change_key | polymorphic_reference_set |
| path_glob | normalized_path_or_glob | project_paths | syntax:path_glob | repository_path_or_glob |

### IX-REF-GLBRT || Global typed field bindings

Only bindings shared by all normative document classes are registered globally here. Document-local bindings belong in owning template contracts. Every normative document MUST contain `source_docs`, `upstream_docs`, and `downstream_docs` exactly once.

| FIELD_CONTEXT | FIELD_NAME | VALUE_KIND | TARGET_NAMESPACE | EMPTY_SENTINEL | NOTES |
| --- | --- | --- | --- | --- | --- |
| document_link | source_docs | list | document_path | allowed | non_normative_provenance |
| document_link | upstream_docs | list | document_path | allowed | normative_mirror |
| document_link | downstream_docs | list | document_path | allowed | normative_mirror |
| requirement_row | DERIVED_FROM | list_or_origin | requirement_id | forbidden | immediate_ancestry_only |

## IX-MAN || Define generated project manifest

The generated manifest is non-normative. It lists materialized files and mirrors registry- and graph-derived links from this index; it cannot create requirements or graph authority.

### IX-MAN-FLDRT || Manifest field registry

`MF-PRJ` materializes as a `FIELD | VALUE` registry using exact lower-case field names. Each required `MF-PRJ` field MUST occur exactly once. `MF-FIL` materializes as one pipe table whose header names are uppercase forms of the declared fields and MUST contain exactly one row per materialized artifact. `DOCUMENT_KEY` and `PATH` MUST each be unique across artifact rows.

A non-repeated artifact uses its registered document key. A concrete repeated Slice uses `SX:<NNN>`, where `<NNN>` is the three-digit ordinal in its concrete `SLICE_<NNN>` materialized path. Repeated-artifact identity is derived from that path ordinal and MUST NOT depend on slice count, enumeration position, or the presence of other slices.

| SECTION | REQUIRED_FIELDS | NOTES |
| --- | --- | --- |
| MF-PRJ | project_slug,status,generated_at | project_identity |
| MF-FIL | document_key,role,path,status,source_docs,upstream_docs,downstream_docs | one_row_per_artifact |

`status` MUST match `^[a-z][a-z0-9_]*$`. `generated_at` MUST be a real RFC 3339 UTC calendar timestamp in `YYYY-MM-DDTHH:MM:SSZ` form. Link emptiness uses `(none)` under `RS-REF-LIST`.

## IX-VAL || Define validation routing

### IX-VAL-PRFRT || Validation profile registry

| PROFILE | MODES | TARGETS | PRIMARY_CHECKS |
| --- | --- | --- | --- |
| framework_index | framework | SPEC_FILES_INDEX.specset.md | registry_graph_namespace_contract_and_authority_layout |
| framework_rules | framework | ID_RULESET.specset.md | shared_structure_identifier_reference_and_mode_rules |
| product_brief | template,materialized | PRODUCT_BRIEF.specset.template.md_and_artifacts | local_contract_heading_non_normativity_applicability_and_placeholders |
| normative | template,materialized | PS,DS,TS,UI,CS,SM,SX | local_contract_structure_ids_ancestry_references_mirrors_readiness_applicability |
| project_manifest | materialized | generated_manifest | non_normativity_grammar_and_mirror_agreement |

Deterministic validation consumes rules declared by this index, the ruleset, and template-local contracts. Unsupported deterministic readiness rules MUST fail closed. Semantic-review criteria remain separate and MUST NOT be reported as mechanically validated unless a semantic review mechanism actually ran.
