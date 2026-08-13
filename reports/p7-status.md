# P7 — MCP protocol qualification status

Status: **PASS — LIVE WSL QUALIFIED 2026-08-13**

Qualification implementation checkpoint:

```text
638f611  Allow safe updates of active owned MCP units
```

P7 was qualified through the real ChatGPT `@ManagerQual` MCP/plugin surface
against the disposable manager-owned deployment.

## Qualification target

```text
INSTANCE_ID=manager-qual
WORKSPACE=/home/cloudtoor/repos/workspace-mcp-manager-qual
MCP=127.0.0.1:7657
TUNNEL_HEALTH=127.0.0.1:7373
TUNNEL_PROFILE=workspace-mcp-manager-qual
PERMISSION_MODE=trusted
```

LeadBot and Vigilus were not migrated or modified during P7.

## Qualified live gate

The following checks passed through the deployed MCP/plugin surface:

1. MCP/plugin connectivity and discovery;
2. protocol `initialize`;
3. MCP session cleanup;
4. `server_info` exact workspace identity;
5. configured `trusted` permission mode;
6. in-workspace read;
7. rejection of direct outside-workspace read;
8. `GH_CONFIG_DIR` visibility;
9. `CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS` visibility;
10. NVM toolchain execution through MCP `exec_command`;
11. Git status through MCP;
12. tunnel `/healthz` and `/readyz`.

Live MCP identity:

```text
workspace=/home/cloudtoor/repos/workspace-mcp-manager-qual
protocol_version=2025-11-25
permission_mode=trusted
tool_count=20
landlock_enabled=true
```

Raw protocol qualification returned HTTP 200 for `initialize`, negotiated
`2025-11-25`, returned an `Mcp-Session-Id`, and returned HTTP 200 for session
`DELETE` cleanup.

Tunnel qualification returned:

```text
healthz=live
readyz=ready
```

## WSL GitHub and execution environment

The live MCP service exposes:

```text
GH_CONFIG_DIR=/home/cloudtoor/.config/gh
CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=/home/cloudtoor/.nvm/versions/node/v24.15.0:/home/cloudtoor/.config/gh
PATH=/home/cloudtoor/.nvm/versions/node/v24.15.0/bin:/home/cloudtoor/.local/bin:/usr/local/bin:/usr/bin:/bin
```

This preserves the external contract that the GitHub configuration directory is
available to the MCP execution sandbox while direct MCP file tools remain
workspace-confined.

The selected NVM toolchain executed successfully through MCP `exec_command`:

```text
node=v24.15.0
npm=11.12.1
npx=11.12.1
corepack=0.34.6
pnpm=11.21.0
```

The qualification workspace is a Git repository and `git_status` returned a
clean working tree.

## P7 qualification regression

### P7-Q1 — active owned MCP unit update blocked by listener collision check

While correcting the disposable qualification declaration to expose the
existing WSL GitHub configuration directory, the desired MCP unit changed only
in environment content. The live MCP listener remained on the same endpoint.

The planner nevertheless returned:

```text
RECONCILIATION_CONFLICT
MCP listener is already occupied without verified ownership by manager-qual
```

The same plan simultaneously identified the MCP unit file as manager-owned and
scheduled its `UPDATE` plus `RESTART`.

Cause: listener ownership was accepted only when the currently installed unit
content exactly matched newly generated desired content. A legitimate update to
an active owned unit therefore invalidated the ownership proof that was needed
to perform that update.

Correction:

- current MCP host is extracted from the running unit `ExecStart` identity;
- an active owned MCP listener may remain occupied during a unit-content update
  only when the current instance ID, host, and port positively match the desired
  endpoint;
- changing the desired endpoint onto an occupied listener still fails closed;
- foreign or unverifiable ownership still fails closed;
- regression coverage lives in `tests/test_p7_listener_updates.py`.

After installing the corrected manager, the live `manager-qual` plan was fully
NOOP and apply succeeded with desired fingerprint:

```text
728dcc4f4086ef9e98f856201741dc16c50bee9a9d0f491009661e94cba14701
```

Pure validation after the correction:

```text
Ran 83 tests
OK
```

## Final result

```text
P7_MCP_PROTOCOL_QUALIFICATION=PASS
```

P7 is complete. P8 RO/RW external-folder access lifecycle may begin.
