# P12 — Resilience diagnostics status

Status: **PASS — LIVE WSL QUALIFIED 2026-08-13**

P12 authority is `specs/p12-resilience-diagnostics.md` and covers CAP-072 through
CAP-089 plus CAP-116.

Implementation checkpoints:

```text
b7f69f1  Implement P12 resilience diagnostics
c83f49c  Add P12 live qualification harness
```

P13 was not started.

## Implemented command

```text
workspace-mcp-manager instance diagnose <instance-id> [--since-seconds N]
```

Default lookback is 900 seconds; accepted range is 1..86400 seconds. The public
command executes through the existing user-systemd host boundary and persists a
redacted snapshot under:

```text
~/.local/state/workspace-mcp-manager/diagnostics/<instance>/<snapshot-id>.json
```

Diagnostics are observational. Apart from their own snapshot persistence, they
perform no manager lifecycle mutation.

## Implemented evidence

Each snapshot records separate MCP/tunnel service state including main PID,
result/exit status, restart count, control group, and monotonic transition
timestamps.

Each unit cgroup is captured separately with:

```text
cgroup.procs
memory.current
memory.peak
memory.max
memory.events
memory.events.local
```

For cgroup processes P12 captures when readable:

```text
RSS
PSS
process age
/proc/<pid>/cgroup membership
```

Host evidence includes structured `/proc/meminfo` and PSI from CPU, memory and
IO.

Current probes include:

- MCP discovery;
- MCP protocol `initialize`;
- DELETE cleanup for every diagnostic session created by initialize;
- tunnel `/healthz`;
- tunnel `/readyz`.

## Time-aware tunnel evidence

The tunnel's primary runtime log is structured JSON with RFC3339 `time`.
CAP-116 classification uses only entries whose parsed timestamp falls inside the
requested lookback. Untimestamped supervisor output cannot create a time-aware
tunnel classification.

The live host retained actual control-plane poll-timeout history, including:

```text
2026-08-13T10:05:40.070372+00:00  poll timed out; backing off  i/o timeout
2026-08-13T10:06:15.272137+00:00  poll timed out; backing off  context deadline exceeded
```

A long-window diagnostic classified this retained history. A current 60-second
diagnostic did not include those stale timestamps. No synthetic tunnel error was
written to production logs for P12 qualification.

## Classification contract

Implemented independent labels:

```text
local_mcp_5xx
tunnel_control_plane_5xx
tunnel_poll_timeout
mcp_process_down_or_restart
memory_pressure
```

Public snapshots group labels by independent domains:

```text
local_mcp
tunnel_control_plane
resource_pressure
```

This prevents the two established incident types from being conflated:

1. local MCP failure/5xx;
2. tunnel/control-plane failure/timeout.

`local_mcp_5xx` is driven by current local MCP probe status. Tunnel 5xx and poll
timeout labels require recent timestamped structured control-plane log evidence.

`memory_pressure` is driven by strong cgroup evidence (`oom*` events or finite
memory limit >=95% consumed); host PSI remains recorded supporting evidence.

## Pure verification

Final host-side suite:

```text
Ran 142 tests in 1.767s
OK
```

Additional gates:

```text
bash -n scripts/qualify_p12_wsl.sh   PASS
git diff --check                     PASS
```

P12 tests cover:

- meminfo and PSI parsing;
- structured control-plane 5xx recognition;
- poll-timeout recognition;
- stale/future timestamp exclusion;
- independent incident domains;
- recent MCP restart classification;
- memory pressure classification;
- cgroup/process RSS/PSS/age/membership;
- MCP initialize DELETE cleanup;
- snapshot persistence and recursive secret redaction;
- public CLI parsing and host-boundary routing;
- qualification-harness contracts.

## Live healthy short-window snapshot

Qualification snapshot:

```text
20260813T104748Z-9358c29e238f.json
captured_at=2026-08-13T10:47:48.520835+00:00
since_seconds=60
classifications=[]
```

Service/cgroup evidence:

```text
MCP:
  MainPID=149981
  ActiveState=active
  SubState=running
  RSS=28444 kB
  PSS=17863 kB

tunnel:
  MainPID=149992
  ActiveState=active
  SubState=running
  RSS=29408 kB
  PSS=16097 kB

cgroups_separate=true
```

The snapshot also captured process age/cgroup membership, 54 parsed meminfo
entries, and available CPU/memory/IO PSI.

Current probe evidence:

```text
MCP discovery:    HTTP 200 / protocol 2025-11-25
MCP initialize:   HTTP 200 / protocol 2025-11-25
session created:  true
session DELETE:   HTTP 200 / PASS
tunnel health:    HTTP 200 / live
tunnel readiness: HTTP 200 / ready
```

## Live retained-history snapshot

Qualification snapshot:

```text
20260813T104749Z-ae8b7480e760.json
captured_at=2026-08-13T10:47:49.086416+00:00
since_seconds=40000
```

Classifications:

```text
mcp_process_down_or_restart
tunnel_poll_timeout
```

Domains:

```text
local_mcp:            [mcp_process_down_or_restart]
tunnel_control_plane: [tunnel_poll_timeout]
resource_pressure:    []
```

The wide lookback intentionally includes the current MCP service start, so the
recent-restart label is expected under that window. The tunnel label is derived
from actual retained timestamped poll-timeout entries; it is not inferred from
the MCP label.

## Live local-MCP saturation snapshot

The qualifier opened MCP initialize sessions until the exact admission limit was
reached:

```text
P12_LOCAL_SATURATION=PASS sessions=125
```

It then invoked `instance diagnose` while the session table was full.

Qualification snapshot:

```text
20260813T104750Z-abb23325e369.json
captured_at=2026-08-13T10:47:50.078856+00:00
since_seconds=60
```

Classification:

```text
classifications=[local_mcp_5xx]

local_mcp:            [local_mcp_5xx]
tunnel_control_plane: []
resource_pressure:    []
```

Probe evidence:

```text
MCP discovery:  HTTP 200
MCP initialize: HTTP 503
tunnel health:  HTTP 200 / live
tunnel ready:   HTTP 200 / ready
```

The service PIDs were unchanged from the healthy snapshot:

```text
MCP MainPID=149981
tunnel MainPID=149992
```

Therefore `instance diagnose` did not restart either service. Held sessions were
deleted by the qualification cleanup and normal readiness remained intact.

This is the direct live CAP-089 proof that a local MCP 5xx is represented as a
local incident rather than being merged with tunnel/control-plane evidence.

## Snapshot persistence and redaction

Final persistence audit:

```text
diagnostic instance directory mode: 0700
qualification snapshot modes:       0600
credential-pattern matches:          false
```

The tested patterns include non-redacted CONTROL_PLANE/OpenAI/GitHub assignment
values and bearer-token shapes. Snapshots are passed through the manager's
recursive redaction before persistence and public output.

## Live result

Detached qualification unit:

```text
workspace-mcp-manager-p12-qualification-r1.service
Result=success
ExecMainStatus=0

P12_LOCAL_SATURATION=PASS sessions=125
P12_RESILIENCE_DIAGNOSTICS_QUALIFICATION=PASS
```

Final live convergence after qualification:

```text
MCP:     active/running
tunnel:  active/running
plan:    all NOOP
MCP discovery: PASS
healthz: live
readyz:  ready
```

Source checkpoint at live qualification:

```text
HEAD=c83f49c43119bbab5638f8a4d0e92c5d99e6eca3
origin/main=c83f49c43119bbab5638f8a4d0e92c5d99e6eca3
```

Final gate:

```text
P12_RESILIENCE_DIAGNOSTICS_QUALIFICATION=PASS
```
