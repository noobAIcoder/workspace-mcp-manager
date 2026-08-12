# P6 v5 — authoritative instance-manager boundary

## Objective

Make `workspace-mcp-manager` itself the complete P6 operator surface. Neither
qualification scripts nor MCP filesystem permissions own lifecycle semantics.

## Host boundary

All manager state outside the current MCP workspace is reached through a narrow
transient `systemd --user` worker. This includes:

```text
instance create/update
instance list/show/render
instance plan/status/logs
instance apply/start/stop/restart/remove
```

The MCP-facing process needs no broad read/write access to `~/.config` or
`~/.local/state`. Registry create/update declarations are validated in the
caller, streamed over stdin to the host worker, validated again there, and then
atomically persisted.

## Local instance creation

A declaration is sufficient to create a local instance. `instance apply` renders
and owns:

- `coding-tools-mcp-<id>.service`;
- `tunnel-client-<id>.service`;
- manager-owned tunnel profile;
- instance state and ownership markers;
- optional access namespace resources.

No pre-existing MCP/tunnel service is required. The remote OpenAI tunnel object
identified by `tunnel.id` remains an external prerequisite and contains no
manager-owned secret material.

## Transaction boundary

Every host-resource or systemd mutation attempt is written to the transaction
journal before the mutation is issued. Successful mutations are recorded
separately. Failure responses preserve bounded/redacted transaction evidence and,
where applicable, unit status and recent journal output.

A fresh reconciliation plan MUST immediately precede normal apply mutation.
Destructive application additionally re-verifies ownership and known residual
state before the first stop/disable/remove operation.

`applied.json` is written only after readiness/stop qualification and a final
fresh plan prove convergence.

## Failed-first-apply recovery

A first apply can fail after creating the manager state directory and starting a
runtime that appends logs, while rollback removes the new `.owner` marker but is
unable to remove the now non-empty directory.

This residue MAY be recovered only from explicit failed-transaction evidence and
only when every residual entry is a recognized runtime log. The recovery action
has its own recovery plan and journal. It restores the missing ownership marker
and preserves diagnostic logs. Unknown residue is never deleted automatically.

## NVM-managed Node tooling

NVM itself is an interactive-shell function and is not part of the MCP service
contract. The selected NVM Node version is instead made explicit in desired
state:

```text
mcp.exec_path      includes <nvm-version-root>/bin
mcp.external_roots includes <nvm-version-root>
```

This gives `node`, `npm`, `npx`, `corepack`, `pnpm`, and their implementation
files under `lib/node_modules` a deterministic PATH/read-execute contract without
sourcing shell initialization files.

## Qualification sequencing

The canonical `manager-qual` declaration remains `present+running`. Live P6
qualification executes:

```text
plan
→ apply
→ status
→ apply again (managed-resource NOOP)
→ status
→ stop
→ status
→ start
→ status
→ restart
→ status
→ remove
→ status
→ update desired state back to present+running
→ apply
→ status
```

The explicit update before recreate follows the frozen lifecycle contract:
`remove` sets desired deployment to `absent`; `apply` only converges to desired
state and never changes it.

Qualification also proves MCP discovery, tunnel health/readiness, manager-owned
unit/profile generation, absence of the legacy tunnel wrapper, NVM PATH/root
rendering, state ownership, transaction/applied evidence, shared-resource
retention, and isolation from all other managed instances.

## Failure evidence

Failed lifecycle operations return bounded/redacted systemd unit snapshots,
recent journal evidence, and the manager transaction reference/evidence.
`instance status` and `instance logs` use the same host boundary and are the
canonical operator diagnostics for P6.
