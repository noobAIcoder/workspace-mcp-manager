# SM || Slices map

## SM-LNK || Record document links

| FIELD | VALUE |
| --- | --- |
| upstream_docs | docs/DOMAIN_SPEC.beta-integration.md,docs/TECHNICAL_SPEC.beta-integration.md,docs/UI_SPEC.beta-integration.md,docs/CODEBASE_SPEC.beta-integration.md |
| downstream_docs | docs/slices/SLICE_001.beta-integration.md |
| source_docs | (none) |
| update_triggers | Changes to acceptance, guardrails, migrations, UI, technical, or codebase boundaries |
| exit_artifacts | Slice catalog, dependencies, assignments, and coverage |

## SM-CAT || Catalog slices

### SM-CAT-CATRT || Slice catalog

| SLICE_ROW_KEY | SLICE_KEY | SLICE_PATH | SLICE_TITLE | SLICE_CLASS | ORDER_INDEX | SLICE_REQ_ID | ACCEPTANCE_ANCHOR_IDS | GUARDRAIL_IDS | MIGRATION_KEYS | TOUCHED_SCREENS | TOUCHED_COMPONENTS | EXIT_ARTIFACTS | NOTES |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SLI_001 | SLICE_001 | docs/slices/SLICE_001.beta-integration.md | Fixture slice 1 | PLATFORM | 1 | SM-CAT-SLIRQ-SLI_001 | DS-ACS-ACSRG-PROC_OK | DS-INV-INVRG-ORDER_INV | MIG_EVENT | (none) | (none) | behavior,tests | fixture slice |

### SM-CAT-SLIRQ || Slice requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| SM-CAT-SLIRQ-SLI_001 | The team MUST implement SLICE_001 as declared in the slice catalog. | slice_key:SLICE_001; order_index:1 | DS-ACS-ACSRG-PROC_OK,TS-ARC-ARCRG-LOCAL_ARCH |

## SM-DEP || Define dependencies

### SM-DEP-EDGRT || Dependency registry

| DEP_ROW_ID | FROM_SLICE_KEY | DEPENDS_ON_SLICE_KEY | DEP_KIND | RATIONALE |
| --- | --- | --- | --- | --- |

## SM-COV || Define coverage

### SM-COV-ACSCV || Acceptance coverage

| DS_ACCEPTANCE_ID | COVERED_BY_SLICE_KEYS | TEST_ANCHORS |
| --- | --- | --- |
| DS-ACS-ACSRG-PROC_OK | SLICE_001 | integration:TST_B001 |

### SM-COV-GRDCV || Guardrail coverage

| GUARDRAIL_ID | INTRODUCED_IN_SLICE | VALIDATED_IN_SLICES |
| --- | --- | --- |
| DS-INV-INVRG-ORDER_INV | SLICE_001 | SLICE_001 |
