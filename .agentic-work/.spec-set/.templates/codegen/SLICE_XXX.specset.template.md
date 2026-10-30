# SX || Slice specification

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "SX",
  "purpose": "Materialize one self-contained implementation slice with all upstream context required to implement and verify that slice.",
  "authoritative_inputs": [
    "the matching row and dependency edges in docs/SLICES_MAP.{{project_slug}}.md",
    "docs/DOMAIN_SPEC.{{project_slug}}.md",
    "docs/TECHNICAL_SPEC.{{project_slug}}.md",
    "docs/UI_SPEC.{{project_slug}}.md",
    "docs/CODEBASE_SPEC.{{project_slug}}.md",
    "materialized prerequisite slice specifications and revision-identified repository-state evidence when prior-slice state is required; these do not create normative ancestry"
  ],
  "non_goals": [
    "changing upstream project requirements",
    "introducing implementation work outside the selected slice",
    "requiring the implementation agent to reconstruct omitted upstream context"
  ],
  "readiness": [
    {
      "rule": "required_sections",
      "sections": [
        "SX-INP", "SX-INP-INPRT", "SX-CTX", "SX-CTX-STACK", "SX-CTX-ERPAT", "SX-CTX-TSARC", "SX-CTX-MODLS", "SX-CTX-ENTTY", "SX-CTX-INVAR", "SX-CTX-PRESL", "SX-CTX-UISPC", "SX-SCP", "SX-CHG", "SX-CHG-CHGRT", "SX-SPC", "SX-SPC-GOAL", "SX-SPC-FLOW", "SX-SPC-EDGE", "SX-RQS", "SX-VER", "SX-VER-TSTRT", "SX-VER-ACMRT", "SX-VER-TSDET"
      ]
    },
    {"rule": "required_requirement_groups", "sections": ["SX-RQS-RQSRG"], "min_rows": 1},
    {
      "rule": "required_registry_headers",
      "tables": [
        {"context": "SX-INP-INPRT", "headers": ["FIELD", "VALUE"]},
        {"context": "SX-CHG-CHGRT", "headers": ["CHANGE_KEY", "CHANGE_KIND", "SUMMARY", "REFERENCES", "DETAILS"]},
        {"context": "SX-VER-TSTRT", "headers": ["TEST_ID", "TEST_KIND", "TEST_SCOPE", "TEST_PATH", "TAGS"]},
        {"context": "SX-VER-ACMRT", "headers": ["DS_ANCHOR_ID", "TEST_ID", "TEST_KIND", "TEST_DESCRIPTION"]}
      ]
    },
    {
      "rule": "self_contained",
      "enforcement": "semantic",
      "implementation_input": "materialized_slice_only",
      "requires_complete_relevant_upstream_context": true,
      "forbids_summary_when_exact_contract_required": true
    },
    {
      "rule": "acceptance_mapping_complete",
      "input_field": "SX-INP-INPRT.acceptance_anchor_ids",
      "mapping_context": "SX-VER-ACMRT",
      "test_registry_context": "SX-VER-TSTRT",
      "test_registry_id_field": "TEST_ID",
      "test_detail_parent": "SX-VER-TSDET",
      "test_detail_registry_label": "Registry key",
      "requires_test_details": true
    },
    {
      "rule": "slice_dependency_inputs",
      "input_context": "SX-INP-INPRT",
      "slice_key_field": "slice_key",
      "dependencies_field": "dependencies",
      "map_document_key": "SM",
      "edge_context": "SM-DEP-EDGRT",
      "edge_from_field": "FROM_SLICE_KEY",
      "edge_to_field": "DEPENDS_ON_SLICE_KEY"
    },
    {
      "rule": "field_ownership",
      "scope_context": "SX-INP-INPRT",
      "scope_field": "touched_modules",
      "path_field": "touched_files",
      "owner_document_key": "CS",
      "owner_context": "CS-MOD-MODRT",
      "owner_key_field": "MODULE_KEY",
      "owner_path_field": "OWNED_PATHS",
      "containment": "path_language_subset_of_selected_owner_union",
      "unestablished": "invalid"
    }
  ],
  "applicability": [
    {"section": "SX-CHG-MIGRA", "mode": "optional_omittable"},
    {"section": "SX-CHG-IFACE", "mode": "optional_omittable"},
    {"section": "SX-CHG-UICHG", "mode": "optional_omittable", "condition": "interactive UI change applies"},
    {"section": "SX-GLY", "mode": "optional_omittable"}
  ],
  "typed_fields": [
    {"context": "SX-INP-INPRT", "field": "slice_key", "kind": "scalar", "namespace": "slice_key", "empty": "forbidden"},
    {"context": "SX-INP-INPRT", "field": "slice_map_requirement_id", "kind": "scalar", "namespace": "requirement_id", "empty": "forbidden"},
    {"context": "SX-INP-INPRT", "field": "acceptance_anchor_ids", "kind": "list", "namespace": "acceptance_requirement_id", "empty": "forbidden"},
    {"context": "SX-INP-INPRT", "field": "guardrail_ids", "kind": "list", "namespace": "invariant_requirement_id", "empty": "forbidden"},
    {"context": "SX-INP-INPRT", "field": "migration_keys", "kind": "list", "namespace": "migration_key", "empty": "allowed"},
    {"context": "SX-INP-INPRT", "field": "dependencies", "kind": "list", "namespace": "slice_key", "empty": "allowed"},
    {"context": "SX-INP-INPRT", "field": "touched_modules", "kind": "list", "namespace": "module_key", "empty": "forbidden"},
    {"context": "SX-INP-INPRT", "field": "touched_files", "kind": "list", "namespace": "path_glob", "empty": "forbidden"},
    {"context": "SX-INP-INPRT", "field": "touched_screens", "kind": "list", "namespace": "screen_key", "empty": "allowed"},
    {"context": "SX-INP-INPRT", "field": "touched_components", "kind": "list", "namespace": "component_key", "empty": "allowed"},
    {"context": "SX-CTX-MODLS", "field": "MODULE_KEY", "kind": "scalar", "namespace": "module_key", "empty": "forbidden"},
    {"context": "SX-CTX-MODLS", "field": "FROM_MODULE", "kind": "scalar", "namespace": "module_key", "empty": "forbidden"},
    {"context": "SX-CTX-MODLS", "field": "TO_MODULE", "kind": "scalar", "namespace": "module_key", "empty": "forbidden"},
    {"context": "SX-CTX-MODLS", "field": "INTERFACE_KEY", "kind": "scalar", "namespace": "interface_key", "empty": "forbidden"},
    {"context": "SX-CTX-MODLS", "field": "DEFINITION_PATHS", "kind": "list", "namespace": "path_glob", "empty": "forbidden"},
    {"context": "SX-CTX-MODLS", "field": "OWNED_BY_MODULE", "kind": "scalar", "namespace": "module_key", "empty": "forbidden"},
    {"context": "SX-CTX-MODLS", "field": "IMPLEMENTED_BY_MODULES", "kind": "list", "namespace": "module_key", "empty": "allowed"},
    {"context": "SX-CTX-ENTTY", "field": "ENTITY_KEY", "kind": "scalar", "namespace": "entity_key", "empty": "forbidden"},
    {"context": "SX-CTX-ENTTY", "field": "ENTITY_REQ_ID", "kind": "scalar", "namespace": "entity_requirement_id", "empty": "forbidden"},
    {"context": "SX-CTX-ENTTY", "field": "SUPPORTING_FLOW_REQ_ID", "kind": "scalar", "namespace": "flow_requirement_id", "empty": "forbidden"},
    {"context": "SX-CTX-ENTTY", "field": "MIGRATION_KEY", "kind": "scalar", "namespace": "migration_key", "empty": "forbidden"},
    {"context": "SX-CTX-ENTTY", "field": "AFFECTED_ENTITY_REQ_IDS", "kind": "list", "namespace": "entity_requirement_id", "empty": "forbidden"},
    {"context": "SX-CTX-INVAR", "field": "ERROR_CODE", "kind": "scalar", "namespace": "error_code", "empty": "forbidden"},
    {"context": "SX-CTX-UISPC", "field": "USED_BY_SCREENS", "kind": "list", "namespace": "screen_key", "empty": "allowed"},
    {"context": "SX-CTX-UISPC", "field": "COMPONENT_KEY", "kind": "scalar", "namespace": "component_key", "empty": "forbidden"},
    {"context": "SX-CTX-UISPC", "field": "SCREEN_KEY", "kind": "scalar", "namespace": "screen_key", "empty": "forbidden"},
    {"context": "SX-CTX-UISPC", "field": "LAYOUT_KEY", "kind": "scalar", "namespace": "layout_key", "empty": "forbidden"},
    {"context": "SX-CTX-UISPC", "field": "DATA_SOURCES", "kind": "list", "namespace": "interface_key", "empty": "allowed"},
    {"context": "SX-CTX-UISPC", "field": "DS_SCREEN_REF", "kind": "scalar", "namespace": "screen_requirement_id", "empty": "forbidden"},
    {"context": "SX-CTX-UISPC", "field": "FROM_SCREEN", "kind": "scalar", "namespace": "screen_key", "empty": "forbidden"},
    {"context": "SX-CTX-UISPC", "field": "TO_SCREEN", "kind": "scalar", "namespace": "screen_key", "empty": "forbidden"},
    {"context": "SX-CHG-CHGRT", "field": "REFERENCES", "kind": "list", "namespace": "reference_union", "empty": "forbidden"},
    {"context": "SX-CHG-MIGRA", "field": "MIGRATION_KEY", "kind": "scalar", "namespace": "migration_key", "empty": "allowed"},
    {"context": "SX-CHG-MIGRA", "field": "AFFECTED_ENTITY_REQ_IDS", "kind": "list", "namespace": "entity_requirement_id", "empty": "allowed"},
    {"context": "SX-CHG-IFACE", "field": "INTERFACE_KEY", "kind": "scalar", "namespace": "interface_key", "empty": "forbidden"},
    {"context": "SX-CHG-UICHG", "field": "SCREEN_KEY", "kind": "scalar", "namespace": "screen_key", "empty": "allowed"},
    {"context": "SX-CHG-UICHG", "field": "COMPONENTS_AFFECTED", "kind": "list", "namespace": "component_key", "empty": "allowed"},
    {"context": "SX-VER-TSTRT", "field": "TEST_PATH", "kind": "scalar", "namespace": "path_glob", "empty": "forbidden"},
    {"context": "SX-VER-ACMRT", "field": "DS_ANCHOR_ID", "kind": "scalar", "namespace": "acceptance_requirement_id", "empty": "forbidden"},
    {"context": "SX-VER-ACMRT", "field": "TEST_ID", "kind": "scalar", "namespace": "test_key", "empty": "forbidden"}
  ],
  "change_areas": [],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "composition": [
    {"step": 1, "source": "SM_catalog_row_and_dependency_edges", "target": "SX-INP", "completion_evidence": "concrete_identity_anchors_guardrails_migrations_dependencies_touch_sets"},
    {"step": 2, "source": "TS_stack_commands", "target": "SX-CTX-STACK", "completion_evidence": "complete_relevant_project_rows"},
    {"step": 3, "source": "TS_error_tests", "target": "SX-CTX-ERPAT,SX-CTX-TSARC", "completion_evidence": "chosen_patterns_and_boundaries"},
    {"step": 4, "source": "CS_modules_dependencies_interfaces", "target": "SX-CTX-MODLS", "completion_evidence": "touched_rows_and_edges_with_exact_values"},
    {"step": 5, "source": "DS_entities_relations_persistence_migrations", "target": "SX-CTX-ENTTY", "completion_evidence": "complete_relevant_logical_contracts"},
    {"step": 6, "source": "DS_invariants", "target": "SX-CTX-INVAR", "completion_evidence": "every_guardrail_copied"},
    {"step": 7, "source": "prior_slices_and_revision_identified_repository_state", "target": "SX-CTX-PRESL", "completion_evidence": "concrete_post_dependency_state_with_provenance"},
    {"step": 8, "source": "UI_contracts", "target": "SX-CTX-UISPC", "completion_evidence": "complete_relevant_contracts_or_no_ui_declaration"},
    {"step": 9, "source": "DS_and_SM_scope", "target": "SX-SCP,SX-CHG,SX-SPC", "completion_evidence": "concrete_scope_changes_behavior_errors"},
    {"step": 10, "source": "DS_acceptance_TS_tests", "target": "SX-VER", "completion_evidence": "every_anchor_mapped_to_complete_test"},
    {"step": 11, "source": "composed_slice", "target": "all_sections", "completion_evidence": "no_placeholders_and_self_containment_review"}
  ],
  "semantic_review": [
    "The materialized slice is sufficient as the sole project-specification input for implementation of this slice.",
    "Copied upstream context preserves exact field names, types, constraints, interface shapes, module ownership, dependency edges, and applicable UI state matrices where implementation depends on them.",
    "Assigned migration keys exactly match the Slices Map catalog and copied migration details preserve Domain Spec semantics without redefining assignment or order.",
    "Dependency inputs exactly match the Slices Map dependency edge registry for this slice.",
    "Prior-slice/repository state identifies the repository revision and evidence source it describes.",
    "Summaries are not substituted for exact upstream contracts when implementation depends on exact values or shapes.",
    "Scope, changes, behavior, edge cases, and verification are concrete and mutually consistent with the selected slice and upstream contracts.",
    "No implementation work or project requirement outside the selected slice is introduced."
  ]
}
specset-authoring:end -->

