# P12 — Resilience diagnostics

Status: **NORMATIVE P12 SPECIFICATION**

P12 implements CAP-072 through CAP-089 plus CAP-116. It is observational: the
diagnostic command may persist its own snapshot, but it MUST NOT restart, stop,
start, enable, disable, or otherwise mutate MCP/tunnel lifecycle state.

## Public command

```text
workspace-mcp-manager instance diagnose <instance-id> [--since-seconds N]
```

`--since-seconds` defines the recent-event classification window. Default:
`900`; allowed range: `1..86400`.

The command executes through the existing transient user-systemd host boundary
and persists one redacted snapshot under the real account state root:

```text
~/.local/state/workspace-mcp-manager/diagnostics/<instance-id>/<snapshot-id>.json
```

The public result returns the snapshot path and the bounded structured snapshot.

## Snapshot schema

Each snapshot records:

```text
schema_version
snapshot_id
captured_at
instance_id
desired_fingerprint
since_seconds
services.mcp
services.tunnel
cgroups
host
probes
recent_tunnel_events
classifications
incident_domains
```

The snapshot is diagnostic evidence, not desired/applied authority.

## Service and process evidence

MCP and tunnel MUST be captured separately. Each service snapshot includes at
least:

- unit name;
- load/active/sub states;
- service result / main exit status;
- main PID;
- restart count when available;
- control-group path;
- monotonic active/inactive transition timestamps when available.

For every PID currently listed in each unit cgroup, diagnostics SHOULD capture:

- PID;
- RSS from `/proc/<pid>/status`;
- PSS from `/proc/<pid>/smaps_rollup` when readable;
- process age derived from `/proc/<pid>/stat` + `/proc/uptime`;
- `/proc/<pid>/cgroup` membership.

Unavailable per-process fields are represented as `null`/bounded error metadata;
one disappearing process MUST NOT abort the whole snapshot.

## Cgroup evidence

For each MCP/tunnel cgroup, capture when available:

```text
cgroup.procs
memory.current
memory.peak
memory.max
memory.events
memory.events.local
```

The snapshot MUST explicitly report whether MCP and tunnel use distinct cgroups.
P12 does not merge their memory accounting.

## Host memory and PSI evidence

Capture `/proc/meminfo` as structured key/value evidence and capture Linux PSI
from:

```text
/proc/pressure/cpu
/proc/pressure/memory
/proc/pressure/io
```

PSI is evidence only unless another P12 classification rule below explicitly
uses it.

## Current probes

### MCP

Diagnostics MUST capture both:

1. discovery GET at `/.well-known/mcp.json`;
2. MCP protocol `initialize` POST.

If initialization creates `Mcp-Session-Id`, diagnostics MUST issue DELETE cleanup
for that session. The diagnostic probe itself MUST NOT leak sessions.

Record status, protocol version when parseable, and bounded/redacted error
metadata. Do not persist full arbitrary response bodies.

### Tunnel

Capture current HTTP status and bounded text for:

```text
/healthz
/readyz
```

## Time-aware tunnel-log evidence

The primary tunnel runtime log is structured JSON with RFC3339 `time`. Only
entries whose parsed `time` falls inside `[captured_at - since_seconds,
captured_at]` may trigger CAP-085/CAP-086 classifications.

Malformed JSON, missing timestamps, future timestamps beyond a small clock-skew
tolerance, and untimestamped supervisor output MUST NOT trigger a time-aware
tunnel incident classification.

Persist only bounded, redacted event summaries (`time`, `level`, `component`,
`msg`, bounded `error`) rather than the raw complete log entry.

## Classification contract

Classifications are independent labels. Multiple labels may be present. P12 MUST
NOT collapse local MCP incidents and tunnel/control-plane incidents into one
generic `503`/`timeout` class.

### `local_mcp_5xx`

Set when either current MCP discovery or current MCP protocol initialize returns
HTTP `500..599`.

