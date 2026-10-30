# Spec Set Manifest: beta-integration

| FIELD | VALUE |
| --- | --- |
| project_slug | beta-integration |
| status | ready |
| generated_at | 2026-08-30T00:00:00Z |

| DOCUMENT_KEY | ROLE | PATH | STATUS | SOURCE_DOCS | UPSTREAM_DOCS | DOWNSTREAM_DOCS |
| --- | --- | --- | --- | --- | --- | --- |
| PB | non_normative_source | docs/PRODUCT_BRIEF.beta-integration.md | ready | (none) | (none) | (none) |
| PS | normative_origin | docs/PRODUCT_SPEC.beta-integration.md | ready | docs/PRODUCT_BRIEF.beta-integration.md | (none) | docs/DOMAIN_SPEC.beta-integration.md |
| DS | normative_derived | docs/DOMAIN_SPEC.beta-integration.md | ready | (none) | docs/PRODUCT_SPEC.beta-integration.md | docs/TECHNICAL_SPEC.beta-integration.md,docs/UI_SPEC.beta-integration.md,docs/SLICES_MAP.beta-integration.md,docs/slices/SLICE_001.beta-integration.md |
| TS | normative_derived | docs/TECHNICAL_SPEC.beta-integration.md | ready | (none) | docs/DOMAIN_SPEC.beta-integration.md | docs/UI_SPEC.beta-integration.md,docs/CODEBASE_SPEC.beta-integration.md,docs/SLICES_MAP.beta-integration.md,docs/slices/SLICE_001.beta-integration.md |
| UI | normative_derived | docs/UI_SPEC.beta-integration.md | ready | (none) | docs/DOMAIN_SPEC.beta-integration.md,docs/TECHNICAL_SPEC.beta-integration.md | docs/CODEBASE_SPEC.beta-integration.md,docs/SLICES_MAP.beta-integration.md,docs/slices/SLICE_001.beta-integration.md |
| CS | normative_derived | docs/CODEBASE_SPEC.beta-integration.md | ready | (none) | docs/TECHNICAL_SPEC.beta-integration.md,docs/UI_SPEC.beta-integration.md | docs/SLICES_MAP.beta-integration.md,docs/slices/SLICE_001.beta-integration.md |
| SM | normative_derived | docs/SLICES_MAP.beta-integration.md | ready | (none) | docs/DOMAIN_SPEC.beta-integration.md,docs/TECHNICAL_SPEC.beta-integration.md,docs/UI_SPEC.beta-integration.md,docs/CODEBASE_SPEC.beta-integration.md | docs/slices/SLICE_001.beta-integration.md |
| SX:001 | normative_derived | docs/slices/SLICE_001.beta-integration.md | ready | (none) | docs/SLICES_MAP.beta-integration.md,docs/DOMAIN_SPEC.beta-integration.md,docs/TECHNICAL_SPEC.beta-integration.md,docs/UI_SPEC.beta-integration.md,docs/CODEBASE_SPEC.beta-integration.md | (none) |
| MF | non_normative_manifest | docs/SPEC_FILES_INDEX.beta-integration.md | ready | (none) | (none) | (none) |
