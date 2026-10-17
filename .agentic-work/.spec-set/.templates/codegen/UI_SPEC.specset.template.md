# UI || UI specification

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "UI",
  "purpose": "Define modality-neutral implementable visual and interaction contracts for Domain Spec screen identities using the Technical Spec stack, or normatively declare that no interactive UI applies.",
  "authoritative_inputs": [
    "docs/DOMAIN_SPEC.{{project_slug}}.md",
    "docs/TECHNICAL_SPEC.{{project_slug}}.md"
  ],
  "non_goals": [
    "domain behavior or screen identity ownership",
    "technical stack selection",
    "codebase module ownership",
    "slice sequencing",
    "assuming a web, desktop, mobile, or terminal modality unless upstream contracts require it"
  ],
  "readiness": [
    {
      "rule": "applicability_branch",
      "enforcement": "deterministic_structure",
      "template_mode": "both_branches_scaffold",
      "selector": {"not_applicable_requirement_group": "UI-OVR-NOUIR"},
      "applicable": {
        "required_sections": ["UI-DSN", "UI-LAY", "UI-CMP", "UI-SCR", "UI-NAV"],
        "required_requirement_groups": ["UI-DSN-DSNRG", "UI-LAY-LAYRG", "UI-CMP-CMPRG", "UI-SCR-SCRRG", "UI-NAV-NAVRG"]
      },
      "not_applicable": {
        "required_requirement_group": "UI-OVR-NOUIR",
        "min_rows": 1,
        "omit_sections": ["UI-DSN", "UI-LAY", "UI-CMP", "UI-SCR", "UI-NAV", "UI-GLY"]
      }
    },
    {
      "rule": "detail_id_sequence",
      "parent": "UI-CMP-CMPDT",
      "terminal_pattern": "C[0-9]{4}",
      "first": "C0001",
      "ordering": "strictly_increasing",
      "gaps": "allowed",
      "enforcement": "deterministic_current_document"
    },
    {
      "rule": "detail_id_sequence",
      "parent": "UI-SCR-SCRDT",
      "terminal_pattern": "S[0-9]{4}",
      "first": "S0001",
      "ordering": "strictly_increasing",
      "gaps": "allowed",
      "enforcement": "deterministic_current_document"
    }
  ],
  "applicability": [
    {"section": "UI-OVR", "mode": "required_with_not_applicable_branch", "not_applicable_requirement_group": "UI-OVR-NOUIR"},
    {"section": "UI-GLY", "mode": "optional_omittable", "condition": "interactive UI applies"}
  ],
  "typed_fields": [
    {"context": "UI-LAY-LAYRT", "field": "USED_BY_SCREENS", "kind": "list", "namespace": "screen_key", "empty": "allowed"},
    {"context": "UI-CMP-CMPRT", "field": "USED_BY_SCREENS", "kind": "list", "namespace": "screen_key", "empty": "allowed"},
    {"context": "UI-SCR-SCRRT", "field": "SCREEN_KEY", "kind": "scalar", "namespace": "screen_key", "empty": "forbidden"},
    {"context": "UI-SCR-SCRRT", "field": "LAYOUT_KEY", "kind": "scalar", "namespace": "layout_key", "empty": "forbidden"},
    {"context": "UI-SCR-SCRRT", "field": "DATA_SOURCES", "kind": "list", "namespace": "interface_key", "empty": "allowed"},
    {"context": "UI-SCR-SCRRT", "field": "DS_SCREEN_REF", "kind": "scalar", "namespace": "screen_requirement_id", "empty": "forbidden"},
    {"context": "UI-NAV-NAVRT", "field": "FROM_SCREEN", "kind": "scalar", "namespace": "screen_key", "empty": "forbidden"},
    {"context": "UI-NAV-NAVRT", "field": "TO_SCREEN", "kind": "scalar", "namespace": "screen_key", "empty": "forbidden"}
  ],
  "change_areas": ["token", "layout", "component", "screen", "navigation", "applicability"],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "semantic_review": [
    "When UI is applicable, project-authored tokens, layout conditions, components, screens, states, interactions, data sources, and navigation form a complete implementable contract for the selected modality without relying on web-specific defaults.",
    "When UI is not applicable, UI-OVR-NOUIR contains a project-specific normative declaration and UI-specific design sections are omitted.",
    "UI screen rows reuse Domain Spec screen keys and do not create competing screen identity.",
    "Component and screen detail identifiers are append-only across revisions; gaps are valid and identifiers are never reused.",
    "UI references remain consistent with Domain and Technical Spec contracts without redefining behavior or stack authority."
  ]
}
specset-authoring:end -->

