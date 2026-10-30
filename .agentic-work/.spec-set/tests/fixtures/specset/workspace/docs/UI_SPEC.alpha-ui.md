# UI || UI specification

## UI-LNK || Record document links

| FIELD | VALUE |
| --- | --- |
| upstream_docs | docs/DOMAIN_SPEC.alpha-ui.md,docs/TECHNICAL_SPEC.alpha-ui.md |
| downstream_docs | docs/CODEBASE_SPEC.alpha-ui.md,docs/SLICES_MAP.alpha-ui.md,docs/slices/SLICE_001.alpha-ui.md,docs/slices/SLICE_002.alpha-ui.md |
| source_docs | (none) |
| update_triggers | Changes to domain screens, flows, entities, stack, or architecture |
| exit_artifacts | UI design and interaction contract |

## UI-DSN || Define design tokens

### UI-DSN-DSNRG || Design requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-DSN-DSNRG-TOKEN_SET | The UI MUST use the declared fixture token set. | token set | DS-SCR-SCRRG-STAT_VIEW |

## UI-LAY || Define layouts

### UI-LAY-LAYRT || Layout registry

| LAYOUT_KEY | LAYOUT_NAME | STRUCTURE | USED_BY_SCREENS | NOTES |
| --- | --- | --- | --- | --- |
| LAY_STATUS | Status layout | shell -> main -> status card | SCR_HOME | fixture layout |

### UI-LAY-LAYRG || Layout requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-LAY-LAYRG-SHELL_DEF | The UI MUST use the declared status layout. | layout | DS-SCR-SCRRG-STAT_VIEW |

## UI-CMP || Register components

### UI-CMP-CMPRT || Component registry

| COMPONENT_KEY | COMPONENT_NAME | PROPS_SUMMARY | STATES | USED_BY_SCREENS | NOTES |
| --- | --- | --- | --- | --- | --- |
| CMP_STATUS | Status card | label:string,state:string | default,loading,error | SCR_HOME | fixture component |

### UI-CMP-CMPDT || Component details

#### UI-CMP-CMPDT-C0001 || Status card

| PROP_NAME | TYPE | REQUIRED | DEFAULT | CONSTRAINTS | NOTES |
| --- | --- | --- | --- | --- | --- |
| label | string | yes | (none) | 1-80 chars | display label |
| state | string | yes | working | working,done,error | status value |

| STATE | TRIGGER | VISUAL_DESCRIPTION |
| --- | --- | --- |
| default | data available | label and state text |
| loading | data pending | progress text |
| error | fetch failure | error text and retry action |

Dimensions: fluid width, maximum 480px.

Content overflow: wrap labels and truncate state detail after two lines.

### UI-CMP-CMPRG || Component requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-CMP-CMPRG-PROP_TYPED | Each component MUST expose typed props and explicit states. | component contract | DS-SCR-SCRRG-STAT_VIEW |

## UI-SCR || Define screens

### UI-SCR-SCRRT || Screen registry

| SCREEN_KEY | SCREEN_NAME | ROUTE_PATH | LAYOUT_KEY | DATA_SOURCES | DS_SCREEN_REF | NOTES |
| --- | --- | --- | --- | --- | --- | --- |
| SCR_HOME | Status home | / | LAY_STATUS | IFACE_STATUS | DS-SCR-SCRRG-STAT_VIEW | entry screen |

### UI-SCR-SCRDT || Screen details

#### UI-SCR-SCRDT-S0001 || Status home

Component tree: `SCR_HOME -> CMP_STATUS`.

| STATE | CONDITION | WHAT_USER_SEES | SAMPLE_DATA |
| --- | --- | --- | --- |
| loading | query pending | loading status | state:loading |
| empty | no active record | empty status | records:[] |
| populated | record available | status card | state:working |
| error | query failed | error status and retry | error:unavailable |

### UI-SCR-SCRRG || Screen requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-SCR-SCRRG-ALL_STATES | Each screen MUST implement loading, empty, populated, and error states. | screen states | DS-FLW-FLWRG-RUN_FLOW |

## UI-NAV || Define navigation

### UI-NAV-NAVRT || Navigation registry

| NAV_EDGE_ID | FROM_SCREEN | TO_SCREEN | TRIGGER | DATA_CARRIED | NAV_KIND | NOTES |
| --- | --- | --- | --- | --- | --- | --- |
| NAV_HOME | SCR_HOME | SCR_HOME | retry | (none) | contextual | retry refresh |

### UI-NAV-NAVRG || Navigation requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| UI-NAV-NAVRG-ENTRY_DEF | The UI MUST load the declared entry screen. | entry route | DS-SCR-SCRRG-STAT_VIEW |
