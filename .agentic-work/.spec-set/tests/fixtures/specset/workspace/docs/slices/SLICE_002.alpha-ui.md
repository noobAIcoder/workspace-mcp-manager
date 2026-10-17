# SX || Slice specification

## SX-LNK || Record document links

### SX-LNK-LNKRT || Link registry

| FIELD | VALUE |
| --- | --- |
| upstream_docs | docs/SLICES_MAP.alpha-ui.md,docs/DOMAIN_SPEC.alpha-ui.md,docs/TECHNICAL_SPEC.alpha-ui.md,docs/UI_SPEC.alpha-ui.md,docs/CODEBASE_SPEC.alpha-ui.md |
| downstream_docs | (none) |
| source_docs | (none) |
| update_triggers | Changes to the catalog or upstream project requirements |
| exit_artifacts | Self-contained implementation input |

## SX-INP || Record inputs

### SX-INP-INPRT || Input registry

| FIELD | VALUE |
| --- | --- |
| slice_key | SLICE_002 |
| slice_title | Fixture slice 2 |
| slice_class | PRODUCT |
| slice_map_requirement_id | SM-CAT-SLIRQ-SLI_002 |
| acceptance_anchor_ids | DS-ACS-ACSRG-NOTIF_OK |
| guardrail_ids | DS-INV-INVRG-NO_BODY |
| migration_keys | (none) |
| dependencies | SLICE_001 |
| touched_modules | MOD_APP,MOD_DAT,MOD_TST |
| touched_files | src/app/**,src/data/**,tests/** |
| touched_screens | SCR_HOME |
| touched_components | CMP_STATUS |

## SX-CTX || Inline project context

### SX-CTX-STACK || Stack and commands

Runtime: Python 3.13. `SETUP`, `TEST`, and `RUN` use the exact command registry from Technical Spec.

### SX-CTX-ERPAT || Error pattern

Errors use typed result codes at module boundaries and map to deterministic operator-facing outcomes.

### SX-CTX-TSARC || Test architecture

Tests use the standard library runner, isolated temporary state, and per-test fixtures.

### SX-CTX-MODLS || Module, dependency, and interface context

| MODULE_KEY | OWNED_PATHS | PUBLIC_ENTRYPOINTS | LAYER | RESPONSIBILITY |
| --- | --- | --- | --- | --- |
| MOD_APP | src/app/** | src/app/main.* | prod | application module |
| MOD_DAT | src/data/**,data/** | src/data/index.* | prod | data module |
| MOD_TST | tests/** | (none) | test | automated tests |

| FROM_MODULE | TO_MODULE | EDGE_RULE | RATIONALE |
| --- | --- | --- | --- |
| MOD_APP | MOD_DAT | ALLOW | application persists state |
| MOD_TST | MOD_APP | ALLOW | tests exercise app |
| MOD_TST | MOD_DAT | ALLOW | tests inspect state |

| INTERFACE_KEY | INTERFACE_KIND | DEFINITION_PATHS | OWNED_BY_MODULE | IMPLEMENTED_BY_MODULES | NOTES |
| --- | --- | --- | --- | --- | --- |
| IFACE_STATUS | internal | src/app/status_contract.* | MOD_APP | MOD_APP | fixture interface |

### SX-CTX-ENTTY || Entity schemas

`FixtureRecord`: `record_id` string required, `state` enum required, `created_at` timestamp required. Logical persistence follows Domain Spec. No migration is assigned to this slice.

### SX-CTX-INVAR || Invariants

The record state never exposes private body content; enforcement occurs in the domain layer and tests reject violating input.

### SX-CTX-PRESL || Prior slice and repository state

| FIELD | VALUE |
| --- | --- |
| repository_commit | 8c183ccaa9092aa1616e36dd48dc9be33775680f |
| evidence_source | synthetic fixture state representing the completed SLICE_001 prerequisite |
| observed_state | Storage and the core interface exist and baseline tests pass after SLICE_001. |

### SX-CTX-UISPC || UI specifications

Token, layout, `CMP_STATUS`, and `SCR_HOME` contracts are inlined: states include loading, empty, populated, and error; retry refreshes the status query.

## SX-SCP || Define scope

### SX-SCP-INSCP || In scope

- Implement the fixture behavior and automated evidence.

### SX-SCP-OUTSC || Out of scope

- Unrelated product expansion.

## SX-CHG || Register changes

### SX-CHG-CHGRT || Change registry

| CHANGE_KEY | CHANGE_KIND | SUMMARY | REFERENCES | DETAILS |
| --- | --- | --- | --- | --- |
| CHG_A002 | OTHER | Implement fixture behavior | SM-CAT-SLIRQ-SLI_002 | Create owned source and test files |

### SX-CHG-IFACE || Interface details

`process(record_id: str) -> Result[state, error]`; sample input `rec-001`; sample result `done`.

### SX-CHG-UICHG || UI changes

Create or update `SCR_HOME` and `CMP_STATUS` according to the inlined states.

## SX-SPC || Define behavior

### SX-SPC-GOAL || Behavioral goal

The operator observes one deterministic fixture outcome.

### SX-SPC-FLOW || Flow specification

1. Trigger processing for `rec-001`.
2. Validate the identifier and invariant.
3. Persist state `done`.
4. Return the observable result.
5. Return a typed error without partial state on failure.

### SX-SPC-EDGE || Edge cases

Reject an empty identifier, duplicate submission, missing prior state, and invariant violation without exposing private content.

## SX-RQS || Define slice requirements

### SX-RQS-RQSRG || Slice requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| SX-RQS-RQSRG-SLI_002_DELIV | The slice MUST deliver its acceptance anchor. | acceptance mapping | SM-CAT-SLIRQ-SLI_002 |
| SX-RQS-RQSRG-SLI_002_PRES_INV | The slice MUST preserve its declared guardrail. | negative test | DS-INV-INVRG-NO_BODY |
| SX-RQS-RQSRG-SLI_002_IMPL_CHG | The slice MUST implement its registered change. | change registry | SM-CAT-SLIRQ-SLI_002 |
| SX-RQS-RQSRG-SLI_002_MAP_TST | The slice MUST map the acceptance anchor to an automated test. | test mapping | TS-TST-TSTRG-TST_STRAT |
| SX-RQS-RQSRG-SLI_002_UI_STATE | The slice MUST implement all declared screen states. | UI state evidence | UI-SCR-SCRRG-ALL_STATES |

## SX-VER || Define verification

### SX-VER-TSTRT || Test registry

| TEST_ID | TEST_KIND | TEST_SCOPE | TEST_PATH | TAGS |
| --- | --- | --- | --- | --- |
| TST_A002 | integration | slice,domain,data | tests/test_fixture.py | fixture |

### SX-VER-ACMRT || Acceptance mapping

| DS_ANCHOR_ID | TEST_ID | TEST_KIND | TEST_DESCRIPTION |
| --- | --- | --- | --- |
| DS-ACS-ACSRG-NOTIF_OK | TST_A002 | integration | Process rec-001 and assert done state |

### SX-VER-TSDET || Test specifications

#### SX-VER-TSDET-T0001 || Test detail 0001

**Registry key:** `TST_A002`

**Preconditions:** isolated fixture storage after SLICE_001.

**Action:** process `rec-001`.

**Expected result:** state `done`, one persisted record, and no private body field.

**Teardown:** handled by temporary-state isolation.
