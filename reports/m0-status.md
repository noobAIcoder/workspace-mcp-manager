# M0 execution status

Date: 2026-08-09

Status: **PASS — stop gate reached; no host-mutating manager lifecycle has been implemented.**

## P0 — normative evidence freeze

PASS.

- Reconciliation inventory SHA-256 frozen in `specs/m0-evidence.md`.
- WSL and Jetson evidence snapshots remain the behavioral source of truth.
- `specs/capabilities.tsv` contains exactly 130 capability rows.
- One-time cross-check against `reports/implemented-functionality-inventory.md`:

```text
inventory_count=130
freeze_count=130
exact_match=true
```

- Every capability has an explicit `PRESERVE`, `REPLACE`, `RETIRE`, `DEFER`, or
  `EXTERNAL` disposition and target.
- The previous canonicalization backlog is retained as historical evidence, not
  as the implementation architecture.
- `coding-tools-mcp 0.2.2` external-root behavior was verified directly from the
  installed WSL package: `CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS` is split using
  `os.pathsep` and admitted as Landlock read/execute roots.

## P1 — project skeleton and domain model

PASS.

Implemented:

- stdlib-only Python package and CLI entry point;
- compact codebase registry;
- strict result/error model;
- cross-cutting secret redaction;
- desired, observed, and applied/ownership state types;
- explicit lifecycle intent (`deployment` + `runtime`);
- deterministic desired-state canonical JSON + SHA-256 fingerprint;
- real-account path abstraction that works when MCP Landlock blocks passwd
  lookup by falling back to the user-systemd manager's login `HOME`.

Frozen semantic decisions:

- implicit legacy toggle behavior is intentionally retired;
- old actuator/setup architecture is replaced by manager application services;
- `remove` means desired deployment becomes absent; declaration deletion is a
  distinct later operation and requires observed absence.

## P2 — read-only host inspection

PASS.

Implemented read-only inspection for:

- OS/kernel/architecture/WSL;
- user-systemd state and relevant unit inventory;
- linger state with a `loginctl list-users` fallback for MCP/NSS confinement;
- command paths and bounded version probes;
- TCP listeners;
- existing workspace-MCP instance candidates from systemd;
- legacy config/profile paths when directly readable;
- selected non-secret environment path metadata;
- secret *presence* booleans only;
- candidate external executable/read roots.

Live direct-workspace observation through `@WorkspaceMCPManager`:

```text
wsl=true
account_home=/home/cloudtoor
linger=true
instances=leadbot,manager,wsl-reconcile
config_scan_incomplete=true
candidate_external_roots=5
```

`config_scan_incomplete=true` is expected under the current MCP Landlock policy:
the manager can observe the systemd deployments but cannot directly enumerate
all real-home configuration files from this MCP sandbox. The inspector reports
that limitation rather than incorrectly claiming no existing instances.

Also observed: `tunnel-client`, Codex, Node/npm, and `uv` are discoverable on the
current MCP PATH but their user-install roots are not currently executable from
the manager MCP Landlock sandbox. This is preserved as a future P4/P10
configuration prerequisite, not hidden by M0.

## P3 — strict desired-state registry

PASS.

Implemented schema/version 1 for:

```text
instance_id
workspace_path
lifecycle
mcp.binary/host/port/permission_mode/shell_env_inherit/exec_path/external_roots
tunnel.binary/id/profile/health_host/health_port/env_file
access.read_only[]/read_write[]
github.config_dir
recovery admission-guard policy
```

Validation fails closed for unknown fields, unsupported versions, invalid
instance/access IDs, relative paths, duplicate external roots/access sources,
legacy-equivalent access alias collisions, access sources already inside the
workspace, and MCP/tunnel endpoint collision.

Registry writes are atomic, mode `0600`, and limited to manager desired-state
configuration. M0 performs no systemd/tunnel/service mutation.

Example `examples/manager.json` validates with fingerprint:

```text
c8c2d3410525e01beef373963940cfebe16e911276d1488312b900e7493f9d37
```

## Validation

Stdlib unit/integration-style tests:

```text
@WSLMCPReconsile RW mount path: 28/28 PASS
@WorkspaceMCPManager direct workspace: 28/28 PASS
```

Direct MCP workspace identity remains:

```text
/home/cloudtoor/repos/workspace-mcp-manager
```

Git repository state at the M0 stop gate:

```text
branch: main
commits: none
all M0 project files: untracked
```

No commit was created because none was requested.

## Stop gate

Per the greenfield plan, execution stops after P3 for review of the domain and
configuration contracts before P4 resource generation or any manager-owned
host mutation begins.
