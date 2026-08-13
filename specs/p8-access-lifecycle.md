# P8 — External-folder access lifecycle

Status: normative implementation/qualification specification for P8.

## Objective

Provide deterministic per-instance read-only and read-write external-folder
access without expanding the direct `coding-tools-mcp` file-tool boundary.

This phase implements CAP-095 through CAP-110. P9 and later capabilities remain
out of scope.

## Desired-state contract

An instance MAY declare external folders in:

```json
{
  "access": {
    "read_only": [{"alias": "docs", "path": "/srv/shared/docs"}],
    "read_write": [{"alias": "scratch", "path": "/srv/shared/scratch"}]
  }
}
```

The manager MUST reject:

- invalid aliases;
- aliases that collide after legacy normalization;
- duplicate source paths;
- non-absolute source paths;
- source paths containing `:` because systemd bind-path syntax uses `:` as a
  structural delimiter;
- a source equal to, contained by, or containing the workspace path.

Host planning MUST fail closed when a declared source cannot be observed as an
existing directory. RW sources MUST additionally be writable/searchable by the
account that owns the user service.

## Command contract

The public CLI MUST expose:

```text
workspace-mcp-manager access list <instance>
workspace-mcp-manager access add-ro <instance> <alias> <path>
workspace-mcp-manager access add-rw <instance> <alias> <path>
workspace-mcp-manager access remove <instance> <alias>
```

`list` MUST be read-only.

`add-ro`, `add-rw`, and `remove` MUST atomically mutate desired state through the
existing transient user-systemd host boundary. They MUST serialize with apply
transactions for the same instance. They MUST NOT silently apply host changes;
their result MUST state that an apply is required.

`remove` MUST resolve aliases using the same legacy-normalized identity used for
collision detection and MUST fail when the alias is absent.

## Generated namespace contract

Every declared folder MUST be exposed inside the MCP workspace at:

```text
.workspace-mcp-access/<alias>
```

The generated MCP unit MUST use:

```text
RO -> BindReadOnlyPaths=
RW -> BindPaths=
PrivateUsers=true
```

`PrivateUsers=true` MUST be present whenever at least one external folder is
configured and MUST be absent when none are configured.

The manager MUST perform a real transient user-systemd access-namespace
preflight before mutating generated resources for a present deployment with
external folders. After the manager-owned target mountpoint directories are
created, it MUST repeat the preflight against those exact workspace paths before
replacing or restarting the MCP unit. Failure of either preflight MUST abort the
transaction; an exact-target failure MUST leave the previously installed MCP
unit authoritative and roll back the newly created access resources.

## Ownership and cleanup contract

When access is configured, the manager MUST own:

```text
.workspace-mcp-access/
.workspace-mcp-access/.owner
.workspace-mcp-access/.gitignore
.workspace-mcp-access/<alias>/
```

The ownership marker MUST identify the instance. Cleanup MUST refuse foreign,
unverified, malformed, or unexpectedly located access resources.

When an alias is removed from desired state, the next apply MUST remove the
obsolete manager-owned mountpoint after the MCP service has been reconciled.
When the last alias is removed, the next apply MUST also remove the owned access
root, marker, and ignore file. Applied-state evidence MUST be used to identify
obsolete resources; arbitrary workspace content MUST NOT be inferred as owned.

`.workspace-mcp-access` MUST NOT dirty Git while configured.

## Lifecycle ordering

An access declaration change that alters the MCP unit MUST produce an MCP unit
update and, when the unit is active, a restart. `instance start` MUST therefore
never activate a stale access declaration: normal reconciliation MUST update the
unit before activation.

Obsolete access resources MUST be removed only after the running MCP unit has
been restarted/stopped as required, so an existing private mount namespace is
not treated as convergence for the new desired state.

## Qualification gate

P8 passes only when the disposable `manager-qual` instance demonstrates through
the real MCP/plugin surface:

1. `access add-ro` and `access add-rw` desired-state mutation;
2. deterministic plan and apply;
3. mounted RO and RW aliases visible inside the workspace;
4. RO content readable but not writable through MCP;
5. RW content readable and writable through MCP;
6. direct reads of the external source paths remain rejected by MCP file tools;
7. `access remove` for both aliases followed by deterministic cleanup;
8. `.workspace-mcp-access` absent after the final removal;
9. Git working tree clean before and after the qualification;
10. full pure test suite green.

Final gate:

```text
P8_ACCESS_LIFECYCLE_QUALIFICATION=PASS
```
