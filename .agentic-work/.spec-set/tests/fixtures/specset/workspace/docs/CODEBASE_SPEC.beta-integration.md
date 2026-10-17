# CS || Codebase specification

## CS-LNK || Record document links

| FIELD | VALUE |
| --- | --- |
| upstream_docs | docs/TECHNICAL_SPEC.beta-integration.md,docs/UI_SPEC.beta-integration.md |
| downstream_docs | docs/SLICES_MAP.beta-integration.md,docs/slices/SLICE_001.beta-integration.md |
| source_docs | (none) |
| update_triggers | Changes to technical or UI architecture |
| exit_artifacts | Modules, interfaces, dependencies, and test layout |

## CS-LAY || Register roots

### CS-LAY-DIRRT || Directory registry

| DIRECTORY_KEY | PATH_ROOT | ROLE |
| --- | --- | --- |
| SRC | src/ | source root |
| TST | tests/ | test root |
| DAT | data/ | data root |

### CS-LAY-LAYRG || Layout requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-LAY-LAYRG-SRC_ROOTS | Source, tests, and data MUST remain under their registered roots. | layout guard | TS-ARC-ARCRG-LOCAL_ARCH |

## CS-MOD || Register modules

### CS-MOD-MODRT || Module registry

| MODULE_KEY | OWNED_PATHS | PUBLIC_ENTRYPOINTS | LAYER | RESPONSIBILITY |
| --- | --- | --- | --- | --- |
| MOD_APP | src/app/** | src/app/main.* | prod | application module |
| MOD_DAT | src/data/**,data/** | src/data/index.* | prod | data module |
| MOD_TST | tests/** | (none) | test | automated tests |

### CS-MOD-MODRG || Module requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-MOD-MODRG-MOD_BOUND | Each source path MUST have one module owner. | module ownership | TS-ARC-ARCRG-LOCAL_ARCH |

## CS-INT || Register interfaces

### CS-INT-IFRT || Interface registry

| INTERFACE_KEY | INTERFACE_KIND | DEFINITION_PATHS | OWNED_BY_MODULE | IMPLEMENTED_BY_MODULES | NOTES |
| --- | --- | --- | --- | --- | --- |
| IFACE_STATUS | internal | src/app/status_contract.* | MOD_APP | MOD_APP | fixture interface |

### CS-INT-INTRG || Interface requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-INT-INTRG-IFACE_DEF | Each interface MUST have one declared owner and definition path. | interface ownership | TS-IFC-IFCRG-EVENT_CNTR |

## CS-DEP || Register dependencies

### CS-DEP-DEPRT || Dependency registry

| FROM_MODULE | TO_MODULE | EDGE_RULE | RATIONALE |
| --- | --- | --- | --- |
| MOD_APP | MOD_DAT | ALLOW | application persists state |
| MOD_TST | MOD_APP | ALLOW | tests exercise app |

### CS-DEP-DEPRG || Dependency requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-DEP-DEPRG-EDGE_RULE | Unlisted cross-module dependencies MUST be denied. | allowlist | TS-ARC-ARCRG-LOCAL_ARCH |

## CS-TRC || Register trace

### CS-TRC-TRCRT || Trace registry

| SOURCE_REQUIREMENT_ID | TARGET_KIND | TARGET_KEY | COVERAGE_NOTE |
| --- | --- | --- | --- |
| TS-ARC-ARCRG-LOCAL_ARCH | module | MOD_APP | architecture target |
| TS-IFC-IFCRG-EVENT_CNTR | interface | IFACE_STATUS | interface target |
| TS-TST-TSTRG-TST_STRAT | module | MOD_TST | test target |
| UI-OVR-NOUIR-NO_UI_SCOPE | module | MOD_APP | no-ui target |

### CS-TRC-TRCRG || Trace requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-TRC-TRCRG-REQ_TRACE | Every in-scope upstream requirement MUST map to a codebase target. | trace completeness | TS-ARC-ARCRG-LOCAL_ARCH |

## CS-TST || Define test layout

### CS-TST-TSTRT || Test location registry

| TEST_DIRECTORY_KEY | PATH_GLOB | PURPOSE |
| --- | --- | --- |
| TST_FIX | tests/** | fixture tests |

### CS-TST-TSTRG || Test requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-TST-TSTRG-TEST_LAYOUT | Automated tests MUST remain isolated under the registered test root. | test layout | TS-TST-TSTRG-TST_STRAT |