## SX-LNK || Record document mirrors

### SX-LNK-LNKRT || Link registry

| FIELD | VALUE |
| --- | --- |
| project_slug | {{project_slug}} |
| document_key | SX |
| document_role | normative_derived |
| source_docs | (none) |
| upstream_docs | docs/SLICES_MAP.{{project_slug}}.md,docs/DOMAIN_SPEC.{{project_slug}}.md,docs/TECHNICAL_SPEC.{{project_slug}}.md,docs/UI_SPEC.{{project_slug}}.md,docs/CODEBASE_SPEC.{{project_slug}}.md |
| downstream_docs | (none) |

## SX-INP || Record slice inputs

### SX-INP-INPRT || Input registry

| FIELD | VALUE |
| --- | --- |
| slice_key | SLICE_{{slice_ordinal}} |
| slice_title | {{slice_title}} |
| slice_class | {{slice_class}} |
| slice_map_requirement_id | SM-CAT-SLIRQ-SLI_{{slice_ordinal}} |
| acceptance_anchor_ids | DS-ACS-ACSRG-TBD_ACCEPT |
| guardrail_ids | DS-INV-INVRG-TBD_INVAR |
| migration_keys | {{migration_keys_or_none}} |
| dependencies | {{dependency_edge_sources_or_none}} |
| touched_modules | {{module_keys}} |
| touched_files | {{repo_relative_paths_or_globs}} |
| touched_screens | {{screen_keys_or_none}} |
| touched_components | {{component_keys_or_none}} |

