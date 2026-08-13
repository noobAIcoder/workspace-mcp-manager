# P9 — Git and toolchain diagnostics status

Status: **PASS — LIVE WSL QUALIFIED 2026-08-13**

Implementation checkpoints:

```text
0a93e39  Implement P9 Git diagnostics
dd5487f  Fix P9 qualification manager path
```

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

P10 and later capabilities were not started.

## Implemented command

```text
workspace-mcp-manager instance git <instance-id>
```

The public command executes through the existing transient user-systemd host
boundary. The host worker sets:

```text
HOME=<real account home>
PATH=<desired.mcp.exec_path>
GIT_TERMINAL_PROMPT=0
GH_CONFIG_DIR=<desired.github.config_dir>  # when configured
```

The diagnostic is non-mutating. It does not perform `gh auth login`, configure
Git identity, alter credential helpers, or change Git protocol/URL rewrites.

## Result contract

The diagnostic reports:

- Git executable resolved from the configured PATH;
- Git working-tree detection;
- clean/dirty status and dirty-entry count;
- deterministic remote selection (`origin`, then lexical first);
- remote transport and host only;
- `git ls-remote --exit-code <remote> HEAD` reachability;
- Git `user.name` / `user.email` presence;
- GitHub CLI executable presence and authentication result;
- the effective real HOME, configured PATH, and configured GH config directory.

It does **not** return remote URLs, remote userinfo, repository path derived from
the remote URL, `ls-remote` output, `gh auth status` output, tokens, SSH key data,
or credential-helper output.

Missing remote, Git identity, `gh`, or GitHub auth are diagnostic warnings when
the underlying operation is externally managed/deferred. A configured remote
that fails `ls-remote`, a missing Git executable, or a non-repository workspace
is a failure.

## Pure verification

Final source verification before live qualification:

```text
Ran 104 tests in 0.607s
OK (skipped=1)

bash -n scripts/qualify_p9_wsl.sh
PASS

git diff --check
PASS
```

The single skip is the pre-existing MCP-sandbox limitation for direct
`systemd-analyze --user verify`; host-side lifecycle verification remains covered
elsewhere.

Regression coverage includes:

- real account HOME propagation into every Git/GitHub probe;
- declared MCP execution PATH propagation;
- GH config propagation;
- deterministic remote selection and reachability failure semantics;
- credential/userinfo/path suppression from remote metadata;
- missing remote/identity/auth warning semantics;
- public CLI and host-boundary dispatch;
- qualification harness cleanup and non-secret assertions.

## Live host qualification

The disposable target was:

```text
INSTANCE_ID=manager-qual
WORKSPACE=/home/cloudtoor/repos/workspace-mcp-manager-qual
```

The qualification harness temporarily added a non-secret remote named
`p9-qual` pointing at the public manager repository, executed the new diagnostic,
validated the result, then removed the remote.

Live result:

```text
P9_HOST_QUALIFICATION=PASS
```

That gate proved:

- repository detection PASS;
- working-tree status PASS and clean;
- temporary remote detected;
- `git ls-remote ... HEAD` PASS;
- remote metadata limited to `transport=https`, `host=github.com`;
- diagnostic HOME equals `/home/cloudtoor`;
- diagnostic PATH equals the declared MCP execution PATH;
- `gh` present and authenticated through the configured `GH_CONFIG_DIR`;
- Git identity diagnostic executed successfully;
- no remote URL/token-bearing field exists in the public result;
- temporary remote cleanup leaves the working tree clean.

After the temporary remote was removed, the normal no-remote state correctly
reports `git-remote-access=WARN` while keeping the overall diagnostic successful.

## Live MCP execution-path qualification

The actual `ManagerQual` MCP process reported:

```text
PATH=/home/cloudtoor/.nvm/versions/node/v24.15.0/bin:/home/cloudtoor/.local/bin:/usr/local/bin:/usr/bin:/bin
HOME=/tmp/coding-tools-mcp/.../home
git=/usr/bin/git
git version 2.43.0
gh=/usr/bin/gh
gh_auth_exit=0
```

The synthetic MCP HOME is expected. The P9 manager diagnostic independently
reported:

```text
home=/home/cloudtoor
gh_config_dir=/home/cloudtoor/.config/gh
repository.present=true
working_tree.clean=true
github_cli.authenticated=true
identity.name_present=true
identity.email_present=true
```

This distinction is the live proof for CAP-054: Git remote/auth diagnostics do
not accidentally inherit the sandbox HOME.

## Live qualification regression — P9-Q1

The first host harness invocation failed before the new diagnostic ran:

```text
workspace-mcp-manager: command not found
```

Cause: transient user-systemd does not guarantee `~/.local/bin` in its default
PATH, while the installed manager executable lives there.

Correction:

- the harness resolves an explicit `MANAGER_BIN`, defaulting to
  `$HOME/.local/bin/workspace-mcp-manager`;
- the executable is checked before qualification;
- static regression coverage asserts use of that explicit path;
- the failed run's cleanup trap removed the temporary Git remote.

This was a qualification-harness defect, not a Git diagnostic implementation
failure.

## Cleanup / final baseline

The temporary development-only RW source bind is removed after the documentation
commit. Final qualification requires:

```text
access entries:        []
access root:           absent
temporary Git remote:  absent
qualification Git:     clean
manager-qual runtime:  active
tunnel /healthz:       live
tunnel /readyz:        ready
plan:                  NOOP
```

Final gate:

```text
P9_GIT_DIAGNOSTICS_QUALIFICATION=PASS
```
