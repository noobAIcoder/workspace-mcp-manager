# workspace-mcp-manager

Greenfield deterministic control plane for multiple `coding-tools-mcp` +
`tunnel-client` deployments.

The project deliberately separates:

- **desired state** — strict, versioned instance declarations;
- **observed state** — freshly inspected host/runtime facts;
- **applied/ownership state** — non-secret provenance and ownership evidence.

M0 implements only the non-host-mutating foundation:

1. reconciled capability dispositions;
2. domain/configuration contracts;
3. read-only host inspection;
4. the persistent desired-state registry.

It does **not** generate or modify systemd units, start/stop services, create
tunnels, or adopt legacy instances yet.

## Run from source

```sh
PYTHONPATH=src python3 -m workspace_mcp_manager host inspect --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager host doctor --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager instance validate examples/manager.json --pretty
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
```

Lifecycle mutation (`plan`, `apply`, `start`, `stop`, `remove`, `delete`) is
intentionally deferred until the M0 contracts are reviewed.