## SX-CTX || Inline implementation context

### SX-CTX-STACK || Copy stack and commands

{{complete_relevant_stack_library_and_command_rows}}

### SX-CTX-ERPAT || Copy error handling pattern

{{exact_error_pattern_and_boundaries}}

### SX-CTX-TSARC || Copy test architecture

{{exact_runner_assertion_isolation_fixture_and_data_lifecycle}}

### SX-CTX-MODLS || Copy module, dependency, and interface context

| MODULE_KEY | OWNED_PATHS | PUBLIC_ENTRYPOINTS | LAYER | RESPONSIBILITY |
| --- | --- | --- | --- | --- |
| {{module_key}} | {{owned_paths}} | {{entrypoints}} | {{layer}} | {{responsibility}} |

| FROM_MODULE | TO_MODULE | EDGE_RULE | RATIONALE |
| --- | --- | --- | --- |
| {{from_module}} | {{to_module}} | {{edge_rule}} | {{rationale}} |

| INTERFACE_KEY | INTERFACE_KIND | DEFINITION_PATHS | OWNED_BY_MODULE | IMPLEMENTED_BY_MODULES | NOTES |
| --- | --- | --- | --- | --- | --- |
| {{interface_key}} | {{interface_kind}} | {{paths}} | {{owner_module}} | {{implementing_modules}} | {{notes}} |

