# TS || Technical specification

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "TS",
  "purpose": "Select the smallest architecture and concrete technical contracts that satisfy Domain Spec behavior, invariants, and logical persistence semantics.",
  "authoritative_inputs": ["docs/DOMAIN_SPEC.{{project_slug}}.md"],
  "non_goals": [
    "domain behavior or logical persistence semantics",
    "visual UI composition",
    "codebase path and module ownership",
    "slice composition and sequencing",
    "framework authoring or Spec Set validation workflow"
  ],
  "readiness": [
    {"rule": "required_sections", "sections": ["TS-ARC", "TS-STK", "TS-STA", "TS-IFC", "TS-ERR", "TS-CMD", "TS-TST"]},
    {
      "rule": "required_requirement_groups",
      "sections": ["TS-ARC-ARCRG", "TS-STK-STKRG", "TS-STA-STARG", "TS-IFC-IFCRG", "TS-ERR-ERRRG", "TS-TST-TSTRG"],
      "min_rows": 1
    },
    {
      "rule": "command_prerequisite_dag",
      "context": "TS-CMD-STRRT",
      "command_field": "COMMAND_KEY",
      "prerequisites_field": "PREREQUISITES"
    }
  ],
  "applicability": [
    {"section": "TS-CFG", "mode": "optional_omittable"},
    {"section": "TS-GLY", "mode": "optional_omittable"}
  ],
  "typed_fields": [
    {"context": "TS-STK-LIBRT", "field": "STACK_KEY", "kind": "scalar", "namespace": "stack_key", "empty": "forbidden"},
    {"context": "TS-STA-IDXRT", "field": "SUPPORTING_FLOW_REQ_ID", "kind": "scalar", "namespace": "flow_requirement_id", "empty": "forbidden"},
    {"context": "TS-CMD-STRRT", "field": "PREREQUISITES", "kind": "list", "namespace": "command_key", "empty": "allowed"}
  ],
  "change_areas": ["architecture", "stack", "state", "interface", "error", "command", "configuration", "test"],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "semantic_review": [
    "Architecture decisions, pinned stack selections, physical state realization, interface contracts, error boundaries, exact project commands, configuration, and test strategy are concrete enough for downstream design and implementation planning.",
    "Technical choices are justified by Domain Spec requirements and do not introduce unsupported product behavior.",
    "Physical storage technology, indexes, consistency mechanisms, and transaction implementation realize rather than redefine Domain Spec logical persistence semantics.",
    "Commands and configuration describe executable project behavior rather than framework authoring or validator operations.",
    "The document does not take ownership of UI composition, codebase layout, or slice sequencing."
  ]
}
specset-authoring:end -->

## TS-LNK || Record document mirrors

| FIELD | VALUE |
| --- | --- |
| project_slug | {{project_slug}} |
| document_key | TS |
| document_role | normative_derived |
| source_docs | (none) |
| upstream_docs | docs/DOMAIN_SPEC.{{project_slug}}.md |
| downstream_docs | docs/UI_SPEC.{{project_slug}}.md,docs/CODEBASE_SPEC.{{project_slug}}.md,docs/SLICES_MAP.{{project_slug}}.md,docs/slices/SLICE_<NNN>.{{project_slug}}.md |

## TS-OVR || Define technical context

{{technical_context}}

## TS-ARC || Define architecture decisions

### TS-ARC-ADRRT || Architecture decision registry

| ADR_KEY | QUESTION | DECISION | ALTERNATIVES_REJECTED | RATIONALE | CHANGE_IMPACT | STATUS |
| --- | --- | --- | --- | --- | --- | --- |
| ADR_001 | {{decision_question}} | {{decision}} | {{rejected_options}} | {{project_specific_rationale}} | {{rewrite_scope}} | proposed |

### TS-ARC-ARCRG || Architecture requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-ARC-ARCRG-ARCH_TBD | The implementation MUST use {{architecture_contract}}. | adr_key:ADR_001 | DS-FLW-FLWRG-TBD_FLOW,DS-ENT-ENTRG-TBD_ENTITY |

## TS-STK || Define stack selections

### TS-STK-LANRT || Language and runtime registry

| STACK_KEY | TECHNOLOGY | VERSION_PIN | ROLE | RATIONALE |
| --- | --- | --- | --- | --- |
| LANG_PRIMARY | {{language}} | {{exact_version}} | primary_language | {{rationale}} |
| RUNTIME_PRIMARY | {{runtime}} | {{exact_version}} | primary_runtime | {{rationale}} |

### TS-STK-LIBRT || Library registry

| LIBRARY_KEY | LIBRARY | VERSION_PIN | PURPOSE | STACK_KEY |
| --- | --- | --- | --- | --- |
| LIB_TBD | {{library}} | {{exact_version}} | {{purpose}} | LANG_PRIMARY |

### TS-STK-STKRG || Stack requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-STK-STKRG-STAK_TBD | The implementation MUST use {{pinned_stack_constraint}}. | lockfile:{{lockfile_path}} | DS-FLW-FLWRG-TBD_FLOW |

