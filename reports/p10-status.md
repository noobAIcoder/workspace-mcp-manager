# P10 — Codex lifecycle status

Status: **IMPLEMENTATION COMPLETE / LIVE QUALIFICATION BLOCKED**

P10 capability authority is `specs/p10-codex-jobs.md` and covers CAP-056 through
CAP-065 plus CAP-113.

Implementation checkpoint:

```text
803eb447e5c15343c3ad21d37a6d3b85690614f5
Implement P10 detached Codex lifecycle
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

## Live WSL prerequisite evidence

The intended standalone entrypoint currently resolves host-side to:

```text
/home/cloudtoor/.local/lib/codex-wsl/bin/codex
```

but its version preflight returns exit `127`:

```text
codex: WSL standalone binary is unavailable at
/home/cloudtoor/.codex/packages/standalone/current/bin/codex.
```

The P10 harness therefore exits before any manager desired-state mutation:

```text
P10_EXTERNAL_CODEX_BLOCKED=version-preflight
P10_HARNESS_EXIT=2
```

This is an external/P15 installation prerequisite, not a P10 implementation
failure. The manager MUST NOT silently substitute an npm, Windows, or other Codex
distribution to force the gate green.

## Current gate

```text
P10_IMPLEMENTATION=PASS
P10_PURE_VERIFICATION=PASS
P10_LIVE_WSL_QUALIFICATION=BLOCKED_EXTERNAL_CODEX
P11=NOT_STARTED
```

P10 can become fully PASS only after the intended standalone Codex payload is
restored and `scripts/qualify_p10_wsl.sh` completes the live read/write,
detach/status/output, cancellation, restart-persistence, and regression gates.
