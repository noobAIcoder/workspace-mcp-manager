# DS || Domain specification

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "DS",
  "purpose": "Translate Product Spec journeys, rules, constraints, and acceptance outcomes into explicit domain behavior, logical data semantics, and technology-neutral persistence requirements.",
  "authoritative_inputs": ["docs/PRODUCT_SPEC.{{project_slug}}.md"],
  "non_goals": [
    "physical storage technology, physical indexes, consistency mechanisms, or transaction implementation",
    "technical architecture and stack selection",
    "visual UI composition",
    "codebase layout",
    "slice composition"
  ],
  "readiness": [
    {"rule": "required_sections", "sections": ["DS-FLW", "DS-ERR", "DS-ENT", "DS-INV", "DS-ACS"]},
    {
      "rule": "required_requirement_groups",
      "sections": ["DS-FLW-FLWRG", "DS-ERR-ERRRG", "DS-ENT-ENTRG", "DS-INV-INVRG", "DS-ACS-ACSRG"],
      "min_rows": 1
    }
  ],
  "applicability": [
    {"section": "DS-SCR", "mode": "optional_omittable"},
    {"section": "DS-REL", "mode": "optional_omittable"},
    {"section": "DS-PER", "mode": "optional_omittable"},
    {"section": "DS-MIG", "mode": "optional_omittable"},
    {"section": "DS-GLY", "mode": "optional_omittable"}
  ],
  "typed_fields": [
    {"context": "DS-ENT-FLDRT", "field": "ENTITY_KEY", "kind": "scalar", "namespace": "entity_key", "empty": "forbidden"},
    {"context": "DS-ENT-STTRT", "field": "ENTITY_KEY", "kind": "scalar", "namespace": "entity_key", "empty": "forbidden"},
    {"context": "DS-REL-RELRT", "field": "FROM_ENTITY", "kind": "scalar", "namespace": "entity_key", "empty": "forbidden"},
    {"context": "DS-REL-RELRT", "field": "TO_ENTITY", "kind": "scalar", "namespace": "entity_key", "empty": "forbidden"},
    {"context": "DS-INV-INVRT", "field": "ERROR_CODE", "kind": "scalar", "namespace": "error_code", "empty": "forbidden"},
    {"context": "DS-PER-LOGRT", "field": "ENTITY_REQ_ID", "kind": "scalar", "namespace": "entity_requirement_id", "empty": "forbidden"},
    {"context": "DS-PER-LOGRT", "field": "SUPPORTING_FLOW_REQ_ID", "kind": "scalar", "namespace": "flow_requirement_id", "empty": "forbidden"},
    {"context": "DS-MIG-MIGRT", "field": "AFFECTED_ENTITY_REQ_IDS", "kind": "list", "namespace": "entity_requirement_id", "empty": "allowed"}
  ],
  "change_areas": ["flow", "error", "screen", "entity", "relation", "invariant", "persistence", "migration", "acceptance"],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "semantic_review": [
    "Every in-scope flow, error, domain screen purpose, entity, relationship, invariant, logical persistence requirement, migration, and acceptance scenario is concrete and traces to immediate Product Spec requirements.",
    "Optional sections are omitted when not applicable rather than retained as unresolved scaffold.",
    "Domain screen keys own stable screen identity, goals, data, actions, states, and navigation data without taking ownership of visual composition.",
    "Persistence content remains technology-neutral: physical storage products, physical indexes, consistency mechanisms, and transaction implementation belong to Technical Spec.",
    "Migration entries define domain semantics without assigning migrations to implementation slices; assignment belongs to Slices Map.",
    "The document does not introduce technical architecture, UI styling, code layout, or slicing policy."
  ]
}
specset-authoring:end -->

## DS-LNK || Record document mirrors

| FIELD | VALUE |
| --- | --- |
| project_slug | {{project_slug}} |
| document_key | DS |
| document_role | normative_derived |
| source_docs | (none) |
| upstream_docs | docs/PRODUCT_SPEC.{{project_slug}}.md |
| downstream_docs | docs/TECHNICAL_SPEC.{{project_slug}}.md,docs/UI_SPEC.{{project_slug}}.md,docs/SLICES_MAP.{{project_slug}}.md,docs/slices/SLICE_<NNN>.{{project_slug}}.md |

## DS-OVR || Define domain context

{{domain_context}}

## DS-FLW || Define system flows

### DS-FLW-FLWRT || Flow registry

| FLOW_KEY | TRIGGER | INITIAL_STATE | STEPS | TERMINAL_STATE | PRINCIPAL_FAILURE |
| --- | --- | --- | --- | --- | --- |
| FLOW_TBD | {{trigger}} | {{initial_state}} | {{ordered_steps}} | {{terminal_state}} | {{failure_and_recovery}} |

### DS-FLW-FLWRG || Flow requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-FLW-FLWRG-TBD_FLOW | The system MUST implement {{domain_flow}}. | flow_key:FLOW_TBD | PS-JRN-JRNRG-TBD_JOURNEY |

## DS-ERR || Define error semantics

### DS-ERR-ERRRT || Error registry

| ERROR_CODE | CATEGORY | TRIGGER | ROLLBACK | USER_RESULT | RECOVERY |
| --- | --- | --- | --- | --- | --- |
| ERR_TBD | {{error_category}} | {{failure_condition}} | {{rollback_scope}} | {{observable_result}} | {{recovery_action}} |

### DS-ERR-ERRRG || Error requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-ERR-ERRRG-TBD_ERROR | The system MUST surface {{failure_semantics}}. | error_code:ERR_TBD | PS-RUL-RULRG-TBD_RULE |