## TS-STA || Define physical state realization

### TS-STA-STTRT || State realization registry

| STATE_KIND | STORAGE_TECHNOLOGY | STORAGE_TARGET | CONSISTENCY_MECHANISM | TRANSACTION_MECHANISM | RECOVERY |
| --- | --- | --- | --- | --- | --- |
| persistent | {{storage_technology}} | {{storage_target}} | {{consistency_mechanism}} | {{transaction_mechanism}} | {{recovery_behavior}} |

### TS-STA-IDXRT || Physical index registry

| INDEX_KEY | STORAGE_TARGET | INDEXED_FIELDS | INDEX_KIND | SUPPORTING_FLOW_REQ_ID |
| --- | --- | --- | --- | --- |
| IDX_TBD | {{storage_target}} | {{indexed_fields}} | {{index_kind}} | DS-FLW-FLWRG-TBD_FLOW |

### TS-STA-STARG || State requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-STA-STARG-STAT_TBD | The implementation MUST realize {{state_contract}} using the declared physical state strategy. | state_kind:persistent | DS-ENT-ENTRG-TBD_ENTITY,DS-INV-INVRG-TBD_INVAR |

## TS-IFC || Define interface contracts

### TS-IFC-IFCRT || Interface contract registry

| INTERFACE_KEY | INTERFACE_KIND | CONTRACT_FORMAT | REQUEST_OR_INPUT | RESPONSE_OR_OUTPUT | ERROR_SHAPE | VERSIONING |
| --- | --- | --- | --- | --- | --- | --- |
| IFACE_TBD | {{interface_kind}} | {{contract_format}} | {{concrete_input_shape}} | {{concrete_output_shape}} | {{error_shape}} | {{version_policy}} |

### TS-IFC-IFCRG || Interface requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-IFC-IFCRG-IFCE_TBD | The implementation MUST expose {{interface_contract}}. | interface_key:IFACE_TBD | DS-FLW-FLWRG-TBD_FLOW,DS-REL-RELRG-TBD_RELATE |

## TS-ERR || Define error handling

### TS-ERR-ERRRT || Error boundary registry

| BOUNDARY | INPUT_ERROR | DOMAIN_ERROR | SYSTEM_ERROR | TRANSLATION | USER_SURFACE |
| --- | --- | --- | --- | --- | --- |
| {{boundary}} | {{input_behavior}} | {{domain_behavior}} | {{system_behavior}} | {{translation_rule}} | {{observable_result}} |

### TS-ERR-ERRRG || Error handling requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-ERR-ERRRG-ERRH_TBD | The implementation MUST propagate {{error_pattern}}. | boundary:{{boundary}} | DS-ERR-ERRRG-TBD_ERROR |

## TS-CMD || Define project command strings

<!-- specset-authoring:guidance Commands are exact executable project commands. Escape literal pipe characters in table cells as \|. -->

### TS-CMD-STRRT || Command registry

| COMMAND_KEY | COMMAND_STRING | PREREQUISITES | SUCCESS_SIGNAL | NOTES |
| --- | --- | --- | --- | --- |
| SETUP | {{setup_command}} | (none) | exit_code:0 | bootstrap_environment |
| LINT | {{lint_command}} | SETUP | exit_code:0 | static_checks |
| TEST | {{test_command}} | SETUP | exit_code:0 | automated_tests |
| RUN | {{run_command}} | SETUP | {{run_ready_signal}} | local_execution |
| MIGRATE | {{migrate_command_or_none}} | SETUP | exit_code:0 | persistence_migrations |

## TS-CFG || Define configuration

### TS-CFG-VARRT || Configuration registry

| VARIABLE_KEY | VARIABLE_NAME | MANDATORY | DEFAULT_VALUE | SECRET | VALIDATION | NOTES |
| --- | --- | --- | --- | --- | --- | --- |
| CFG_TBD | {{variable_name}} | {{yes_or_no}} | {{default_or_none}} | {{yes_or_no}} | {{validation_rule}} | {{notes}} |

## TS-TST || Define test architecture

### TS-TST-TSTRT || Test strategy registry

| CONCERN | TEST_KIND | RUNNER | ASSERTION_LIBRARY | ISOLATION | FIXTURE_PATTERN | DATA_LIFECYCLE | COVERAGE_EVIDENCE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| domain_rules | unit | {{runner}} | {{assertion_library}} | {{isolation}} | {{fixture_pattern}} | {{setup_and_teardown}} | {{evidence}} |
| persisted_flows | integration | {{runner}} | {{assertion_library}} | {{isolation}} | {{fixture_pattern}} | {{setup_and_teardown}} | {{evidence}} |

### TS-TST-TSTRG || Test architecture requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-TST-TSTRG-TEST_TBD | The implementation MUST verify {{test_strategy_contract}}. | runner:{{runner}} | DS-ACS-ACSRG-TBD_ACCEPT,DS-INV-INVRG-TBD_INVAR |

## TS-GLY || Maintain glossary

| TERM | DEFINITION |
| --- | --- |
| {{term}} | {{definition}} |
