# P6 — Apply lifecycle status

Status: **IMPLEMENTED, LIVE WSL QUALIFICATION REQUIRED**

Base checkpoint expected by this overlay:

```text
a71313c  Implement P4 resource generation and P5 planning
```

## Implemented

P6 adds a narrow host-execution boundary based on transient user-systemd workers:

```text
workspace-mcp-manager instance <operation>
        ↓
systemd-run --user --wait --collect --pipe
        ↓
workspace-mcp-manager _runtime ...
        ↓
fresh host-side P5 plan
        ↓
validate → mutate → qualify → persist applied state
```

Public lifecycle operations:

```text
instance plan
instance apply
instance start
instance stop
instance restart
instance remove
```

The host worker performs:

- a first P5 validation and a second fresh plan immediately before mutation;
- host-side `systemd-analyze --user verify` for generated units;
- deterministic file/directory application;
- `systemctl --user daemon-reload`;
- enable/start/restart/stop/disable in dependency-safe order;
- readiness waits for MCP discovery plus tunnel `/healthz` and `/readyz`;
- non-secret per-transaction journals;
- `applied.json` only after successful post-apply convergence;
- fail-safe rollback of generated resources and newly started/enabled units where feasible.

`start`, `stop`, `restart`, and `remove` update the desired lifecycle target inside the host worker, so callers inside `coding-tools-mcp` do not need direct write access to the real manager registry.

## Security boundary

The transient worker runs under the existing user systemd manager rather than inside the MCP Landlock execution sandbox. It receives only:

- the absolute manager executable;
- the absolute registry directory;
- the requested non-secret operation/instance ID.

Tunnel credentials remain external. The P6 worker does not copy credential values into desired state, journals, applied state, or generated source.

## Qualification target

Disposable instance:

```text
INSTANCE_ID=manager-qual
WORKSPACE=/home/cloudtoor/repos/workspace-mcp-manager-qual
MCP=127.0.0.1:7657
TUNNEL_HEALTH=127.0.0.1:7373
TUNNEL_PROFILE=workspace-mcp-manager-qual
TUNNEL_ID=tunnel_6a78e290552c81918e1fa028923b3087
```

The bootstrap-managed `manager`, `wsl-reconcile`, and LeadBot deployments are not migration targets in P6.

## Live gate

Run:

```bash
bash scripts/qualify_p6_wsl.sh
```

If the historical qualification tunnel was deleted, pass a fresh dedicated tunnel with `QUAL_TUNNEL_ID=tunnel_...`; do not reuse a live instance tunnel.

The script qualifies:

```text
plan
→ apply
→ ready
→ stop
→ start
→ restart
→ remove
→ recreate identically
```

It also verifies that the qualification tunnel unit is manager-owned and does not invoke the legacy `workspace-mcp _run-tunnel` wrapper.

P6 MUST NOT be marked PASS until this host-side script succeeds.
