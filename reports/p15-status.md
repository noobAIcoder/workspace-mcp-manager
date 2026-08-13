# P15 — Toolkit and developer-tool provisioning status

Status: **PASS — LIVE WSL QUALIFIED**

Normative authority: `specs/p15-toolkit-and-developer-tools.md`.

P15 covers CAP-006 through CAP-010, CAP-121, CAP-122, and the P15 audit portion
of CAP-123. Persistent manager-owned agent activation remains post-MVP.

## Verification

```text
standalone installer focused tests: 5 PASS
developer-tool focused tests:       9 PASS
complete host-side suite:          190 PASS
bash syntax:                         PASS
git diff check:                      PASS
```

## WSL toolkit evidence

The real development manager was not switched to the new standalone layout.
The real-home read-only candidate check validated the existing manager
declaration successfully.

An isolated temporary home/prefix then passed:

```text
P15_REAL_TOOLKIT_CHECK=PASS
P15_TOOLKIT_INITIAL_INSTALL=PASS
P15_TOOLKIT_NOOP=PASS
P15_TOOLKIT_EXPLICIT_UPGRADE_GATE=PASS
P15_TOOLKIT_ATOMIC_SWITCH=PASS
```

The isolated exercise used distinct source fingerprints before/after the switch
and both installed manager entrypoints remained executable.

## Real developer-tool evidence

Corrected explicit-path inventory:

```text
uv                         0.12.3
coding-tools-mcp           present (uv tool 0.2.2)
tunnel-client              0.0.11+8d55683...
gh                         /usr/bin/gh 2.45.0

NVM v22.22.3:
  node                     v22.22.3
  npm                      11.16.0
  codex                    absent

NVM v24.15.0:
  node                     v24.15.0
  npm                      11.12.1
  codex                    codex-cli 0.147.0
```

Codex clean-baseline check:

```text
result=noop
codex_version=0.147.0
required_exec_path=/home/cloudtoor/.nvm/versions/node/v24.15.0/bin
required_external_root=/home/cloudtoor/.nvm/versions/node/v24.15.0
```

GitHub CLI clean-baseline check:

```text
result=noop
path=/usr/bin/gh
version=gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)
```

Agent audit:

```text
ssh-agent=true
gpg-agent=true
gpgconf=true
current SSH_AUTH_SOCK=/run/user/1000/gnupg/S.gpg-agent.ssh
current socket present=true
manager-owned agent resources=absent
```

No authentication, private-key, known-host, Codex-session, or GPG trust data was
managed by P15.

## Qualification regressions

### P15-Q1 — host-boundary envelope

The first live tooling audit rejected its own valid `_runtime/tools-*` transport
shape. The validator now checks the transport marker and tooling command in the
correct argv positions. Regression coverage was added.

### P15-Q2 — NVM probe environment

The first NVM audit found Node but could not version npm/Codex because those
scripts resolve `node` through the environment. NVM probes now execute with the
selected root's `bin` directory at the front of PATH. The live inventory above
is the corrected result.

## Live gate

```text
P15_TOOL_AUDIT_COMMAND=PASS
P15_TOOLING_STATE=PASS
P15_FINAL_CONVERGENCE=PASS
P15_WSL_QUALIFICATION=PASS
```

Final qualification authority:

```text
manager-qual fingerprint=728dcc4f4086ef9e98f856201741dc16c50bee9a9d0f491009661e94cba14701
access=[]
.workspace-mcp-access=absent
plan=all NOOP
MCP discovery=PASS
healthz=live
readyz=ready
P12 diagnose smoke=PASS
```

## Current gate

```text
P15_IMPLEMENTATION=PASS
P15_PURE_VERIFICATION=PASS
P15_LIVE_WSL_QUALIFICATION=PASS
P15_AGENT_PERSISTENT_ACTIVATION=POST_MVP
P16=NOT_STARTED
```