This is a **local MCP** incident even when a dependent tunnel is also unhealthy.

### `tunnel_control_plane_5xx`

Set only when a recent timestamped structured tunnel entry belongs to the
control-plane path and contains a control-plane HTTP response/status code in
`500..599`.

A local MCP 5xx alone MUST NOT create this label.

### `tunnel_poll_timeout`

Set when a recent timestamped control-plane tunnel entry identifies poll timeout
behavior, including structured messages such as `poll timed out; backing off`
or equivalent poll request timeout/deadline errors.

Historical entries outside `--since-seconds` MUST NOT trigger this label.

### `mcp_process_down_or_restart`

Set when:

- MCP is not currently active/running or has no live main PID; or
- MCP's current active transition occurred inside the requested recent window.

The transition comparison SHOULD use systemd monotonic timestamps plus host
uptime rather than locale-dependent wall-clock text.

### `memory_pressure`

Set when either MCP/tunnel cgroup shows strong current/historical cgroup-v2
pressure evidence:

- `oom`, `oom_kill`, or `oom_group_kill` > 0; or
- finite `memory.max` with `memory.current >= 95%` of the limit.

Host PSI and other `memory.events` fields remain recorded evidence but do not by
themselves create this label.

## Incident domains

The snapshot reports independent domains, for example:

```json
{
  "local_mcp": ["local_mcp_5xx", "mcp_process_down_or_restart"],
  "tunnel_control_plane": ["tunnel_control_plane_5xx", "tunnel_poll_timeout"],
  "resource_pressure": ["memory_pressure"]
}
```

Empty domains are represented by empty arrays. This structure is the explicit
CAP-089 guarantee that the established local MCP incident and later
tunnel/control-plane incident remain distinguishable.

## Secret-redaction contract

Snapshots MUST pass through the manager's recursive redaction before persistence
and before public output.

Diagnostics MUST NOT persist:

- `CONTROL_PLANE_API_KEY` values;
- OpenAI/GitHub bearer/token values;
- Codex authentication/session material;
- SSH private-key material;
- arbitrary environment dumps.

Tunnel log excerpts and subprocess errors are bounded and redacted before being
inserted into the snapshot.

## Failure semantics

Diagnostics are best-effort observational snapshots. Missing optional `/proc`,
PSS, PSI, or cgroup fields are captured as unavailable evidence rather than
turning the entire command into an error.

The command itself fails only when the managed instance cannot be resolved or
the snapshot cannot be safely persisted.

## P12 qualification gate

Pure verification MUST cover:

1. snapshot persistence and recursive redaction;
2. separate MCP/tunnel service/cgroup/process evidence;
3. RSS/PSS/age/cgroup parsing;
4. meminfo and PSI parsing;
5. MCP initialize session cleanup;
6. exact current `local_mcp_5xx` classification;
7. time-window tunnel 5xx and poll-timeout classification;
8. stale tunnel events excluded;
9. MCP down/recent-restart classification;
10. memory-pressure classification;
11. incident-domain separation.

Live `manager-qual` qualification MUST additionally prove:

1. healthy snapshot persistence with distinct MCP/tunnel cgroups and live
   per-process RSS/PSS evidence;
2. `/proc/meminfo` and CPU/memory/IO PSI captured;
3. discovery + initialize + DELETE cleanup and live tunnel health/readiness;
4. actual retained timestamped tunnel poll-timeout history is classified with a
   sufficiently long lookback but excluded by a short current lookback;
5. controlled MCP session saturation produces `local_mcp_5xx` without requiring
   a tunnel/control-plane label;
6. diagnostic execution itself does not restart services;
7. snapshots contain no credential material;
8. P7–P11 surfaces remain healthy and final manager plan/apply is NOOP.

Final gate:

```text
P12_RESILIENCE_DIAGNOSTICS_QUALIFICATION=PASS
```
