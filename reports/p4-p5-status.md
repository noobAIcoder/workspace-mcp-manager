# P4–P5 execution status

Date: 2026-08-09
Status: **PASS — stop gate reached before P6**

## Baseline

M0 was frozen and committed before P4/P5 implementation:

```text
9497588 Establish workspace-mcp-manager M0 baseline
```

Repository-local Git identity was derived from the reconciled WSL evidence and
configured only in this new repository; no global Git identity was changed.

## P4 — deterministic resource generation

Implemented non-mutating generation for:

- manager-owned per-instance state directory and ownership marker;
- manager-owned tunnel profile directory and tunnel profile;
- `coding-tools-mcp-<id>.service`;
- `tunnel-client-<id>.service`;
- optional admission guard service/timer generation;
- `.workspace-mcp-access/<alias>` RO/RW namespace resources;
- optional `GH_CONFIG_DIR`;
- `CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS` using the verified `os.pathsep` contract;
- positive ownership markers and shared-resource retention metadata.

Preserved proven legacy semantics include MCP/tunnel unit dependencies, restart
policies, stop timeouts, `KillMode=mixed`, tunnel `UMask=0077`, health/readiness
profile shape, RO/RW systemd bind directives, and `PrivateUsers=true` when extra
mounts exist.

Generated resource fingerprints are frozen in:

```text
tests/golden/sample-resource-fingerprints.json
```

The `systemd-analyze --user verify` integration test remains mandatory where it
can execute. In the current MCP sandbox it is explicitly skipped only for:

```text
Failed to setup working directory: Permission denied
```

No other `systemd-analyze` failure is converted to a skip.

## P5 — reconciliation planning

Implemented read-only planning operations:

```text
CREATE
UPDATE
ENABLE
DISABLE
START
STOP
RESTART
REMOVE
NOOP
CONFLICT
```

The planner now validates before mutation:

- manager-registry workspace/port/profile/tunnel-ID collisions;
- live listener collisions;
- existing managed workspace/MCP-port identities from systemd;
- existing legacy tunnel collision metadata when observable;
- required workspace/binary/credential-reference/runtime paths;
- live manager ownership markers;
- foreign/unverified resource refusal.

Teardown planning is ordered so service STOP/DISABLE operations precede
resource removal, the tunnel is stopped before its MCP, shared directories are
retained, and instance directories require positive ownership-marker evidence
before destructive planning.

Present deployments that enable admission recovery are deliberately rejected
with `CONFLICT` until P11 implements the guard runtime. This prevents P6 from
installing an enabled timer whose action is only a placeholder.

Cached/applied ownership is not allowed to override missing live ownership.
P6 will be the first phase permitted to persist successful applied-state data.

## Automated qualification

Current suite:

```text
48 tests
47 PASS
1 SKIP — systemd-analyze working-directory denial caused by the current MCP sandbox
0 FAIL
```

Direct `@WorkspaceMCPManager` revalidation also passes from the exact workspace
`/home/cloudtoor/repos/workspace-mcp-manager`: the same suite reports no test
failures, resource rendering returns six candidate
resources, and the live plan returns the same five fail-closed conflicts.

CLI semantics are automation-safe: a conflict-bearing plan emits its structured
JSON with `ok=false` and exits 1; `ManagerError` remains exit 2.

## `manager-qual` candidate

The non-mutating candidate is frozen in:

```text
examples/registry/manager-qual.json
```

Identity:

```text
INSTANCE_ID=manager-qual
WORKSPACE=/home/cloudtoor/repos/workspace-mcp-manager-qual
MCP=127.0.0.1:7657
TUNNEL_HEALTH=127.0.0.1:7373
TUNNEL_PROFILE=workspace-mcp-manager-qual
TUNNEL_ID=tunnel_6a78e290552c81918e1fa028923b3087
```

Admission recovery is disabled for this qualification declaration because its
runtime action belongs to P11. P4 still renders/tests guard resources for
declarations that enable them, while P5 hard-blocks applying such a declaration
until P11 exists.

Current live plan is correctly invalid. It proposes the intended fresh
resources but refuses P6 because these prerequisites remain unresolved:

1. `/home/cloudtoor/repos/workspace-mcp-manager-qual` does not yet exist.
2. `~/.local/bin/workspace-mcp-manager` is not yet installed.
3. collision-sensitive identity metadata for the existing LeadBot, manager and
   wsl-reconcile tunnel units cannot be fully verified from the current MCP
   filesystem sandbox.

Known MCP ports are observable and distinct:

```text
leadbot       7654
wsl-reconcile 7655
manager       7656
manager-qual  7657 desired
```

The candidate health port `7373` is not currently listening. P5 does not treat
the inability to inspect legacy tunnel IDs/profiles as proof of uniqueness.

## Execution incident preserved for later resilience work

During P4/P5 implementation the `@WSLMCPReconsile` remote path returned 502.
Local MCP/tunnel services were initially live. After a tunnel restart,
`/readyz` identified local MCP initialization failure; an explicit MCP probe
confirmed exactly:

```text
503 maximum HTTP session count reached
```

Using the independent `@WorkspaceMCPManager` recovery path, only
`coding-tools-mcp-wsl-reconcile.service` was restarted. Discovery and tunnel
readiness recovered to HTTP 200 and the reconciliation plugin became usable
again. This is supporting evidence for the already-frozen P11 exact-signature
recovery requirement; it did not trigger any manager-qual/P6 mutation.

## P6 boundary

No `manager-qual` directory, manager installation, generated unit, tunnel
profile, listener, or service was created by P4/P5.

Before P6, define a narrow host-execution boundary that lets the manager update
its own registry/state and owned user-systemd resources while still allowing
complete collision observation. Broad writable exposure of the user's home or
tunnel credential directory is not acceptable.
