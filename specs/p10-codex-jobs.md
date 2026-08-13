# P10 — Durable detached Codex jobs

Status: implementation authority for CAP-056 through CAP-065 and CAP-113.

## Objective

Preserve the proven detached Codex lifecycle as a deterministic
`workspace-mcp-manager` capability while keeping Codex installation and
authentication external to P10.

P10 owns:

- executable resolution from the instance's configured MCP execution `PATH`;
- durable read/write job submission;
- transient user-systemd workers;
- persisted request/status/result/output;
- status, bounded output, and cancellation;
- strict request-to-instance/workspace binding;
- independence from the initiating MCP request;
- qualification that the WSL standalone Codex executable is admitted by the
  configured MCP execution environment.

P10 does **not** install Codex, authenticate Codex, migrate Codex sessions, or
persist Codex authentication material. CAP-121 remains P15 and CAP-127 remains
external.

## Public command contract

```text
workspace-mcp-manager codex start <instance> --mode read|write (--prompt <text> | --prompt-stdin)
workspace-mcp-manager codex status <instance> <job-id>
workspace-mcp-manager codex output <instance> <job-id> [--limit-bytes N]
workspace-mcp-manager codex cancel <instance> <job-id>
```

`start` MUST return after the detached worker is successfully queued. It MUST
NOT wait for Codex completion. `--mode read` and `--mode write` preserve the two
legacy modes.

Prompt text MUST cross the MCP-facing host boundary over stdin rather than as a
transient systemd command-line argument. The prompt is intentionally persisted
inside the private manager job state as the durable request.

## Executable resolution

Codex MUST be resolved only from `desired.mcp.exec_path` in declared order.
The worker MUST NOT silently fall back to the host process `PATH`.

Before a job is created, the resolved executable MUST pass:

```text
codex --version
```

under the same effective execution environment used by the worker.

A missing or nonfunctional executable is a host dependency failure. P10 MUST
fail closed and MUST NOT install, upgrade, relink, or substitute Codex.

## WSL standalone contract

The reconciled WSL authority uses a standalone Codex entrypoint. For live P10
qualification:

1. the standalone entrypoint directory MUST be present in
   `desired.mcp.exec_path`;
2. the executable/package roots required to execute that entrypoint MUST be
   represented by the existing `mcp.external_roots` mechanism;
3. `command -v codex` through MCP `exec_command` MUST resolve the intended WSL
   standalone entrypoint rather than an unrelated npm/Windows installation;
4. `codex --version` through the configured environment MUST succeed.

Installation/repair remains P15 even if this gate detects a missing standalone
release.

## Worker environment

The host-side Codex worker MUST explicitly use:

```text
HOME=<real account home>
PATH=<desired.mcp.exec_path>
CODEX_HOME=<real account home>/.codex
GIT_TERMINAL_PROMPT=0
GH_CONFIG_DIR=<desired.github.config_dir>   # when configured
```

Codex authentication/session values MUST NOT be copied into desired state,
applied state, transaction evidence, job status/result metadata, or reports.

## Mode mapping

The worker MUST invoke noninteractive Codex execution with the managed workspace
as the Codex root.

```text
read  -> codex exec -s read-only       -C <workspace> ...
write -> codex exec -s workspace-write -C <workspace> ...
```

P10 MUST NOT use `danger-full-access` or bypass Codex sandboxing/approvals to
implement either mode.

## Durable state

Jobs live under the real account state root:

```text
~/.local/state/workspace-mcp-manager/instances/<instance>/codex/<job-id>/
```

Each job directory is mode `0700`. Durable files are mode `0600`:

```text
request.json
status.json
result.json          # once terminal
stdout.log
stderr.log
last-message.txt     # transient until folded into result.json
```

`request.json` MUST include at least:

- schema version;
- job ID;
- instance ID;
- workspace path;
- desired fingerprint at submission;
- mode;
- exact Codex sandbox mapping;
- resolved Codex executable path/version;
- real account home and `CODEX_HOME`;
- configured execution `PATH`;
- configured `GH_CONFIG_DIR` reference when present;
- prompt;
- SHA-256 of the UTF-8 prompt;
- creation timestamp.

