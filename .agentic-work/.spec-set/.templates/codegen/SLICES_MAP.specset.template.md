# SM || Slices map

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "SM",
  "purpose": "Select, sequence, and cover implementation slices while keeping dependency graph authority and file-level change detail in their single owning structures.",
  "authoritative_inputs": [
    "docs/DOMAIN_SPEC.{{project_slug}}.md",
    "docs/TECHNICAL_SPEC.{{project_slug}}.md",
    "docs/UI_SPEC.{{project_slug}}.md",
    "docs/CODEBASE_SPEC.{{project_slug}}.md"
  ],
  "non_goals": [
    "complete slice implementation context",
    "file-level change procedures",
    "redefinition of domain, technical, UI, or codebase contracts",
    "duplicating the dependency graph inside the slice catalog"
  ],
  "readiness": [
    {"rule": "required_sections", "sections": ["SM-CAT", "SM-DEP", "SM-COV"]},
    {
      "rule": "slice_catalog_complete",
      "catalog_context": "SM-CAT-CATRT",
      "catalog_fields": {
        "slice_key": "SLICE_KEY",
        "slice_path": "SLICE_PATH",
        "slice_requirement": "SLICE_REQ_ID",
        "order_index": "ORDER_INDEX",
        "acceptance_anchors": "ACCEPTANCE_ANCHOR_IDS",
        "guardrails": "GUARDRAIL_IDS",
        "migrations": "MIGRATION_KEYS",
        "screens": "TOUCHED_SCREENS",
        "components": "TOUCHED_COMPONENTS"
      },
      "required_catalog_roles": ["slice_key", "slice_path", "slice_requirement", "order_index", "acceptance_anchors", "guardrails", "migrations", "screens", "components"],
      "forbidden_catalog_fields": ["DEPENDENCY_KEYS"],
      "dependency_edges": {
        "context": "SM-DEP-EDGRT",
        "from_field": "FROM_SLICE_KEY",
        "to_field": "DEPENDS_ON_SLICE_KEY",
        "require_acyclic": true,
        "require_dependency_lower_order": true
      },
      "coverage": {
        "acceptance": {"context": "SM-COV-ACSCV", "key_field": "DS_ACCEPTANCE_ID", "slice_field": "COVERED_BY_SLICE_KEYS"},
        "guardrail": {"context": "SM-COV-GRDCV", "key_field": "GUARDRAIL_ID", "slice_field": "VALIDATED_IN_SLICES"},
        "screen": {"context": "SM-COV-SCRCV", "key_field": "SCREEN_KEY", "slice_field": "COVERED_BY_SLICE_KEYS"},
        "component": {"context": "SM-COV-CMPCV", "key_field": "COMPONENT_KEY", "slice_field": "USED_IN_SLICES"}
      },
      "ui_applicability_document_key": "UI",
      "requires_acceptance_coverage": true,
      "requires_guardrail_coverage": true,
      "requires_ui_coverage_when_applicable": true,
      "requires_migration_assignment_exactly_once": true
    }
  ],
  "applicability": [
    {"section": "SM-COV-SCRCV", "mode": "optional_omittable", "condition": "interactive UI applies"},
    {"section": "SM-COV-CMPCV", "mode": "optional_omittable", "condition": "interactive UI applies"},
    {"section": "SM-CFL", "mode": "optional_omittable"},
    {"section": "SM-GLY", "mode": "optional_omittable"}
  ],
  "typed_fields": [
    {"context": "SM-CAT-CATRT", "field": "SLICE_KEY", "kind": "scalar", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-CAT-CATRT", "field": "SLICE_PATH", "kind": "scalar", "namespace": "document_path", "empty": "forbidden"},
    {"context": "SM-CAT-CATRT", "field": "SLICE_REQ_ID", "kind": "scalar", "namespace": "requirement_id", "empty": "forbidden"},
    {"context": "SM-CAT-CATRT", "field": "ACCEPTANCE_ANCHOR_IDS", "kind": "list", "namespace": "acceptance_requirement_id", "empty": "forbidden"},
    {"context": "SM-CAT-CATRT", "field": "GUARDRAIL_IDS", "kind": "list", "namespace": "invariant_requirement_id", "empty": "forbidden"},
    {"context": "SM-CAT-CATRT", "field": "MIGRATION_KEYS", "kind": "list", "namespace": "migration_key", "empty": "allowed"},
    {"context": "SM-CAT-CATRT", "field": "TOUCHED_SCREENS", "kind": "list", "namespace": "screen_key", "empty": "allowed"},
    {"context": "SM-CAT-CATRT", "field": "TOUCHED_COMPONENTS", "kind": "list", "namespace": "component_key", "empty": "allowed"},
    {"context": "SM-DEP-EDGRT", "field": "FROM_SLICE_KEY", "kind": "scalar", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-DEP-EDGRT", "field": "DEPENDS_ON_SLICE_KEY", "kind": "scalar", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-COV-ACSCV", "field": "DS_ACCEPTANCE_ID", "kind": "scalar", "namespace": "acceptance_requirement_id", "empty": "forbidden"},
    {"context": "SM-COV-ACSCV", "field": "COVERED_BY_SLICE_KEYS", "kind": "list", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-COV-GRDCV", "field": "GUARDRAIL_ID", "kind": "scalar", "namespace": "invariant_requirement_id", "empty": "forbidden"},
    {"context": "SM-COV-GRDCV", "field": "INTRODUCED_IN_SLICE", "kind": "scalar", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-COV-GRDCV", "field": "VALIDATED_IN_SLICES", "kind": "list", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-COV-SCRCV", "field": "SCREEN_KEY", "kind": "scalar", "namespace": "screen_key", "empty": "forbidden"},
    {"context": "SM-COV-SCRCV", "field": "COVERED_BY_SLICE_KEYS", "kind": "list", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-COV-CMPCV", "field": "COMPONENT_KEY", "kind": "scalar", "namespace": "component_key", "empty": "forbidden"},
    {"context": "SM-COV-CMPCV", "field": "INTRODUCED_IN_SLICE", "kind": "scalar", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-COV-CMPCV", "field": "USED_IN_SLICES", "kind": "list", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SM-CFL-CFLRT", "field": "TOUCHING_SLICE_KEYS", "kind": "list", "namespace": "slice_key", "empty": "forbidden"}
  ],
  "change_areas": ["catalog", "dependency", "coverage", "conflict"],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "semantic_review": [
    "Every in-scope acceptance anchor and guardrail is covered by at least one slice and every slice dependency exists only in the dependency edge registry.",
    "Every in-scope Domain Spec migration key is assigned to exactly one slice; assignment does not redefine migration semantics.",
    "Coverage registries agree exactly with the slice catalog for covered/validated/used slice sets; introduction annotations add information without becoming a competing slice-membership source.",
    "UI screens and components are covered when UI is applicable; no-UI projects omit those coverage registries.",
    "Slice order is unique, dependency edges are acyclic and point to lower-order prerequisites, and declared overlaps have explicit resolutions.",
    "The map defines outcomes, ordering, migration assignment, coverage, and conflicts without duplicating complete implementation context or file-level change detail."
  ]
}
specset-authoring:end -->

## SM-LNK || Record document mirrors

| FIELD | VALUE |
| --- | --- |
| project_slug | {{project_slug}} |
| document_key | SM |
| document_role | normative_derived |
| source_docs | (none) |
| upstream_docs | docs/DOMAIN_SPEC.{{project_slug}}.md,docs/TECHNICAL_SPEC.{{project_slug}}.md,docs/UI_SPEC.{{project_slug}}.md,docs/CODEBASE_SPEC.{{project_slug}}.md |
| downstream_docs | docs/slices/SLICE_<NNN>.{{project_slug}}.md |

## SM-OVR || Define slicing intent

{{slicing_strategy}}

## SM-CAT || Catalog slices

### SM-CAT-CATRT || Slice catalog

| SLICE_ROW_KEY | SLICE_KEY | SLICE_PATH | SLICE_TITLE | SLICE_CLASS | ORDER_INDEX | SLICE_REQ_ID | ACCEPTANCE_ANCHOR_IDS | GUARDRAIL_IDS | MIGRATION_KEYS | TOUCHED_SCREENS | TOUCHED_COMPONENTS | EXIT_ARTIFACTS | NOTES |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SLI_TBD | SLICE_001 | docs/slices/SLICE_001.{{project_slug}}.md | {{slice_title}} | {{slice_class}} | 1 | SM-CAT-SLIRQ-TBD_SLICE | DS-ACS-ACSRG-TBD_ACCEPT | DS-INV-INVRG-TBD_INVAR | MIG_TBD | SCR_TBD | CMP_TBD | {{exit_artifacts}} | {{notes}} |

### SM-CAT-SLIRQ || Slice requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| SM-CAT-SLIRQ-TBD_SLICE | The project MUST deliver {{slice_outcome}} in the declared slice. | slice_key:SLICE_001; order_index:1 | DS-ACS-ACSRG-TBD_ACCEPT,TS-ARC-ARCRG-ARCH_TBD,UI-SCR-SCRRG-SCRN_TBD,CS-MOD-MODRG-MODL_TBD |

## SM-DEP || Define slice dependencies

### SM-DEP-EDGRT || Dependency edge registry

| DEPENDENCY_ROW_KEY | FROM_SLICE_KEY | DEPENDS_ON_SLICE_KEY | DEPENDENCY_KIND | RATIONALE |
| --- | --- | --- | --- | --- |
| DEP_TBD | {{dependent_slice}} | {{prerequisite_slice}} | {{dependency_kind}} | {{rationale}} |

## SM-COV || Define coverage mirrors and annotations

### SM-COV-ACSCV || Acceptance coverage registry

| DS_ACCEPTANCE_ID | COVERED_BY_SLICE_KEYS | TEST_ANCHORS |
| --- | --- | --- |
| DS-ACS-ACSRG-TBD_ACCEPT | SLICE_001 | {{test_anchor}} |

### SM-COV-GRDCV || Guardrail coverage registry

| GUARDRAIL_ID | INTRODUCED_IN_SLICE | VALIDATED_IN_SLICES |
| --- | --- | --- |
| DS-INV-INVRG-TBD_INVAR | SLICE_001 | SLICE_001 |

### SM-COV-SCRCV || Screen coverage registry

| SCREEN_KEY | SCREEN_NAME | COVERED_BY_SLICE_KEYS | STATES_DELIVERED | NOTES |
| --- | --- | --- | --- | --- |
| SCR_TBD | {{screen_name}} | SLICE_001 | {{states_delivered}} | {{notes}} |

### SM-COV-CMPCV || Component coverage registry

| COMPONENT_KEY | COMPONENT_NAME | INTRODUCED_IN_SLICE | USED_IN_SLICES |
| --- | --- | --- | --- |
| CMP_TBD | {{component_name}} | SLICE_001 | SLICE_001 |

## SM-CFL || Define cross-slice conflicts

### SM-CFL-CFLRT || Conflict registry

| OVERLAP_KEY | OVERLAP_TARGET | TOUCHING_SLICE_KEYS | OVERLAP_KIND | RESOLUTION |
| --- | --- | --- | --- | --- |
| OVL_TBD | {{overlap_target}} | {{slice_keys}} | {{overlap_kind}} | {{resolution}} |

## SM-GLY || Maintain glossary

| TERM | DEFINITION |
| --- | --- |
| slice_key | Internal stable key such as SLICE_001; materialized through the canonical filename function. |
| acceptance_anchor | Domain acceptance requirement that defines observable slice completion. |
| guardrail | Domain invariant that the slice introduces or preserves. |
| migration_key | Domain migration contract assigned by the map to exactly one implementation slice. |
