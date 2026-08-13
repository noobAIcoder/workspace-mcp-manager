# workspace-mcp-manager

Greenfield deterministic control plane for multiple `coding-tools-mcp` +
`tunnel-client` deployments.

The project deliberately separates:

- **desired state** — strict, versioned instance declarations;
- **observed state** — freshly inspected host/runtime facts;
- **applied/ownership state** — non-secret provenance and ownership evidence.

M0 implements the non-host-mutating foundation:

1. reconciled capability dispositions;
2. domain/configuration contracts;
3. read-only host inspection;
4. the persistent desired-state registry.

P4/P5 add deterministic resource rendering and reconciliation planning.

P6 adds the authoritative host-side apply lifecycle through transient user-systemd
workers. P6 is live-qualified on the disposable `manager-qual` instance, including
apply/NOOP apply/stop/start/restart/remove/recreate, ownership, rollback/recovery,
transaction evidence, MCP discovery, tunnel health/readiness, and deterministic
NVM-managed Node toolchain rendering.

P7 live-qualifies the manager-owned `manager-qual` deployment through the real
ChatGPT MCP/plugin surface: discovery, protocol initialization/session cleanup,
workspace and permission identity, workspace confinement, WSL GitHub/NVM
environment propagation, Node toolchain execution, Git status, and tunnel
health/readiness. P7 also regression-covers safe updates of an active owned MCP
unit without weakening endpoint-collision checks.

P8 adds deterministic RO/RW external-folder access. Desired access mutations run
through the host boundary, generated user-systemd units mount sources under
`.workspace-mcp-access/<alias>`, access ownership is tracked and cleaned from
applied-state evidence, and both namespace preflight and live plugin enforcement
are qualified on `manager-qual`.

P9 adds non-mutating per-instance Git/GitHub diagnostics through the same host
boundary. The manager detects the repository and working-tree state, probes a
configured remote with `ls-remote`, diagnoses Git identity and GitHub CLI auth,
forces the real account HOME for credential resolution, and uses the same
configured execution PATH rendered into the MCP service.

P10 implements durable detached Codex jobs through transient user-systemd
workers. Jobs resolve Codex only from the instance's declared MCP execution
PATH, preserve read-only/workspace-write modes, persist request/status/result
and separate stdout/stderr logs, support bounded output and cancellation, and
survive the initiating MCP request. The configured WSL Codex installation and
the full detached read/write/cancel/restart lifecycle are live-qualified on
`manager-qual`.

P11 implements exact local MCP admission recovery. A manager-owned 30-second
timer probes MCP `initialize`, deletes every healthy probe session, and restarts
only the local MCP unit when HTTP 503 carries the exact JSON-RPC error message
`maximum HTTP session count reached`. Persisted recovery evidence enforces the
120-second cooldown across guard invocations. Controlled live saturation on
`manager-qual` qualified the exact-signature restart, dependent tunnel recovery,
cooldown suppression, and reversible guard enable/disable lifecycle.

P12 adds persisted non-mutating resilience snapshots. `instance diagnose`
captures separate MCP/tunnel service, process, cgroup and memory evidence;
host meminfo/PSI; current MCP discovery/initialize with session cleanup; current
tunnel health/readiness; and time-windowed structured tunnel-log evidence. It
keeps local MCP 5xx, tunnel control-plane 5xx/poll timeout, process-restart, and
memory-pressure classifications in explicit independent incident domains.
Controlled live qualification on `manager-qual` proved current healthy evidence,
retained real poll-timeout history, local MCP saturation classification without
service restart, snapshot redaction/persistence, and final NOOP convergence.

P13 adds controlled reboot checkpointing and reconciliation for native Linux.
`host reboot`
fsyncs a manager-owned checkpoint containing the current boot ID and all
present+running instance fingerprints before it performs even the authorization
preflight. The separately installed `workspace-mcp-reboot` bridge accepts only
`--check` or the exact preserved `sudo -n /usr/bin/systemctl reboot` action;
the manager never creates or broadens sudo policy. `host reboot-check` compares
the persisted boot ID with `/proc/sys/kernel/random/boot_id` and reconciles the
captured expected instances after a real boot change. WSL reboot execution is
explicitly deferred: both `host reboot` and `workspace-mcp-reboot` fail closed
on WSL before checkpoint creation or sudo/systemctl execution. A future optional
Windows-side listener may provide WSL restart without enabling WSL interop.
Native-Linux physical reboot remains a separate host acceptance gate.