### SX-CTX-ENTTY || Copy entity, logical persistence, and migration contracts

{{complete_entity_fields_relationships_state_transitions_logical_persistence_and_assigned_migration_contracts}}

### SX-CTX-INVAR || Copy guardrail invariants

{{complete_invariant_text_enforcement_and_error_semantics}}

### SX-CTX-PRESL || Record prior slice and repository state

| FIELD | VALUE |
| --- | --- |
| repository_commit | {{repository_commit_or_not_applicable}} |
| evidence_source | {{repository_state_evidence_source}} |
| observed_state | {{concrete_state_after_dependencies_or_first_slice_statement}} |

### SX-CTX-UISPC || Copy applicable UI contracts

{{complete_relevant_tokens_layouts_component_specs_screen_specs_navigation_or_no_ui_declaration}}

## SX-SCP || Define slice scope

### SX-SCP-INSCP || In-scope work

- {{in_scope_item}}

### SX-SCP-OUTSC || Out-of-scope work

- {{out_of_scope_item}}

## SX-CHG || Define changes

### SX-CHG-CHGRT || Change registry

| CHANGE_KEY | CHANGE_KIND | SUMMARY | REFERENCES | DETAILS |
| --- | --- | --- | --- | --- |
| CHG_001 | {{change_kind}} | {{summary}} | {{references}} | {{complete_change_detail}} |