## UI-LNK || Record document mirrors

| FIELD | VALUE |
| --- | --- |
| project_slug | {{project_slug}} |
| document_key | UI |
| document_role | normative_derived |
| source_docs | (none) |
| upstream_docs | docs/DOMAIN_SPEC.{{project_slug}}.md,docs/TECHNICAL_SPEC.{{project_slug}}.md |
| downstream_docs | docs/CODEBASE_SPEC.{{project_slug}}.md,docs/SLICES_MAP.{{project_slug}}.md,docs/slices/SLICE_<NNN>.{{project_slug}}.md |

## UI-OVR || Define UI applicability and context

{{ui_context_or_not_applicable_declaration}}

### UI-OVR-NOUIR || Declare no interactive UI

<!-- specset-authoring:guidance This requirement group is template scaffold for the no-UI branch. Remove it when interactive UI applies; when retained, omit UI-DSN through UI-NAV and UI-GLY. -->

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-OVR-NOUIR-NOUI_TBD | The implementation MUST NOT provide an interactive UI because {{no_ui_rationale}}. | applicability:no_ui | TS-ARC-ARCRG-ARCH_TBD |

## UI-DSN || Define design tokens

### UI-DSN-TKNRT || Design token registry

| TOKEN_KEY | TOKEN_KIND | VALUE | USAGE | NOTES |
| --- | --- | --- | --- | --- |
| TOK_TBD | {{token_kind}} | {{value}} | {{usage}} | {{notes}} |

### UI-DSN-DSNRG || Design requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-DSN-DSNRG-DSGN_TBD | The UI MUST apply {{design_system_constraint}}. | tokens:project_authored | DS-SCR-SCRRG-TBD_SCREEN,TS-STK-STKRG-STAK_TBD |

## UI-LAY || Define layouts

### UI-LAY-CNDRT || Layout condition registry

| CONDITION_KEY | CONDITION | NOTES |
| --- | --- | --- |
| COND_DEFAULT | {{viewport_window_terminal_or_device_condition}} | {{notes}} |

### UI-LAY-LAYRT || Layout registry

| LAYOUT_KEY | LAYOUT_NAME | STRUCTURE | USED_BY_SCREENS | NOTES |
| --- | --- | --- | --- | --- |
| LAY_TBD | {{layout_name}} | {{ascii_or_nesting_structure}} | SCR_TBD | {{notes}} |

### UI-LAY-LAYRG || Layout requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-LAY-LAYRG-LAYO_TBD | The UI MUST arrange {{layout_contract}} under the declared layout conditions. | layout_key:LAY_TBD | DS-SCR-SCRRG-TBD_SCREEN,TS-ARC-ARCRG-ARCH_TBD |

## UI-CMP || Define components

### UI-CMP-CMPRT || Component registry

| COMPONENT_KEY | COMPONENT_NAME | RESPONSIBILITY | STATES | USED_BY_SCREENS | NOTES |
| --- | --- | --- | --- | --- | --- |
| CMP_TBD | {{component_name}} | {{single_responsibility}} | {{supported_states}} | SCR_TBD | detail:UI-CMP-CMPDT-C0001 |

### UI-CMP-CMPDT || Component details

#### UI-CMP-CMPDT-C0001 || Component detail 0001

**Registry key:** `CMP_TBD`

