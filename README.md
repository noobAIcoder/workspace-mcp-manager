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
survive the initiating MCP request. P10 implementation/pure verification is
complete; live WSL qualification remains blocked until the externally managed
standalone Codex payload is restored.

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

P5/P6 also feature-gate runtime capabilities that belong to later phases. A
present deployment with admission recovery enabled is a `CONFLICT` until P11
implements the guard runtime; P4 still renders and tests the guard resources.

P7, P8, and P9 are complete and live-qualified. P10 implementation is complete
and pushed, but the P10 live gate is blocked by the missing WSL standalone
Codex payload. P11 remains out of scope until P10 live qualification passes.

For automation, successful/valid public results return exit 0, structured public
results with `ok=false` return exit 1, and public `ManagerError` failures return
exit 2. Internal JSON host-worker commands use transport-success semantics so
structured semantic failures are preserved across the host boundary.