### SX-CHG-MIGRA || Migration details

| MIGRATION_KEY | AFFECTED_ENTITY_REQ_IDS | MIGRATION_KIND | ORDER_INDEX | COMPLETE_SCHEMA_CHANGE |
| --- | --- | --- | --- | --- |
| {{migration_key_or_none}} | {{entity_requirement_ids_or_none}} | {{migration_kind_or_none}} | {{order_or_none}} | {{schema_change_or_none}} |

### SX-CHG-IFACE || Interface changes

| INTERFACE_KEY | CONTRACT_KIND | COMPLETE_CONTRACT | CONCRETE_EXAMPLE | ERROR_CASES |
| --- | --- | --- | --- | --- |
| {{interface_key}} | {{contract_kind}} | {{request_response_or_signature}} | {{sample_data}} | {{error_shapes}} |

### SX-CHG-UICHG || UI change registry

| UI_CHANGE_KEY | SCREEN_KEY | CHANGE_TYPE | DESCRIPTION | COMPONENTS_AFFECTED |
| --- | --- | --- | --- | --- |
| UIC_001 | {{screen_key_or_none}} | {{create_modify_or_none}} | {{delta_or_no_ui}} | {{component_keys_or_none}} |

## SX-SPC || Define slice behavior

### SX-SPC-GOAL || State behavioral goal

{{one_sentence_stakeholder_visible_behavior}}

### SX-SPC-FLOW || Specify behavior flow

{{ordered_trigger_computation_state_change_user_result_and_error_path_with_concrete_sample_data}}

### SX-SPC-EDGE || Define edge cases

{{empty_boundary_duplicate_missing_dependency_and_failure_cases_with_expected_results}}

## SX-RQS || Define slice project requirements

### SX-RQS-RQSRG || Slice requirement registry

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| SX-RQS-RQSRG-BEHV_TBD | The slice MUST deliver {{slice_specific_behavior}}. | verification:SX-VER-ACMRT | SM-CAT-SLIRQ-SLI_{{slice_ordinal}} |

## SX-VER || Define verification

### SX-VER-TSTRT || Test registry

| TEST_ID | TEST_KIND | TEST_SCOPE | TEST_PATH | TAGS |
| --- | --- | --- | --- | --- |
| TST_001 | {{test_kind}} | {{scope}} | {{test_path}} | {{tags}} |

### SX-VER-ACMRT || Acceptance mapping

| DS_ANCHOR_ID | TEST_ID | TEST_KIND | TEST_DESCRIPTION |
| --- | --- | --- | --- |
| DS-ACS-ACSRG-TBD_ACCEPT | TST_001 | {{test_kind}} | {{concrete_description}} |

### SX-VER-TSDET || Test details

#### SX-VER-TSDET-T0001 || Test detail 0001

**Registry key:** `TST_001`

**Preconditions:** {{exact_state_and_fixture_data}}

**Action:** {{exact_operation_and_payload}}

**Expected result:** {{observable_output_and_persisted_state}}

**Teardown:** {{cleanup_or_isolation_strategy}}

## SX-GLY || Maintain glossary

| TERM | DEFINITION |
| --- | --- |
| {{term}} | {{definition}} |