**Inputs / props:**

| INPUT_NAME | TYPE | MANDATORY | DEFAULT | CONSTRAINTS | NOTES |
| --- | --- | --- | --- | --- | --- |
| {{input_name}} | {{type}} | {{yes_or_no}} | {{default_or_none}} | {{constraints}} | {{notes}} |

**Visual / interaction states:**

| STATE | TRIGGER_OR_CONDITION | USER_VISIBLE_RESULT |
| --- | --- | --- |
| {{state}} | {{trigger_or_condition}} | {{visual_result}} |

**Dimensions / layout behavior:** {{sizing_behavior}}

**Content overflow:** {{overflow_strategy}}

### UI-CMP-CMPRG || Component requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-CMP-CMPRG-COMP_TBD | The UI MUST provide {{component_behavior}}. | component_key:CMP_TBD | DS-SCR-SCRRG-TBD_SCREEN,TS-STK-STKRG-STAK_TBD |

## UI-SCR || Define screens

### UI-SCR-SCRRT || Screen registry

| SCREEN_KEY | SCREEN_NAME | ENTRYPOINT_OR_LOCATION | LAYOUT_KEY | DATA_SOURCES | DS_SCREEN_REF | NOTES |
| --- | --- | --- | --- | --- | --- | --- |
| SCR_TBD | {{screen_name}} | {{route_window_view_region_or_entrypoint}} | LAY_TBD | {{data_sources_or_none}} | DS-SCR-SCRRG-TBD_SCREEN | detail:UI-SCR-SCRDT-S0001 |

### UI-SCR-SCRDT || Screen details

#### UI-SCR-SCRDT-S0001 || Screen detail 0001

**Registry key:** `SCR_TBD`

**Component tree:**

```text
SCR_TBD
  {{component_tree}}
```

**Data requirements:**

| DATA_KEY | SOURCE | QUERY_OR_BINDING | LOAD_CONDITION | NOTES |
| --- | --- | --- | --- | --- |
| {{data_key}} | {{source}} | {{query_or_binding}} | {{load_condition}} | {{notes}} |

**Screen states:**

| STATE | CONDITION | USER_RESULT | SAMPLE_DATA |
| --- | --- | --- | --- |
| {{state}} | {{condition}} | {{exact_result}} | {{sample_data_or_none}} |

**Interactions:**

| ACTION | TRIGGER | IMMEDIATE_FEEDBACK | SUCCESS_RESULT | FAILURE_RESULT |
| --- | --- | --- | --- | --- |
| {{action}} | {{trigger}} | {{feedback}} | {{success}} | {{failure}} |

**Navigation / transition:**

| TRIGGER | TO_SCREEN | DATA_CARRIED |
| --- | --- | --- |
| {{trigger}} | {{screen_key_or_same}} | {{data_or_none}} |

### UI-SCR-SCRRG || Screen requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-SCR-SCRRG-SCRN_TBD | The UI MUST render {{screen_contract}}. | screen_key:SCR_TBD | DS-SCR-SCRRG-TBD_SCREEN,DS-FLW-FLWRG-TBD_FLOW |

## UI-NAV || Define navigation and transitions

### UI-NAV-NAVRT || Navigation edge registry

| NAVIGATION_KEY | FROM_SCREEN | TO_SCREEN | TRIGGER | DATA_CARRIED | NAVIGATION_KIND | NOTES |
| --- | --- | --- | --- | --- | --- | --- |
| NAV_TBD | SCR_TBD | SCR_TBD | {{trigger}} | {{data_or_none}} | {{navigation_kind}} | {{notes}} |

### UI-NAV-NAVRG || Navigation requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-NAV-NAVRG-NAVT_TBD | The UI MUST transition {{navigation_contract}}. | navigation_key:NAV_TBD | DS-FLW-FLWRG-TBD_FLOW |

## UI-GLY || Maintain glossary

| TERM | DEFINITION |
| --- | --- |
| {{term}} | {{definition}} |
