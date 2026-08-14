# PM3 — Operator UX replatform

Status: **NORMATIVE IMPLEMENTATION SPECIFICATION**

PM3 replaces the PM2 curses presentation with a Textual frontend while
preserving `workspace-mcp-manager` as the sole lifecycle/control-plane
authority.

```text
Overview -> Instance -> Task
```

## Authority

The TUI MUST use public manager operations for authoritative reads and every
mutation. It MUST NOT directly mutate registry files, systemd, Git, access
resources, journals, or managed host state. It MUST NOT accept authentication
secrets. Raw declaration JSON remains read-only.

PM3 MAY add public manager projection, preview, optimistic-concurrency,
reviewed-apply, atomic-access, and categorized-log operations. Those operations
MUST reuse existing manager parsing, validation, planning, ownership, recovery,
and mutation semantics.

## Semantic state contract

Projection schema version is `1`.

| Dimension | Values |
| --- | --- |
| desired lifecycle | `Present + Running`, `Present + Stopped`, `Absent` |
| observed runtime | `Running`, `Stopped`, `Failed`, `Unknown`, `Not applicable` |
| health | `Healthy`, `Degraded`, `Failed`, `Unknown`, `Not applicable` |
| tunnel | `Ready`, `Not ready`, `Unknown`, `Not applicable` |
| reconciliation | `Applied`, `Pending`, `Blocked`, `Unknown` |
| recovery | `Clean`, `Required`, `In progress`, `Unknown`, `Not applicable` |

Applicability and precedence MUST be deterministic:

1. Desired and observed runtime remain independent.
2. A desired-stopped/observed-stopped instance is intentional, not failed.
3. Desired-stopped/observed-running is a divergence and MUST receive attention.
4. Absent with no residual runtime MAY use `Not applicable` for runtime, health,
   tunnel, and recovery.
5. Absent with residual runtime MUST keep residual evidence visible.
6. Missing systemd evidence is `Unknown`.
7. MCP health is `Healthy` only when required lightweight manager probe evidence
   affirmatively succeeds; process state alone is insufficient.
8. Tunnel state is `Ready` only on affirmative readiness evidence.
9. Valid all-NOOP plan is `Applied`; valid non-NOOP is `Pending`; invalid plan
   is `Blocked`; unavailable planning is `Unknown`.
10. A currently running recovery transaction is `In progress`; latest failed or
    unreadable recovery/applied evidence is `Required`.
11. `Unknown` MUST NOT be converted to success.

Attention reasons MAY be derived for sorting but MUST NOT replace dimensions.

## Public frontend contracts

The manager MUST expose:

```text
instance summary <id>
instance summaries
instance template
instance preview <candidate-file>
```

`summary` MUST include projection version, declaration fingerprint, desired
lifecycle, semantic state dimensions, access/Git summaries, attention reasons,
and authoritative plan fingerprint when available. Fleet summaries MUST NOT run
full resilience diagnostics per instance.

`template` MUST return manager-owned config version/defaults and required
operator fields. Defaults MUST contain no secrets.

`preview` MUST parse/canonicalize/validate a candidate and run existing manager
generation/planning/collision logic without persistence. It MUST return the
effective declaration, candidate fingerprint, current fingerprint when an
instance exists, validation/collision evidence, proposed plan, semantic plan
items, and plan fingerprint. Preview MUST NOT reserve resources.

## Fingerprints and concurrency

Plan fingerprint schema version is `1`. It MUST hash canonical JSON containing:

- contract version and instance identity;
- desired declaration fingerprint;
- plan validity;
- ordered effective operations expressed as operation/target/resource identity;
- generated resource/content fingerprints relevant to the desired state;
- manager execution precondition version.

It MUST exclude timestamps, durations, operator-facing descriptions, and log
metadata.

Declaration update MUST accept optional `expected_current_fingerprint`. The
manager MUST compare it under an authoritative registry serialization boundary
before persistence and reject mismatch with `STALE_STATE`.

Access add/remove/update MUST accept the same precondition. `access update`
MUST atomically replace one existing alias/mode/path under the instance lock;
the frontend MUST NOT emulate edit as remove/add.

Reviewed apply MUST accept `expected_plan_fingerprint`. `HostApplyService` MUST,
under its existing instance lock, recompute the current plan and fingerprint,
compare the reviewed fingerprint, and reject mismatch with `STALE_STATE` before
host mutation.

## Logs and diagnostics

The manager remains log authority. `instance logs` MUST support `all`, `mcp`,
`tunnel`, and `recovery` categories while preserving the existing unqualified
`all` behavior. The TUI MUST NOT read journals directly.

