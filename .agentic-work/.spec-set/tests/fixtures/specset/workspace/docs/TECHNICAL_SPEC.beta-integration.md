# TS || Technical specification

## TS-LNK || Record document links

| FIELD | VALUE |
| --- | --- |
| upstream_docs | docs/DOMAIN_SPEC.beta-integration.md |
| downstream_docs | docs/UI_SPEC.beta-integration.md,docs/CODEBASE_SPEC.beta-integration.md,docs/SLICES_MAP.beta-integration.md,docs/slices/SLICE_001.beta-integration.md |
| source_docs | (none) |
| update_triggers | Changes to domain flows, entities, invariants, errors, or screens |
| exit_artifacts | Architecture, stack, interfaces, commands, and tests |

## TS-ARC || Define architecture

### TS-ARC-ARCRG || Architecture requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-ARC-ARCRG-LOCAL_ARCH | The implementation MUST use the fixture architecture boundary. | fixture technical decision | DS-FLW-FLWRG-EVENT_FLOW |

## TS-STK || Define stack

### TS-STK-STKRG || Stack requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-STK-STKRG-PIN_STACK | The implementation MUST pin the fixture runtime and dependencies. | fixture technical decision | DS-FLW-FLWRG-EVENT_FLOW |

## TS-STA || Define state

### TS-STA-STARG || State requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-STA-STARG-PERS_STATE | The implementation MUST persist fixture state deterministically. | fixture technical decision | DS-ENT-ENTRG-EVENT_REC |

## TS-IFC || Define interfaces

### TS-IFC-IFCRT || Interface registry

| INTERFACE_KEY | INTERFACE_KIND | CONTRACT_FORMAT | REQUEST_OR_INPUT | RESPONSE_OR_OUTPUT | ERROR_SHAPE | VERSIONING |
| --- | --- | --- | --- | --- | --- | --- |
| IFACE_STATUS | internal | typed_result | event_id:string | state:string | typed_error | stable |

### TS-IFC-IFCRG || Interface requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-IFC-IFCRG-EVENT_CNTR | The implementation MUST define the fixture interface contract. | interface_key:IFACE_STATUS | DS-FLW-FLWRG-EVENT_FLOW |

## TS-ERR || Define error handling

### TS-ERR-ERRRG || Error requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-ERR-ERRRG-ERR_BOUND | The implementation MUST translate fixture failures at module boundaries. | fixture technical decision | DS-ERR-ERRRG-QUEUE_ERR |

## TS-CMD || Define commands

### TS-CMD-STRRT || Command registry

| COMMAND_KEY | COMMAND_STRING | PREREQUISITES | SUCCESS_SIGNAL | NOTES |
| --- | --- | --- | --- | --- |
| SETUP | python -m pip install -e . | (none) | exit_code:0 | fixture setup |
| TEST | python -m unittest | SETUP | exit_code:0 | fixture tests |
| RUN | python app.py | SETUP | exit_code:0 | fixture run |

### TS-CMD-CMDRG || Command requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-CMD-CMDRG-RUN_CMD | The project MUST define runnable setup, test, and run commands. | fixture technical decision | DS-ACS-ACSRG-PROC_OK |

## TS-TST || Define tests

### TS-TST-TSTRG || Test requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| TS-TST-TSTRG-TST_STRAT | The implementation MUST use isolated automated fixture tests. | fixture technical decision | DS-ACS-ACSRG-PROC_OK |
