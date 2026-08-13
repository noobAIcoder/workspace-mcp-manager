# P11 — Exact MCP admission recovery

Status: **NORMATIVE P11 SPECIFICATION**

P11 implements CAP-090 through CAP-094. Its purpose is intentionally narrow:
recover a local `coding-tools-mcp` instance only from the proven session-limit
saturation failure and never from generic MCP, tunnel, network, or host errors.

## Capability authority

```text
CAP-090  Exact MCP saturation signature `503 maximum HTTP session count reached`
CAP-091  30-second admission guard
CAP-092  120-second recovery cooldown
CAP-093  Restart only on exact saturation signature
CAP-094  Persisted admission-recovery evidence
```

P11 MUST NOT implement P12 incident classification or P13 reboot behavior.

## Generated runtime

When `recovery.admission_guard_enabled=true`, the existing generated resources
remain authoritative:

```text
workspace-mcp-admission-guard-<instance>.service
workspace-mcp-admission-guard-<instance>.timer
```

The timer cadence is taken from `recovery.guard_interval_seconds`. The qualified
preserved WSL configuration is exactly 30 seconds. The timer runs the guard as a
oneshot service and remains separate from the MCP and tunnel services.

`recovery.recovery_cooldown_seconds` is the cooldown authority. The qualified
preserved WSL configuration is exactly 120 seconds.

## Admission probe

Each guard invocation probes the local MCP endpoint directly:

```text
POST http://<mcp.host>:<mcp.port>/mcp
```

using a normal MCP `initialize` request for protocol `2025-11-25`.

Healthy admission is HTTP 200. If a healthy initialize returns an
`Mcp-Session-Id`, the guard MUST issue MCP `DELETE` for that session before
returning so the guard does not itself consume session capacity.

The **only** recovery signature is the semantic conjunction:

```text
HTTP status == 503
JSON-RPC error.message == "maximum HTTP session count reached"
```

Together this is the preserved signature:

```text
503 maximum HTTP session count reached
```

The live WSL server serializes this as a JSON-RPC error object (observed error
code `-32000`). Matching is on the HTTP status and exact JSON-RPC error message,
not on raw JSON formatting or a substring of the response body. HTTP 503 with
another error message is not a match. The same message under another HTTP status
is not a match. Plaintext/non-JSON bodies are not a match.

## Non-recovery observations

The guard MUST NOT restart a service for any of the following:

- healthy HTTP 200;
- another 4xx/5xx response;
- HTTP 503 with a different body;
- connection refused;
- timeout;
- malformed/undecodable response;
- MCP process down;
- tunnel `/healthz` or `/readyz` failure;
- tunnel/control-plane 5xx;
- poll timeout;
- memory pressure or any P12 classification.

These observations may be persisted as evidence but remain non-mutating.

## Recovery action

For an exact saturation match outside cooldown, the guard MUST directly invoke
restart only for:

```text
coding-tools-mcp-<instance>.service
```

It MUST NOT directly invoke restart for the tunnel service, timer, host, or
unrelated MCP instances. The generated tunnel unit already has
`Requires=coding-tools-mcp-<instance>.service` and `Restart=always`; therefore
systemd may stop/restart that dependent tunnel as a consequence of the MCP
restart. Such dependency propagation is allowed and MUST recover healthy. It is
not a second P11 recovery target.

The restart is performed through the user's systemd manager. A restart attempt
is recorded before execution, and the attempt result is persisted after systemd
returns.

## Cooldown

Cooldown is measured from the most recent persisted **restart attempt**, whether
that attempt succeeded or failed. This fail-safe rule prevents a broken MCP or
systemd state from causing a restart loop every guard interval.

If an exact saturation match occurs during cooldown:

- no restart is attempted;
- the observation is persisted as `cooldown`;
- the remaining cooldown is recorded.

Wall-clock timestamps are UTC. If persisted time is malformed or appears in the
future, the guard fails closed rather than treating cooldown as expired.

## Persisted evidence

Structured state is stored under the existing manager-owned instance state:

```text
~/.local/state/workspace-mcp-manager/instances/<instance>/admission-recovery.json
```

The file is mode `0600` and schema-versioned. It contains bounded non-secret
evidence including:

- instance ID;
- last observation timestamp/classification;
- last HTTP status when available;
- exact-signature match boolean;
- saturation detection count;
- restart-attempt count;
- last restart-attempt timestamp;
- last restart result/exit code;
- configured guard interval/cooldown values;
- cooldown-until timestamp when applicable.

The raw response body is never persisted. For the exact match, the fixed
signature name is sufficient evidence. Secret environment values MUST NOT be
stored or emitted.

The existing `admission-guard.log` remains bounded operational/logging evidence;
the JSON file is the authoritative structured recovery state.

## Runtime gating

P11 removes the P5/P6 planner conflict that previously rejected
`admission_guard_enabled=true`. Once P11 is implemented, the generated guard
service/timer participate in normal lifecycle planning/apply/start/stop/remove
ordering.

When `admission_guard_enabled=false`, no guard service/timer is generated and a
direct private runtime invocation MUST perform no restart.

## Pure qualification

Automated tests MUST cover at least:

1. healthy initialize + session DELETE;
2. exact 503/body detection;
3. no restart for near-match 503;
4. no restart for another 5xx;
5. no restart for connection/timeout errors;
6. exact match outside cooldown restarts only the MCP unit;
7. exact match inside cooldown does not restart;
8. failed restart attempt still establishes cooldown;
9. malformed/future persisted cooldown fails closed;
10. structured evidence persistence and no raw body/secret values;
11. planner accepts enabled admission recovery;
12. generated timer preserves configured 30-second cadence;
13. full prior suite remains green.

## Live WSL qualification

The disposable `manager-qual` instance is the only live P11 target.

Live qualification MUST prove:

1. enable guard with `guard_interval_seconds=30` and
   `recovery_cooldown_seconds=120`;
2. generated guard service/timer are manager-owned, enabled/active as expected;
3. healthy guard run creates evidence and does not restart MCP;
4. controlled local MCP session saturation reaches the exact preserved 503
   signature;
5. the guard automatically recovers the instance by restarting only its MCP
   service;
6. persisted recovery evidence names only the MCP unit as the direct restart
   target; any tunnel restart is attributable to its existing systemd dependency
   propagation and the tunnel returns healthy/ready;
7. evidence records exact detection and one restart attempt;
8. a repeated exact signature within 120 seconds does not cause a second
   restart;
9. MCP discovery and tunnel health/readiness recover;
10. restore baseline `admission_guard_enabled=false`;
11. final desired fingerprint, NOOP plan/apply, P7-P10 smoke, and source Git
    cleanliness remain green.

P11 is PASS only when both pure and controlled live saturation/recovery gates
pass. A generic forced restart without the exact saturation signature is not a
valid substitute.