The prompt digest MUST be revalidated before status/output/cancel/worker use.

`status.json` MUST persist lifecycle state and timestamps. Active states are
`queued` and `running`. Terminal states are:

```text
succeeded
failed
cancelled
interrupted
```

`result.json` MUST persist terminal state, exit code when available, completion
time, error summary when applicable, and a bounded final Codex message when
available.

`stdout.log` and `stderr.log` are durable separate streams. The public `output`
operation MUST return bounded tails capped at 65536 bytes per stream and state
whether each stream was truncated.

## Binding validation

Every operation MUST resolve the current managed instance and verify that the
persisted request exactly matches:

```text
job_id
instance_id
workspace_path
sandbox mapping
```

The stored mode and prompt digest MUST also validate. Before worker execution,
and before returning status/output/cancel for a persisted job, execution
authority MUST also revalidate against the current instance:

```text
resolved Codex executable
real account home
CODEX_HOME
configured MCP execution PATH
GH_CONFIG_DIR reference
```

Any mismatch is a reconciliation conflict. An arbitrary caller MUST NOT
redirect a persisted job to another instance/workspace or silently move an
already-submitted request onto a different executable/toolchain environment.

Changing unrelated desired-state fields after submission does not invalidate a
job; permanent equality of the full desired fingerprint is not required.

## Detached systemd contract

Submission launches a unique transient user-systemd service:

```text
workspace-mcp-codex-<instance>-<job-id>.service
```

The launch MUST:

- use `Type=exec`;
- use the managed workspace as `WorkingDirectory`;
- use `KillMode=control-group`;
- use `UMask=0077`;
- use the installed `workspace-mcp-manager _runtime codex-worker` entrypoint;
- use `--no-block`;
- use `--collect`;
- NOT use `--wait`.

Therefore the Codex worker and its child process are not owned by the initiating
MCP request or MCP process lifetime.

## Status and interruption reconciliation

`result.json` is authoritative once present. Otherwise `status` MUST inspect the
dedicated transient systemd unit using structured properties.

- `active`/`activating`/`reloading`/`deactivating` MUST NOT be classified as
  interrupted;
- a short bounded activation grace is permitted after `systemd-run --no-block`
  returns, because the unit may not yet be observable through `systemctl show`;
- once that grace has passed, a queued/running job with no terminal result and
  no live dedicated unit MUST persist and return terminal `interrupted`.

This reconciliation is what makes persisted jobs observable after initiating
request loss or MCP restart while still failing closed when their detached
worker actually disappears.

## Cancellation

`cancel` MUST stop the exact transient worker unit for a nonterminal job and
then persist `cancelled` status/result evidence. Cancellation is idempotent for
already terminal jobs.

## Qualification gate

Pure verification MUST cover:

1. configured-PATH resolution;
2. broken executable fail-closed behavior before job creation;
3. prompt transport outside the systemd argv;
4. detached systemd launch without `--wait`;
5. persisted request/status/result/output;
6. read-only and workspace-write sandbox mapping;
7. real HOME / configured PATH / CODEX_HOME worker environment;
8. request binding and prompt-digest rejection;
9. bounded stdout/stderr tail output;
10. cancellation persistence.

Live WSL qualification MUST additionally prove with the intended standalone
Codex installation:

1. MCP resolution and `codex --version`;
2. one successful read job;
3. one successful write job producing an explicitly requested disposable file;
4. status/output/result persistence;
5. a long-enough job remains active after the initiating command returns;
6. cancellation of a live job;
7. manager/MCP restart does not destroy persisted job state;
8. no Codex auth/session material is present in manager evidence;
9. final manager plan/apply and P7-P9 smoke remain healthy.

P10 MUST NOT be marked PASS while the intended WSL standalone Codex executable
is missing or fails its version preflight.
