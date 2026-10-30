# DS || Domain specification

## DS-LNK || Record document links

| FIELD | VALUE |
| --- | --- |
| upstream_docs | docs/PRODUCT_SPEC.alpha-ui.md |
| downstream_docs | docs/TECHNICAL_SPEC.alpha-ui.md,docs/UI_SPEC.alpha-ui.md,docs/SLICES_MAP.alpha-ui.md,docs/slices/SLICE_001.alpha-ui.md,docs/slices/SLICE_002.alpha-ui.md |
| source_docs | (none) |
| update_triggers | Changes to product journeys, rules, constraints, or acceptance |
| exit_artifacts | Domain flows, data, errors, and acceptance |

## DS-FLW || Define domain flows

### DS-FLW-FLWRG || Flow requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-FLW-FLWRG-RUN_FLOW | The system MUST execute the primary domain flow. | flow contract | PS-JRN-JRNRG-MON_RUN |

## DS-ERR || Define error semantics

### DS-ERR-ERRRG || Error requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-ERR-ERRRG-OBS_LOST | The system MUST expose a recoverable error result for the primary failure. | error contract | PS-RUL-RULRG-NO_CONTENT |

## DS-SCR || Define screen semantics

### DS-SCR-SCRRT || Screen registry

| SCREEN_KEY | USER_GOAL | DATA_QUERY | ACTIONS | EMPTY_STATE | POPULATED_STATE | ERROR_STATE | NAVIGATION_DATA |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SCR_HOME | Observe fixture status | Load current fixture record | retry | no active record | current status | unavailable status | selected record ID |

### DS-SCR-SCRRG || Screen requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-SCR-SCRRG-STAT_VIEW | The UI MUST expose the fixture status screen. | screen_key:SCR_HOME | PS-JRN-JRNRG-MON_RUN |

## DS-ENT || Define entities

### DS-ENT-ENTRG || Entity requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-ENT-ENTRG-RUN_STATE | The data model MUST define the fixture record entity. | entity contract | PS-JRN-JRNRG-MON_RUN |

## DS-REL || Define relationships

### DS-REL-RELRG || Relationship requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-REL-RELRG-RUN_EVENT | The data model MUST define the fixture record relationship. | relationship contract | PS-JRN-JRNRG-MON_RUN |

## DS-INV || Define invariants

### DS-INV-INVRG || Invariant requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-INV-INVRG-NO_BODY | The data model MUST preserve the fixture invariant. | guardrail | PS-RUL-RULRG-NO_CONTENT |

## DS-PER || Define logical persistence semantics

### DS-PER-LOGRT || Logical persistence registry

| ENTITY_REQ_ID | SUPPORTING_FLOW_REQ_ID | DURABILITY | LIFECYCLE_COUPLING | CONSISTENCY_EXPECTATION | NOTES |
| --- | --- | --- | --- | --- | --- |
| DS-ENT-ENTRG-RUN_STATE | DS-FLW-FLWRG-RUN_FLOW | durable_across_run | follows_fixture_record_lifecycle | completed_writes_visible_to_subsequent_flow_steps | technology-neutral logical contract |

## DS-MIG || Define migrations

### DS-MIG-MIGRT || Migration registry

| MIGRATION_KEY | AFFECTED_ENTITY_REQ_IDS | MIGRATION_KIND | ORDER_INDEX | NOTES |
| --- | --- | --- | --- | --- |
| MIG_STATE | DS-ENT-ENTRG-RUN_STATE | create_storage | 1 | introduce logical persistence before dependent flows |

### DS-MIG-MIGRG || Migration requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-MIG-MIGRG-STATE_SEQ | Migrations MUST introduce fixture persistence semantics before dependent flows execute. | migration_key:MIG_STATE | PS-JRN-JRNRG-MON_RUN |

## DS-ACS || Define acceptance

### DS-ACS-ACSRG || Acceptance requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| DS-ACS-ACSRG-NOTIF_OK | The system MUST pass the concrete fixture acceptance scenario. | test anchor | PS-ACS-ACSRG-STAT_NOTICE |
