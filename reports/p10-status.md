# P10 — Codex lifecycle status

Status: **PASS / LIVE WSL QUALIFIED 2026-08-13**

P10 capability authority is `specs/p10-codex-jobs.md` and covers CAP-056 through
CAP-065 plus CAP-113.

Implementation checkpoint:

```text
803eb447e5c15343c3ad21d37a6d3b85690614f5
Implement P10 detached Codex lifecycle
```

Qualification-authority re-baseline:

```text
1931387de4bc665ecc7bbbf2fd8dc9398899818c
Rebaseline P10 Codex installation authority
```

## Implemented behavior

The manager now provides:

```text
workspace-mcp-manager codex start <instance> --mode read|write (--prompt <text> | --prompt-stdin)
workspace-mcp-manager codex status <instance> <job-id>
workspace-mcp-manager codex output <instance> <job-id> [--limit-bytes N]
workspace-mcp-manager codex cancel <instance> <job-id>
```

Implemented semantics:

- Codex resolution only from declared `mcp.exec_path`;
- `codex --version` preflight before job creation;
- exact `read -> read-only` and `write -> workspace-write` mapping;
- prompt transport to the host boundary over stdin and exclusion from transient
  systemd argv;
- detached `systemd-run --user --no-block --collect` worker;
- real account `HOME`, real `CODEX_HOME`, declared PATH, and optional
  `GH_CONFIG_DIR` worker environment;
- manager/tunnel/GitHub bearer environment filtering;
- private per-instance durable state under:

  ```text
  ~/.local/state/workspace-mcp-manager/instances/<instance>/codex/<job-id>/
  ```

- durable `request.json`, `status.json`, `stdout.log`, `stderr.log`, and terminal
  `result.json`;
- request digest, instance/workspace/sandbox and execution-authority
  revalidation;
- structured transient-unit inspection;
- bounded activation grace after `--no-block` submission;
- persisted `interrupted` result when a nonterminal job actually loses its
  detached unit;
- bounded stdout/stderr tails;
- control-group cancellation and terminal cancellation evidence;
- idempotent handling of already terminal jobs.

P10 deliberately does not install/upgrade/relink Codex or persist Codex
authentication/session material. CAP-121 remains P15 and CAP-127 remains
external.

## Pure verification

Final host-side verification on WSL:

```text
git diff --check                         PASS
bash -n scripts/qualify_p10_wsl.sh      PASS
Ran 119 tests in 1.289s
OK
```

The test suite covers configured-PATH resolution, broken-executable fail-closed
behavior before job creation, stdin prompt transport, prompt exclusion from the
transient unit argv, no-block detached launch, persisted authority/state,
read/write sandbox mapping, real-home execution environment, secret filtering,
binding/tamper rejection, activating-unit handling, interrupted reconciliation,
bounded separate output streams, cancellation, CLI transport, and the WSL
qualification harness contract.

## Live WSL Codex authority

The WSL Codex installation was re-baselined to the executable selected by the
instance's declared MCP execution PATH rather than a hard-coded installation
mechanism. Live `manager-qual` resolves:

```text
PATH=/home/cloudtoor/.nvm/versions/node/v24.15.0/bin:/home/cloudtoor/.local/bin:/usr/local/bin:/usr/bin:/bin
codex=/home/cloudtoor/.nvm/versions/node/v24.15.0/bin/codex
codex-cli 0.147.0
```

The non-system Codex installation is covered by the already-declared external
root:

```text
/home/cloudtoor/.nvm/versions/node/v24.15.0
```

Real MCP `exec_command` resolved the same executable and `codex --version`
succeeded. The MCP sandbox emitted non-fatal warnings about writes under
`CODEX_HOME`; P10 detached workers execute through the host user-systemd
boundary with real account `HOME`/`CODEX_HOME`, as specified.

## Live detached-job qualification

The configured-PATH qualification harness ran against disposable
`manager-qual`. Its initiating MCP request was lost when the harness deliberately
restarted `manager-qual`; the host-side qualification and detached job state
survived independently. Persisted evidence after reconnect showed:

### Read job

```text
job_id=20260813T085834Z-c66b93cf533f
mode=read
state=succeeded
exit_code=0
last_message=P10_READ_OK
```

### Write job

```text
job_id=20260813T085852Z-81b4181340bb
mode=write
state=succeeded
exit_code=0
last_message=P10_WRITE_OK
```

The write job created the requested disposable `.p10-codex-write-check`; the
harness verified its exact content and removed it. Post-qualification evidence
confirmed the file is absent.

### Cancellation job

```text
job_id=20260813T085916Z-dae3de3a548e
mode=read
state=cancelled
exit_code=null
```

Submission returned before the long-running worker completed, and cancellation
persisted terminal `cancelled` status/result evidence.

After the deliberate instance restart, the completed read job remained
queryable and authoritative. This qualifies detached persistence independently
of the initiating MCP request and persistence across MCP/tunnel restart.

## Final convergence

After qualification and removal of the temporary source-development access:

```text
desired_fingerprint=728dcc4f4086ef9e98f856201741dc16c50bee9a9d0f491009661e94cba14701
access.read_only=[]
access.read_write=[]
.workspace-mcp-access=absent
plan=NOOP
coding-tools-mcp-manager-qual.service=active/running
tunnel-client-manager-qual.service=active/running
MCP discovery=PASS
healthz=live
readyz=ready
```

The live harness preserves the desired-state fingerprint and performs no Codex
installation or authentication changes.

## Current gate

```text
P10_IMPLEMENTATION=PASS
P10_PURE_VERIFICATION=PASS
P10_LIVE_WSL_QUALIFICATION=PASS
P10_WSL_QUALIFICATION=PASS
P11=UNBLOCKED
```
