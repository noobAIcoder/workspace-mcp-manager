# P6 — Apply lifecycle status

Status: **FINALIZATION IN PROGRESS — LIVE WSL QUALIFICATION REQUIRED**

Base implementation checkpoint:

```text
34d013b  Implement P6 apply lifecycle
```

P6 finalization consolidates subsequent live-qualification fixes before the gate
is allowed to pass.

## Implemented lifecycle

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
instance status
instance logs
```

The host worker performs:

- host-side `systemd-analyze --user verify` for generated units;
- a fresh authoritative reconciliation plan immediately before normal apply mutation;
- deterministic file/directory application;
- `systemctl --user daemon-reload`;
- enable/start/restart/stop/disable in dependency-safe order;
- readiness waits for MCP discovery plus tunnel `/healthz` and `/readyz`;
- non-secret per-transaction journals;
- mutation-attempt journal persistence before mutation execution;
- `applied.json` only after successful post-apply convergence;
- fail-safe rollback of generated resources and newly started/enabled units where feasible;
- current ownership/residual-content revalidation before destructive mutation;
- bounded/redacted transaction evidence on failures.

`start`, `stop`, `restart`, and `remove` update the desired lifecycle target inside
the host worker, so callers inside `coding-tools-mcp` do not need direct write
access to the real manager registry.

## Failed-first-apply recovery

The established failed `manager-qual` first apply may leave a manager-created
state directory with known runtime logs but no surviving `.owner` marker.

Recovery is allowed only when all of the following are true:

- no `applied.json` exists;
- the state directory has the expected type and mode;
- the owner marker is absent;
- a failed transaction proves successful creation of both the state directory
  and owner marker during the failed apply;
- every residual state entry is a recognized runtime log with the expected file
  type.

Recovery restores the missing ownership marker through a separately journaled
recovery plan and preserves the runtime logs. Unknown residue fails closed.

## NVM toolchain contract

An MCP instance may use an NVM-managed Node version without sourcing `nvm.sh`.
P6 qualification selects an explicit NVM version and requires:

```text
PATH includes:            ~/.nvm/versions/node/<version>/bin
exec read/execute root:   ~/.nvm/versions/node/<version>
```

The selected `bin` must expose `node`, `npm`, `npx`, `corepack`, and `pnpm`.
P6 proves host availability and generated service rendering; protocol execution
is deferred to P7.

## Qualification target

Disposable instance:

```text
INSTANCE_ID=manager-qual
WORKSPACE=/home/cloudtoor/repos/workspace-mcp-manager-qual
MCP=127.0.0.1:7657
TUNNEL_HEALTH=127.0.0.1:7373
TUNNEL_PROFILE=workspace-mcp-manager-qual
```

A current disposable tunnel ID MUST be supplied with `QUAL_TUNNEL_ID`. The
qualification MUST NOT silently reuse the historical example tunnel ID.

LeadBot, Vigilus, `manager`, and `wsl-reconcile` are not migration targets in P6.

## Live gate

Run:

```bash
QUAL_TUNNEL_ID=tunnel_... bash scripts/qualify_p6_wsl.sh
```

If the current shell's `node` is not NVM-managed, also provide:

```bash
QUAL_NVM_BIN="$HOME/.nvm/versions/node/<version>/bin"
```

The gate executes:

```text
plan
→ apply
→ status
→ NOOP apply
→ status
→ stop
→ status
→ start
→ status
→ restart
→ status
→ remove
→ status
→ manager update back to present+running
→ apply
→ status
```

The explicit `instance update` before recreate is required by the frozen domain
contract: `remove` sets desired deployment to `absent`, while `apply` never
changes desired state.

The script also verifies:

- MCP discovery, tunnel health, and tunnel readiness;
- generated tunnel service uses `workspace-mcp-manager`, never legacy
  `workspace-mcp _run-tunnel`;
- generated MCP service carries the NVM PATH and version-root admission;
- second apply performs no managed-resource/systemd mutation;
- state ownership and modes;
- remove preserves shared resources and does not change other managed instances;
- transaction journals and `applied.json` reflect successful convergence.

P6 MUST NOT be marked PASS until this host-side gate succeeds completely.
