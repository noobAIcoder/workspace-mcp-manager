# PM2 — Terminal frontend status

Status: **PASS — LIVE WSL QUALIFIED**

Normative authority: `specs/pm2-tui.md`.

PM2 implements `workspace-mcp-manager-tui` as a standard-library curses
frontend over the already-qualified manager CLI/control plane.

## Implemented authority

- public `workspace-mcp-manager-tui` Python/project entrypoint;
- atomic P15 toolkit entrypoint and stable symlink;
- explicit argv/JSON manager command client with no shell execution;
- dashboard and instance-detail screens;
- host inspect/doctor/components/tool-audit views;
- plan/status/log/Git/resilience views;
- apply/start/stop/restart/remove lifecycle delegation;
- RO/RW access list/add/remove delegation;
- structured declaration editor for MCP/tunnel/recovery and PM1 fields;
- deterministic v1 -> v2 declaration projection;
- create-from-selected-template workflow;
- public validate-before-create/update temporary declaration transport;
- non-interactive dashboard/instance snapshots;
- pseudo-TTY `--smoke` curses mode;
- clipping/small-terminal-safe drawing helpers.

PM2 has no private `_runtime`, direct systemd, direct Git mutation, registry
write, or secret-onboarding path.

## Pure verification

Focused PM2/toolkit verification:

```text
17 PASS
```

Complete regression suite:

```text
226 PASS
1 skipped
```

The skip is the existing generated-unit `systemd-analyze verify` case blocked
by the current MCP sandbox; PM2 live qualification separately exercised a real
pseudo-TTY and real user environment.

## Live WSL qualification

Harness:

```text
bash scripts/qualify_pm2_wsl.sh
```

The qualification ran through a transient user-systemd host worker so the TUI
used the real account HOME and manager executable. It was read-only against
`manager-qual` and used only an isolated temporary prefix for toolkit install.

Observed gates:

```text
PM2_SNAPSHOT=PASS
PM2_INSTANCE_DETAIL=PASS
PM2_CURSES_SMOKE=PASS
PM2_TOOLKIT_ENTRYPOINT=PASS
PM2_BASELINE_RESTORED=PASS
PM2_PURE_SUITE=PASS
PM2_TUI_QUALIFICATION=PASS
```

Final authority:

```text
manager-qual declaration fingerprint unchanged
manager-qual plan=all NOOP
MCP discovery=PASS
healthz=live
readyz=ready
```

## Current gate

```text
PM2_SPECIFICATION=PASS
PM2_IMPLEMENTATION=PASS
PM2_PURE_VERIFICATION=PASS
PM2_LIVE_WSL_QUALIFICATION=PASS
```
