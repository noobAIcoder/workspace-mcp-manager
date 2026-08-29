# P9 — Git and toolchain diagnostics

Status: **implementation target**

## Authority

P9 preserves:

```text
CAP-051  Git repository detection
CAP-052  Git working-tree status test
CAP-053  Git remote-access `ls-remote` test
CAP-054  Git remote probe uses real account home
CAP-055  Configurable MCP execution `PATH`
CAP-114  GitHub CLI auth diagnostic
CAP-115  Git identity diagnostic
```

P10 and later capabilities are out of scope.

## Public command

```text
workspace-mcp-manager instance git <instance-id>
```

The public command MUST execute through the existing transient user-systemd host
boundary. It MUST NOT perform Git or GitHub authentication changes.

## Execution environment

Every Git/GitHub diagnostic subprocess MUST use:

```text
HOME=<ManagerPaths.account_home>
PATH=<desired.mcp.exec_path>
GIT_TERMINAL_PROMPT=0
GH_CONFIG_DIR=<desired.github.config_dir>   # when configured
```

`HOME` MUST NOT be inherited from the synthetic MCP execution home. `PATH` MUST
be the same configured path rendered into the MCP service by P4/P7.

Secret-bearing environment variables are excluded through the existing
subprocess-environment sanitization contract.

## Git checks

The command MUST report, without mutating repository state:

1. Git executable resolution from the configured execution PATH;
2. whether `workspace_path` is a Git working tree;
3. working-tree cleanliness and dirty-entry count;
4. configured remote name, transport and host only;
5. `git ls-remote --exit-code <remote> HEAD` reachability;
6. Git `user.name` / `user.email` presence and non-secret values.

Remote selection is deterministic:

1. `origin`, when present;
2. otherwise the lexically first configured remote;
3. otherwise no remote.

An absent remote is a diagnostic warning. A configured remote that fails
`ls-remote` is a diagnostic failure.

## Remote failure classification

When `git ls-remote` fails, the manager MUST inspect only bounded, already-redacted
probe evidence internally and project a non-secret `reason_code` plus bounded
operator detail. Raw probe stdout/stderr MUST remain excluded from the result.

The classifier SHOULD distinguish at least:

```text
SSH_AUTHENTICATION_FAILED
SSH_HOST_KEY_VERIFICATION_FAILED
DNS_FAILED
NETWORK_TIMEOUT
CONNECTION_REFUSED
REMOTE_REPOSITORY_NOT_FOUND
HTTPS_AUTHENTICATION_FAILED
SSH_REMOTE_ACCESS_FAILED
REMOTE_ACCESS_FAILED
```

Classification MUST be deterministic and fail closed to the protocol-appropriate
generic remote-access reason when no specific signature is recognized.

## GitHub CLI check

When `gh` resolves from the configured PATH, run:

```text
gh auth status
```

The command output MUST be discarded. Only executable presence and the boolean
authentication result may be returned.

Missing `gh` or missing GitHub authentication is a warning because installation
and authentication onboarding remain outside P9 authority.

## Git identity semantics

P9 diagnoses Git identity but MUST NOT configure it. Missing `user.name` or
`user.email` is therefore a warning rather than a manager failure. CAP-119
remains deferred.

## Secret and remote metadata contract

The diagnostic result MUST NOT return:

- a remote URL;
- remote username/userinfo;
- repository path derived from the remote URL;
- `gh auth status` stdout/stderr;
- `git ls-remote` stdout/stderr;
- PAT/OAuth tokens, SSH key data, credential-helper output, or bearer material.

Remote metadata is limited to:

```text
name
transport
host
reachable
exit_code
reason_code
```

`reason_code` MUST contain only a manager-defined classification and MUST NOT
contain subprocess output or remote path/user information.

## Result semantics

The result exposes a `checks[]` array with `PASS`, `WARN`, or `FAIL` states.

`ok=false` when any check is `FAIL`.

A failed `git-remote-access` check MUST use the classified, bounded operator
detail rather than the generic `git ls-remote failed` text when classification
is available.

Expected warnings that do not fail the diagnostic:

- no remote configured;
- Git identity absent/incomplete;
- `gh` absent;
- GitHub CLI not authenticated.

## Live qualification target

Disposable instance:

```text
INSTANCE_ID=manager-qual
WORKSPACE=/home/cloudtoor/repos/workspace-mcp-manager-qual
```

Qualification MUST prove:

1. repository detection through the new command;
2. working-tree status through the new command;
3. a temporary non-secret remote can be probed with `ls-remote`;
4. reported HOME equals the real Unix account home;
5. reported PATH equals the configured MCP execution PATH;
6. GitHub CLI auth diagnostic reports the current external auth state without
   emitting token material;
7. Git identity diagnostic reports current state without configuration changes;
8. failed remote probes project reason codes without returning raw probe output;
9. real MCP `exec_command` resolves Git from the configured PATH;
10. P7/P8 no-access baseline and tunnel health remain intact after cleanup;
11. the full pure test suite remains green.

Final gate:

```text
P9_GIT_DIAGNOSTICS_QUALIFICATION=PASS
```