P14 replaces the legacy cleanup toggle with an explicit ownership-gated cleanup
surface. `cleanup audit` is read-only and recognizes legacy
`workspace-mcp` configs, tunnel profiles, MCP/tunnel user units, per-instance
launchers, and bounded legacy state logs. `cleanup execute <id>` requires one
explicit target, revalidates every ownership marker immediately before mutation,
stops/disables only the exact legacy units, removes only proven per-instance
residue, and preserves shared binaries plus credential/runtime files. Live WSL
qualification used only the synthetic `p14-legacy-qual` instance and proved the
existing `leadbot`, `manager`, and `wsl-reconcile` legacy deployments remained
unchanged.

## Run from source

```sh
PYTHONPATH=src python3 -m workspace_mcp_manager host inspect --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager host doctor --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager instance validate examples/manager.json --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager --registry-dir examples/registry instance render manager-qual --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager --registry-dir examples/registry instance plan manager-qual --pretty
```

Run the pure test suite without third-party dependencies:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Registry

By default declarations are stored under the real Unix account home, not the
possibly sandboxed `$HOME` inherited from an MCP request:

```text
~/.config/workspace-mcp-manager/instances/<instance>.json
```

The CLI supports `--registry-dir` for explicit testing/bootstrap isolation.

## Current command surface

```text
workspace-mcp-manager host inspect
workspace-mcp-manager host doctor
workspace-mcp-manager host components
workspace-mcp-manager host reboot --reason <text>
workspace-mcp-manager host reboot-check

workspace-mcp-reboot --check
workspace-mcp-reboot

workspace-mcp-manager cleanup audit [<legacy-id>]
workspace-mcp-manager cleanup execute <legacy-id>

workspace-mcp-manager instance validate <file>
workspace-mcp-manager instance create <file>
workspace-mcp-manager instance update <file>
workspace-mcp-manager instance list
workspace-mcp-manager instance show <id>
workspace-mcp-manager instance render <id>
workspace-mcp-manager instance plan <id>
workspace-mcp-manager instance status <id>
workspace-mcp-manager instance logs <id>
workspace-mcp-manager instance git <id>
workspace-mcp-manager instance diagnose <id> [--since-seconds N]
workspace-mcp-manager instance apply <id>
workspace-mcp-manager instance start <id>
workspace-mcp-manager instance stop <id>
workspace-mcp-manager instance restart <id>
workspace-mcp-manager instance remove <id>

workspace-mcp-manager access list <id>
workspace-mcp-manager access add-ro <id> <alias> <path>
workspace-mcp-manager access add-rw <id> <alias> <path>
workspace-mcp-manager access remove <id> <alias>

workspace-mcp-manager codex start <id> --mode read|write (--prompt <text> | --prompt-stdin)
workspace-mcp-manager codex status <id> <job-id>
workspace-mcp-manager codex output <id> <job-id> [--limit-bytes N]
workspace-mcp-manager codex cancel <id> <job-id>
```

`plan` remains explicitly non-mutating. Lifecycle mutations execute through the
manager's narrow host-worker boundary and never require manual systemd/profile
editing.

P7 through P12 are complete and live-qualified. P13 implementation and pure
verification are complete. Its WSL platform gate is qualified to fail closed as
unsupported; WSL restart is deferred. Native-Linux physical reboot acceptance
has not yet been run for the greenfield manager. P14 implementation, pure
verification, and synthetic WSL legacy-cleanup qualification are complete. P15
has not started.

For automation, successful/valid public results return exit 0, structured public
results with `ok=false` return exit 1, and public `ManagerError` failures return
exit 2. Internal JSON host-worker commands use transport-success semantics so
structured semantic failures are preserved across the host boundary.
