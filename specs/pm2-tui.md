# PM2 — Terminal frontend

Status: **NORMATIVE POST-MVP SPECIFICATION**

PM2 adds the interactive terminal frontend originally separated from the
deterministic manager control plane:

```text
workspace-mcp-manager      = authoritative lifecycle/control plane
workspace-mcp-manager-tui  = interactive frontend over public manager commands
```

PM2 MUST NOT introduce a second registry, lifecycle implementation, systemd
mutation path, secret store, or private `_runtime` dependency.

## Architecture

The TUI MUST be implemented with the Python standard library only. The initial
frontend uses `curses` and therefore adds no runtime dependency beyond the
existing Python installation.

All manager reads and mutations MUST be issued through the public
`workspace-mcp-manager` executable. The TUI MUST NOT:

- import host-apply internals to mutate state;
- invoke `systemctl`, `systemd-run`, Git mutation commands, or tunnel binaries;
- write manager registry files directly;
- invoke private `_runtime` commands;
- accept API keys, PATs, private keys, passphrases, or Codex authentication
  material.

Temporary declaration files used for public `instance validate/create/update`
are frontend transport only and MUST be deleted after use.

## Installation

PM2 adds the public entrypoint:

```text
workspace-mcp-manager-tui
```

It MUST be installed by both:

1. normal Python/project installation through `pyproject.toml`;
2. the P15 standalone atomic toolkit installer.

The toolkit stable link is:

```text
~/.local/bin/workspace-mcp-manager-tui
-> ../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager-tui
```

The release wrapper MUST resolve the TUI from the same atomic `current`
release as the manager CLI and reboot bridge.

## Manager command client

The frontend command client MUST:

- execute an explicit argv vector with no shell;
- prepend `--registry-dir` only when the operator supplied an override;
- parse structured JSON from stdout or stderr;
- preserve structured manager errors for display;
- bound captured stdout/stderr;
- treat the manager executable as the only control-plane mutation boundary.

Public command families used by PM2 are limited to:

```text
host inspect|doctor|components|tools audit
instance list|show|validate|create|update
instance plan|status|logs|git|diagnose
instance apply|start|stop|restart|remove
access list|add-ro|add-rw|remove
```

Codex job control, legacy cleanup execution, reboot requests, and developer-tool
installation are not exposed by the first PM2 UI. Their existing CLI authority
remains unchanged and they MAY be added to a later frontend phase.

## Screens

### Dashboard

The dashboard MUST show every declared instance in lexical manager order with:

```text
instance ID
config version
desired deployment/runtime
```

It provides navigation to instance detail and host overview. Refresh MUST be
read-only.

### Instance detail

For the selected instance the detail screen MUST show at least:

```text
workspace path
MCP endpoint / permission mode
tunnel profile / health endpoint
desired lifecycle
GitHub mode when v2
Git remote protocol when v2
agent mode when v2
```

The screen exposes public manager views for plan, status, logs, Git diagnostics,
and resilience diagnostics.

Lifecycle actions are thin public CLI calls:

```text
apply
start
stop
restart
remove
```

`remove` MUST require a bounded in-TUI confirmation because it changes desired
deployment state. The confirmation does not alter manager semantics.

### Access management

The access screen MUST support:

```text
list
add-ro <alias> <absolute-path>
add-rw <alias> <absolute-path>
remove <alias>
```

No direct mountpoint or systemd mutation is permitted.

### Declaration editor

The TUI MUST provide a structured editor for common non-secret declaration
fields, including:

- workspace path;
- MCP host/port, permission mode, shell inheritance, execution PATH and
  external roots;
- tunnel ID/profile/health endpoint/env-file path;
- recovery policy;
- v2 GitHub mode/config directory/binary;
- v2 Git identity and remote objects;
- v2 agent object.

Access arrays are managed by the dedicated access screen rather than the
declaration editor.

The editor MUST support a deterministic v1 -> v2 projection matching PM1:

- existing v1 `github.config_dir` becomes `github.mode=external`;
- absent v1 GitHub config becomes `github.mode=disabled`;
- `git.identity=null`;
- `git.remote=null`;
- `agent.mode=none`.

The editor MUST NOT offer a v2 -> v1 downgrade.

Saving MUST call public `instance validate` followed by public `instance update`
or `instance create`. Validation failure leaves the authoritative declaration
unchanged.

### Create-from-template

PM2 MAY create a new declaration by cloning a selected existing declaration as
a non-secret template. Before save the operator MUST provide a new instance ID,
workspace path, MCP port, tunnel-health port, tunnel ID and tunnel profile.
Normal manager validation/reconciliation remains authoritative for collisions.

PM2 does not invent host/tool paths on an empty registry; clean-host bootstrap
continues to use the existing manager/toolkit authority.

## Host overview

The host screen exposes read-only results from:

```text
host inspect
host doctor
host components
host tools audit
```

Authentication state MAY be displayed only through existing redacted manager
diagnostics. The TUI MUST NOT implement `gh auth login`, token entry, key import,
or other secret onboarding.

## Terminal behavior

Interactive mode MUST use `curses.wrapper` so terminal modes are restored on
normal exit or Python exception.

The TUI MUST tolerate small terminals by clipping content instead of raising a
curses drawing exception. `q` always returns toward the previous screen and
exits from the dashboard.

For deterministic automation PM2 also provides:

```text
workspace-mcp-manager-tui --snapshot
workspace-mcp-manager-tui --snapshot --instance <id>
workspace-mcp-manager-tui --smoke
```

`--snapshot` MUST be non-interactive and emit bounded plain text. `--smoke`
initializes/draws the curses dashboard once and exits without mutation.

## Failure conditions

PM2 MUST fail closed when:

- the manager executable cannot be started;
- manager output is not structured JSON for a JSON command;
- declaration validation fails;
- a requested create/update is rejected by manager authority;
- curses is unavailable in interactive/smoke mode.

Frontend failures MUST NOT mutate registry or host resources except through a
manager command that already completed successfully.

## Qualification

Pure verification MUST cover:

1. manager argv construction and registry override;
2. JSON/error parsing;
3. dashboard snapshot rendering;
4. v1 -> v2 editor projection;
5. declaration temporary-file validation/update/create workflow;
6. access/lifecycle command mappings;
7. no `_runtime`, `systemctl`, shell execution, or secret-entry surface;
8. curses drawing helpers tolerate clipping/small dimensions;
9. toolkit installation publishes the TUI stable entrypoint;
10. full P6-PM1 regression suite.

Live WSL qualification on `manager-qual` MUST prove:

1. non-interactive dashboard snapshot lists `manager-qual`;
2. instance snapshot resolves `manager-qual` detail through public commands;
3. a pseudo-TTY `--smoke` curses run exits 0;
4. read-only TUI qualification leaves the declaration fingerprint unchanged;
5. final manager plan is all `NOOP`;
6. MCP discovery and tunnel health/readiness remain healthy;
7. complete pure suite remains green.

Final gate:

```text
PM2_TUI_QUALIFICATION=PASS
```
