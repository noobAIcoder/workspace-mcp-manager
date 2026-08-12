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

Read-only `instance plan` uses the same evidence requirements to project only this
specific residue into a valid recovery plan. It does not create the ownership
marker or delete logs. Generic missing/foreign ownership remains a conflict.

## Live qualification regressions

### P6-Q1 — pre-apply plan blocked proven recovery residue

Observed during live `manager-qual` qualification after pure tests were green:

```text
plan -> CONFLICT
state-dir -> ownership marker is missing
state-owner -> CREATE
no units deployed
no applied.json
known mcp.log + tunnel-supervisor.log remain
failed transaction evidence exists
```

Cause: recovery recognition existed only inside `apply`, while the required
qualification sequence starts with read-only `plan`. The preceding plan therefore
blocked the apply that knew how to recover the exact residue.

Correction:

- read-only planning recognizes only journal-proven failed-first-apply residue;
- the state-directory ownership conflict is projected to a recovery `NOOP`;
- creation of `.owner` remains visible in the plan;
- `apply` still performs the actual ownership restoration under the instance lock
  and recomputes a fresh normal plan before subsequent mutation;
- unknown residual content, missing failed-transaction evidence, or existing
  `applied.json` still fail closed;
- regression coverage lives in `tests/test_recovery_planning.py`.

### P6-Q2 — tunnel start raced MCP discovery readiness

Observed on the next live first-apply attempt after Q1 recovery succeeded:

```text
coding-tools-mcp-manager-qual.service -> systemd started
tunnel-client-manager-qual.service -> ExecStartPre curl connection refused
systemctl start tunnel -> failed
rollback -> PASS
no applied.json
```

Cause: `After=`/`Requires=` order systemd activation but do not prove that the
MCP HTTP listener is accepting discovery requests. The generated tunnel unit
performed a single immediate discovery `curl`, so a normal MCP startup delay was
misclassified as a hard tunnel-start failure.

The same incident also showed that a structured `ManagerError` produced by the
transient JSON worker exited process status `2`; `HostExecutionBridge` therefore
wrapped it as generic `host worker failed` and embedded the useful JSON in
escaped stderr.

Correction:

- generated tunnel `ExecStartPre` uses bounded `curl` retry-on-connection-refused
  semantics before launching the tunnel runtime;
- generated resource fingerprint evidence was updated;
- JSON-producing `_runtime` worker commands serialize `ManagerError` to stdout
  and return transport success so the public bridge preserves structured
  code/details and maps semantic `ok=false` normally;
- `_runtime tunnel` and `_runtime admission-guard` remain real systemd service
  processes and retain nonzero exits on failure;
- regression coverage lives in `tests/test_generation.py` and
  `tests/test_cli_host_boundary.py`.

### P6-Q3 — authority assertion mismatched quoted ExecStart

After Q2 was corrected, live qualification reached and passed first apply,
readiness, and status, then stopped at:

```text
== generated service authority and NVM toolchain ==
```

Cause: the harness searched for the contiguous text `_runtime tunnel`, while the
generated deterministic systemd command quotes every argument separately and
therefore renders `"_runtime" "tunnel"`.

Correction:

- the authority assertion now matches the actual quoted argument contract;
- assertion failures use explicit `fail` diagnostics rather than bare `grep`
  exits under `set -e`;
- the harness may resume after a harness-only failure only when `applied.json`
  proves `present+running` with owned resources and the expected state marker,
  units, and tunnel profile still exist;
- no manager-owned resources are deleted or normalized to resume;
- regression coverage lives in `tests/test_qualification_script.py`.

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
