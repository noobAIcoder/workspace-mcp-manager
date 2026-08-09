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

P4/P5 add deterministic resource rendering and reconciliation planning. They
still do **not** write systemd units, start/stop services, create tunnels, or
adopt legacy instances.

## Run from source

```sh
PYTHONPATH=src python3 -m workspace_mcp_manager host inspect --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager host doctor --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager instance validate examples/manager.json --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager --registry-dir examples/registry instance render manager-qual --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager --registry-dir examples/registry instance plan manager-qual --pretty
```

Run the M0 tests without third-party dependencies:

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
```

Lifecycle mutation (`apply`, `start`, `stop`, `remove`, `delete`) remains
deferred until P6. `plan` is explicitly non-mutating.

P5 also feature-gates runtime capabilities that belong to later phases. A
present deployment with admission recovery enabled is a `CONFLICT` until P11
implements the guard runtime; P4 still renders and tests the guard resources.

For automation, successful/valid results return exit 0, structured results with
`ok=false` return exit 1, and `ManagerError` failures return exit 2.