## DS-SCR || Define domain screen purposes

<!-- specset-authoring:guidance Omit this section when no interactive UI applies. Stable screen identity and behavior live here; visual composition belongs to UI Spec. -->

### DS-SCR-SCRRT || Screen registry

| SCREEN_KEY | USER_GOAL | DATA_QUERY | ACTIONS | EMPTY_STATE | POPULATED_STATE | ERROR_STATE | NAVIGATION_DATA |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SCR_TBD | {{user_goal}} | {{query}} | {{actions}} | {{empty_state}} | {{populated_state}} | {{error_state}} | {{data_carried}} |

### DS-SCR-SCRRG || Screen requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-SCR-SCRRG-TBD_SCREEN | The product UI MUST provide {{domain_screen_behavior}}. | screen_key:SCR_TBD | PS-JRN-JRNRG-TBD_JOURNEY |

## DS-ENT || Define entities

### DS-ENT-ENTRT || Entity registry

| ENTITY_KEY | CONCEPT | ID_FIELD | LIFECYCLE | DELETION_POLICY |
| --- | --- | --- | --- | --- |
| ENT_TBD | {{real_world_concept}} | {{identifier_field}} | {{states_and_transitions}} | {{deletion_policy}} |

### DS-ENT-FLDRT || Entity field registry

| ENTITY_KEY | FIELD_NAME | TYPE | MANDATORY | DEFAULT | VALID_VALUES | NOTES |
| --- | --- | --- | --- | --- | --- | --- |
| ENT_TBD | {{field_name}} | {{field_type}} | {{yes_or_no}} | {{default_or_none}} | {{constraints}} | {{notes}} |

### DS-ENT-STTRT || Entity state transition registry

| ENTITY_KEY | FROM_STATE | TO_STATE | TRIGGER | FORBIDDEN_WHEN |
| --- | --- | --- | --- | --- |
| ENT_TBD | {{from_state}} | {{to_state}} | {{trigger}} | {{forbidden_condition_or_none}} |

### DS-ENT-ENTRG || Entity requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-ENT-ENTRG-TBD_ENTITY | The data model MUST define {{entity_contract}}. | entity_key:ENT_TBD | PS-JRN-JRNRG-TBD_JOURNEY |

## DS-REL || Define relationships

### DS-REL-RELRT || Relationship registry

| RELATION_KEY | FROM_ENTITY | TO_ENTITY | CARDINALITY | OWNER_FIELD | NULLABLE | DELETE_BEHAVIOR |
| --- | --- | --- | --- | --- | --- | --- |
| REL_TBD | ENT_TBD | ENT_TBD | {{cardinality}} | {{owner_field}} | {{yes_or_no}} | {{delete_behavior}} |

### DS-REL-RELRG || Relationship requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-REL-RELRG-TBD_RELATE | The data model MUST preserve {{relationship_contract}}. | relation_key:REL_TBD | PS-RUL-RULRG-TBD_RULE |

## DS-INV || Define invariants

### DS-INV-INVRT || Invariant registry

| INVARIANT_KEY | SCOPE | CONSTRAINT | ENFORCEMENT_POINT | ERROR_CODE | BOUNDARY_CASES |
| --- | --- | --- | --- | --- | --- |
| INV_TBD | {{scope}} | {{always_true_constraint}} | {{domain_boundary}} | ERR_TBD | {{boundary_cases}} |

### DS-INV-INVRG || Invariant requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-INV-INVRG-TBD_INVAR | The system MUST preserve {{domain_invariant}}. | invariant_key:INV_TBD; error_code:ERR_TBD | PS-RUL-RULRG-TBD_RULE |

## DS-PER || Define logical persistence semantics

### DS-PER-LOGRT || Logical persistence registry

| ENTITY_REQ_ID | SUPPORTING_FLOW_REQ_ID | DURABILITY | LIFECYCLE_COUPLING | CONSISTENCY_EXPECTATION | NOTES |
| --- | --- | --- | --- | --- | --- |
| DS-ENT-ENTRG-TBD_ENTITY | DS-FLW-FLWRG-TBD_FLOW | {{durability_requirement}} | {{lifecycle_semantics}} | {{logical_consistency_expectation}} | {{notes}} |

## DS-MIG || Define migrations

### DS-MIG-MIGRT || Migration registry

| MIGRATION_KEY | AFFECTED_ENTITY_REQ_IDS | MIGRATION_KIND | ORDER_INDEX | NOTES |
| --- | --- | --- | --- | --- |
| MIG_TBD | DS-ENT-ENTRG-TBD_ENTITY | {{migration_kind}} | {{order_index}} | {{logical_schema_change}} |

## DS-ACS || Define acceptance scenarios

### DS-ACS-ACSRT || Acceptance scenario registry

| SCENARIO_KEY | GIVEN | WHEN | THEN | NEGATIVE_CASE | OBSERVATION_POINT |
| --- | --- | --- | --- | --- | --- |
| ACS_TBD | {{specific_precondition}} | {{specific_action}} | {{specific_result}} | {{failure_case}} | {{observation_point}} |

### DS-ACS-ACSRG || Acceptance requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-ACS-ACSRG-TBD_ACCEPT | The system MUST pass {{acceptance_scenario}}. | scenario_key:ACS_TBD | PS-ACS-ACSRG-TBD_ACCEPT |

## DS-GLY || Maintain glossary

| TERM | DEFINITION |
| --- | --- |
| {{term}} | {{definition}} |
