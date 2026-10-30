# SM || Slices map

## SM-LNK || Record document links

| FIELD | VALUE |
| --- | --- |
| upstream_docs | docs/DOMAIN_SPEC.alpha-ui.md,docs/TECHNICAL_SPEC.alpha-ui.md,docs/UI_SPEC.alpha-ui.md,docs/CODEBASE_SPEC.alpha-ui.md |
| downstream_docs | docs/slices/SLICE_001.alpha-ui.md,docs/slices/SLICE_002.alpha-ui.md |
| source_docs | (none) |
| update_triggers | Changes to acceptance, guardrails, migrations, UI, technical, or codebase boundaries |
| exit_artifacts | Slice catalog, dependencies, assignments, and coverage |

## SM-CAT || Catalog slices

### SM-CAT-CATRT || Slice catalog

| SLICE_ROW_KEY | SLICE_KEY | SLICE_PATH | SLICE_TITLE | SLICE_CLASS | ORDER_INDEX | SLICE_REQ_ID | ACCEPTANCE_ANCHOR_IDS | GUARDRAIL_IDS | MIGRATION_KEYS | TOUCHED_SCREENS | TOUCHED_COMPONENTS | EXIT_ARTIFACTS | NOTES |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SLI_001 | SLICE_001 | docs/slices/SLICE_001.alpha-ui.md | Fixture slice 1 | PLATFORM | 1 | SM-CAT-SLIRQ-SLI_001 | DS-ACS-ACSRG-NOTIF_OK | DS-INV-INVRG-NO_BODY | MIG_STATE | SCR_HOME | CMP_STATUS | behavior,tests | fixture slice |
| SLI_002 | SLICE_002 | docs/slices/SLICE_002.alpha-ui.md | Fixture slice 2 | PRODUCT | 2 | SM-CAT-SLIRQ-SLI_002 | DS-ACS-ACSRG-NOTIF_OK | DS-INV-INVRG-NO_BODY | (none) | SCR_HOME | CMP_STATUS | behavior,tests | fixture slice |

### SM-CAT-SLIRQ || Slice requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| SM-CAT-SLIRQ-SLI_001 | The team MUST implement SLICE_001 as declared in the slice catalog. | slice_key:SLICE_001; order_index:1 | DS-ACS-ACSRG-NOTIF_OK,TS-ARC-ARCRG-LOCAL_ARCH |
| SM-CAT-SLIRQ-SLI_002 | The team MUST implement SLICE_002 as declared in the slice catalog. | slice_key:SLICE_002; order_index:2 | DS-ACS-ACSRG-NOTIF_OK,TS-ARC-ARCRG-LOCAL_ARCH |

## SM-DEP || Define dependencies

### SM-DEP-EDGRT || Dependency registry

| DEP_ROW_ID | FROM_SLICE_KEY | DEPENDS_ON_SLICE_KEY | DEP_KIND | RATIONALE |
| --- | --- | --- | --- | --- |
| DEP_002 | SLICE_002 | SLICE_001 | HARD | ordered fixture dependency |

## SM-COV || Define coverage

### SM-COV-ACSCV || Acceptance coverage

| DS_ACCEPTANCE_ID | COVERED_BY_SLICE_KEYS | TEST_ANCHORS |
| --- | --- | --- |
| DS-ACS-ACSRG-NOTIF_OK | SLICE_001 | integration:TST_A001 |
| DS-ACS-ACSRG-NOTIF_OK | SLICE_002 | integration:TST_A002 |

### SM-COV-GRDCV || Guardrail coverage

| GUARDRAIL_ID | INTRODUCED_IN_SLICE | VALIDATED_IN_SLICES |
| --- | --- | --- |
| DS-INV-INVRG-NO_BODY | SLICE_001 | SLICE_001,SLICE_002 |

### SM-COV-SCRCV || Screen coverage

| SCREEN_KEY | SCREEN_NAME | COVERED_BY_SLICE_KEYS | STATES_DELIVERED | NOTES |
| --- | --- | --- | --- | --- |
| SCR_HOME | Status home | SLICE_001,SLICE_002 | loading,empty,populated,error | fixture coverage |

### SM-COV-CMPCV || Component coverage

| COMPONENT_KEY | COMPONENT_NAME | INTRODUCED_IN_SLICE | USED_IN_SLICES |
| --- | --- | --- | --- |
| CMP_STATUS | Status card | SLICE_001 | SLICE_001,SLICE_002 |