Full resilience diagnostics remain explicit task actions because they persist
diagnostic evidence. Dashboard refresh MUST use summary/status projections only.

## Textual runtime

Interactive PM3 uses `textual==8.2.8`. Manager/core MUST NOT import Textual.
Frontend dependencies MUST be exact/hash locked in `requirements-tui.lock`.
The canonical repository installation entrypoint MUST be shell-only:

```text
bash scripts/install_toolkit.sh
```

Release mechanics (fingerprinting, staging, atomic switching, manifest writing,
upgrade gating) MUST be implemented in Bash, not in a Python installer module.
The shell installer MAY invoke Python only as the installed product runtime for
manager/declaration validation and frontend package installation.

The standalone toolkit release MUST atomically contain:

```text
manager/core source
manager CLI wrapper
reboot wrapper
TUI source/wrapper
requirements-tui.lock
shell installer
isolated TUI site-packages runtime
runtime manifest
```

The manager and reboot wrappers MUST continue to use the release Python without
the frontend environment. TUI startup failure MUST NOT affect either one. The
TUI wrapper MUST resolve the manager from its own release by default; an
explicit `--manager` override MAY be used for controlled testing.

## Frontend execution model

The Textual event loop MUST NOT block on manager subprocess work.

```text
ManagerClient -> bounded worker layer -> generation-aware projection -> UI
```

PM3 freezes:

- global read concurrency: `4`;
- one mutation at a time per instance;
- bounded captured output: 1 MiB per stream;
- normal read timeout: 30 s;
- diagnostic/log read timeout: 180 s;
- mutation response timeout: 180 s;
- screen-entry and explicit refresh are read-only;
- successful mutation triggers authoritative refresh;
- older generations MUST NOT overwrite newer projections.

Discardable reads MAY be cancelled. A started mutation SHOULD NOT be killed by
frontend navigation/cancellation. If its response is unavailable or times out,
the frontend MUST report `Outcome unknown`, refresh authoritative manager state,
and suppress dependent mutations until that refresh completes.

## Information architecture

```text
Dashboard
  Instances / Host summary / Attention
Instance
  Overview | Access | Git & GitHub | Diagnostics | Settings
New Instance
  Basics -> Runtime -> Tunnel -> Git & Access -> Review
Host
  Health | Components | Developer tools
Ctrl+P
  Advanced/uncommon operations
```

Primary navigation MUST use arrows, Enter, Escape, Tab, Space, `Ctrl+P`, and `?`.
Single-letter command matrices MUST NOT be the primary interaction model.

The dashboard MUST display Desired, Runtime, Health, Tunnel, and Changes. Color
is supplementary. The qualification viewport is 80x24; smaller terminals MUST
degrade without unhandled exceptions.

Reconciliation normal flow is `Review changes -> conditional Apply`. Destructive
deployment removal MUST use explicit confirmation.

Settings MUST use a draft -> manager preview -> conditional manager update
workflow. Stale rejection MUST preserve the local draft. v1 -> v2 projection
MUST preserve PM1 semantics; v2 -> v1 MUST NOT be offered.

New-instance creation MUST use manager template/preview authority. Declaration
creation and deployment remain distinct. If create succeeds but reviewed apply
is stale/fails, the declaration remains visible as unapplied.

## External compatibility

PM3 supersedes curses-specific presentation/runtime tests but MUST preserve:

```text
workspace-mcp-manager-tui --snapshot
workspace-mcp-manager-tui --snapshot --instance <id>
workspace-mcp-manager-tui --smoke
```

Snapshots remain bounded, plain-text, non-interactive, and read-only. Smoke is
startup/render validation with no mutation. curses MUST NOT remain a PM3 runtime
dependency.

## Verification and completion

Pure verification MUST cover state derivation, projections, template/preview,
plan fingerprints, stale declaration/apply/access rejection, atomic access edit,
semantic plan descriptions, async generation suppression, mutation
serialization/outcome-unknown behavior, no-secret/read-only raw surfaces,
Textual Pilot workflows, 80x24 and smaller rendering, same-release resolution,
and deterministic isolated runtime installation.

Live WSL qualification MUST use disposable `manager-qual`, capture a baseline,
perform only reversible mutations, restore through public manager operations,
apply restoration, and prove baseline equivalence plus all-NOOP final plan.

The final regression gate is:

```text
full P6-PM1 regression
+ PM2 external-contract regression
+ PM3 verification
```

Completion requires all gates defined by the PM3 Operator UX TODO, culminating
in `PM3_OPERATOR_UX_QUALIFICATION=PASS`.
