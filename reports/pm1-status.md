# PM1 — Per-instance development environment status

Status: **PASS — LIVE WSL QUALIFIED**

Normative authority: `specs/pm1-development-environment.md`.

PM1 implements the first post-MVP capability group:

```text
CAP-117  per-instance GH_CONFIG_DIR
CAP-119  repository-local Git identity
CAP-120  repository Git transport / GitHub CLI helper
CAP-123  persistent agent-provider remainder
```

Authentication remains external. PM1 does not accept or manage GitHub tokens,
SSH/GPG private keys, passphrases, known-host databases, Codex sessions, or GPG
trust/key databases.

## Implemented authority

- declaration schema v2 with v1 parsing/serialization compatibility;
- one-way v1 -> v2 registry migration;
- managed, external, and disabled GitHub profile modes;
- retained manager-owned per-instance GitHub profile directory/marker;
- repository-local identity adoption/update/release with reversible ownership
  metadata;
- GitHub SSH and HTTPS+GitHub-CLI transport modes;
- repository-local `core.sshCommand` with explicit `IdentityAgent` for SSH;
- manager-owned GitHub CLI helper for HTTPS mode;
- managed per-instance `ssh-agent` systemd service and stable runtime socket;
- explicit external-agent Unix-socket mode;
- MCP environment propagation for GitHub profile and SSH-agent socket;
- ownership/fingerprint-gated stale helper/agent cleanup;
- `.git/config` backup and exact P6 transaction rollback on apply failure;
- Git diagnostics use the same effective development environment.

## Pure verification

Focused PM1 operational verification:

```text
11 PASS
```

Complete regression suite:

```text
214 PASS
1 skipped
```

The skip is the existing generated-unit `systemd-analyze verify` case blocked by
the current MCP sandbox. The live qualification harness verifies real user
systemd behavior on WSL.

The pure PM1 suite covers real temporary Git repositories, exact identity and
transport adoption, protocol switching, reversible cleanup, external Unix
socket validation, schema downgrade refusal, applied-evidence stale cleanup,
`STOP -> DISABLE -> REMOVE` agent ordering, and exact `.git/config` rollback.

Qualification harness syntax:

```text
PM1_SCRIPT_SYNTAX=PASS
```

## Live WSL qualification

Harness:

```text
bash scripts/qualify_pm1_wsl.sh
```

The harness is scoped to disposable `manager-qual`. It snapshots the exact v1
declaration and repository-local Git config, temporarily exercises:

1. v2 managed GitHub profile + managed SSH agent + SSH transport;
2. HTTPS+GitHub-CLI helper transport;
3. return to SSH with stale-helper cleanup;
4. neutral v2 state with Git ownership release and stale-agent cleanup;
5. exact v1 declaration/Git-config restoration and final all-NOOP convergence.

It never performs GitHub login or key loading. A newly created qualification
GitHub profile is removed only if it contains exactly the manager ownership
marker and no other content.

Expected final gates:

```text
PM1_MANAGED_GITHUB_PROFILE=PASS
PM1_GIT_IDENTITY=PASS
PM1_SSH_AGENT=PASS
PM1_HTTPS_HELPER=PASS
PM1_REVERSIBLE_CLEANUP=PASS
PM1_PURE_SUITE=PASS
PM1_DEVELOPMENT_ENVIRONMENT_QUALIFICATION=PASS
```

Observed live gates on `manager-qual`:

```text
PM1_MANAGED_GITHUB_PROFILE=PASS
PM1_GIT_IDENTITY=PASS
PM1_SSH_AGENT=PASS
PM1_HTTPS_HELPER=PASS
PM1_REVERSIBLE_CLEANUP=PASS
PM1_PURE_SUITE=PASS
PM1_DEVELOPMENT_ENVIRONMENT_QUALIFICATION=PASS
```

Final restored authority:

```text
config_version=1
desired_fingerprint=728dcc4f4086ef9e98f856201741dc16c50bee9a9d0f491009661e94cba14701
plan=all NOOP
coding-tools-mcp-manager-qual.service=active
tunnel-client-manager-qual.service=active
readyz=ready
PM1 Git ownership metadata=absent
PM1 qualification remote=absent
```

### PM1-Q1 — raw Git URL versus rewrite-expanded URL

The first live run failed its HTTPS assertion even though the PM1 transaction
converged. `manager-qual` inherits a path-specific Git rewrite from the real
account configuration that rewrites `https://github.com/` to SSH when Git
resolves a remote URL. PM1 correctly owns the raw repository-local
`remote.<name>.url` key, so qualification and pure tests now assert that raw
local key rather than `git remote get-url`.

### PM1-Q2 — MCP restart must restart dependent tunnel

The first cleanup restore exposed a generic lifecycle gap: restarting an MCP
unit causes systemd to stop the tunnel that `Requires=` it, but the planner did
not explicitly restart that still-desired tunnel. Planning now schedules a
tunnel restart whenever an active tunnel depends on an MCP unit whose generated
resource is changing. Regression coverage was added to the existing active-unit
update test. The disposable instance was recovered through normal manager apply
before qualification continued.

### PM1-Q3 — host-worker cwd independence

The live harness final pure-suite gate runs from a transient user-systemd worker,
not necessarily from the repository root. The PM1 qualification-script test now
resolves the harness relative to its own file, and the harness includes the
repository root in `PYTHONPATH`. The exact host-worker suite passes 214 tests.

## Current gate

```text
PM1_SPECIFICATION=PASS
PM1_IMPLEMENTATION=PASS
PM1_PURE_VERIFICATION=PASS
PM1_LIVE_WSL_QUALIFICATION=PASS
```
