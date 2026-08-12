# P6 v4 — authoritative instance-manager boundary

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

## Qualification sequencing

Live P6 qualification now separates local deployment from remote connectivity:

```text
declare present+stopped
→ plan
→ apply
→ prove units/profile exist and are stopped
→ status
→ start
→ require MCP discovery + tunnel health + tunnel ready
→ stop/start/restart/remove/recreate
```

This makes a tunnel registration problem distinguishable from a resource-
generation or systemd-application problem.

## Failure evidence

Failed lifecycle operations return bounded/redacted systemd unit snapshots and
recent journal evidence. `instance status` and `instance logs` use the same host
boundary and are the canonical operator diagnostics for P6.
