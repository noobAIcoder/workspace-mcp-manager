# P11 — Admission recovery status

Status: **PASS — LIVE WSL QUALIFIED**

P11 authority is `specs/p11-admission-recovery.md` and implements CAP-090
through CAP-094.

## Scope

P11 preserves the narrow admission-recovery contract:

```text
CAP-090  exact MCP saturation signature
CAP-091  30-second admission guard
CAP-092  120-second recovery cooldown
CAP-093  restart only for the exact signature
CAP-094  persisted admission-recovery evidence
```

P12 incident classification and P13 reboot behavior were not started.

## Implementation

The generated resources remain:

```text
workspace-mcp-admission-guard-<instance>.service
workspace-mcp-admission-guard-<instance>.timer
```

The P11 runtime performs a real local MCP `initialize` probe using protocol
`2025-11-25`. Healthy probe sessions are deleted immediately. Recovery matches
only this semantic signature:

```text
HTTP 503
AND
JSON-RPC error.message == "maximum HTTP session count reached"
```

The live server serialized the preserved failure as:

```json
{"error":{"code":-32000,"message":"maximum HTTP session count reached"},"id":1,"jsonrpc":"2.0"}
```

The guard directly invokes restart only for:

```text
coding-tools-mcp-<instance>.service
```

The existing tunnel unit has an explicit `Requires=` dependency on MCP and
`Restart=always`; systemd may therefore cycle the dependent tunnel during MCP
restart. P11 does not directly target the tunnel and requires it to recover
`live`/`ready` afterward.

Structured evidence is persisted at:

```text
~/.local/state/workspace-mcp-manager/instances/<instance>/admission-recovery.json
```

It records bounded non-secret observation/restart/cooldown evidence, including
cumulative saturation detections, restart attempts, cooldown suppressions, the
last exact signature, and the direct restart unit. A failed restart attempt also
establishes cooldown to prevent a 30-second restart loop.

P11 also adds applied-state-driven cleanup for the conditional guard service and
timer. Disabling the feature stops/disables/removes only fingerprint-verified,
manager-owned stale guard units and fails closed on modified ownership evidence.

## Pure verification

Final host-side verification:

```text
bash -n scripts/qualify_p11_wsl.sh     PASS
git diff --check                       PASS
Ran 131 tests
OK
```

Coverage includes:

- healthy initialize + MCP session DELETE;
- exact JSON-RPC 503 signature detection;
- near-match/other-status rejection;
- connection/timeout non-recovery;
- MCP-only direct restart targeting;
- successful and failed restart evidence;
- failed restart still establishing cooldown;
- 120-second cooldown suppression;
- malformed/future timestamp fail-closed behavior;
- persistent non-secret evidence;
- 30-second generated timer cadence;
- planner acceptance of enabled recovery;
- reversible stale guard lifecycle;
- modified stale guard fail-closed cleanup;
- the full P6-P10 regression suite.

## Live qualification — R4

Qualification ran as detached user-systemd unit:

```text
workspace-mcp-manager-p11-qualification-r4.service
Result=success
ExecMainStatus=0
```

The live run emitted:

```text
P11_SATURATION_SIGNATURE=PASS sessions=126
P11_SATURATION_SIGNATURE=PASS sessions=128
P11_WSL_QUALIFICATION=PASS
```

The first saturation was left for the generated **30-second timer** to recover;
the qualification script did not invoke the recovery guard manually for that
event. The MCP request carrying the ChatGPT connection received the expected
temporary 502 while the MCP service restarted, and the plugin subsequently
reconnected.

Persisted R4 evidence after the second saturation showed:

```text
saturation_detection_count=2
restart_attempt_count=1
cooldown_suppression_count=1
last_saturation_signature=503 maximum HTTP session count reached
last_restart_result.ok=true
last_restart_result.exit_code=0
last_restart_result.unit=coding-tools-mcp-manager-qual.service
```

This proves that the first exact saturation caused one successful MCP restart
and the second exact saturation, inside the 120-second cooldown, caused no
second restart.

After MCP restart the tunnel dependency also cycled under systemd and recovered.
Final endpoints were:

```text
MCP discovery: PASS
tunnel /healthz: live
tunnel /readyz: ready
```

## Qualification regressions

### P11-Q1 — exact signature response shape

The first harness assumed the preserved text appeared as a plaintext HTTP body.
Live saturation proved the server returns a JSON-RPC error object instead. The
detector and specification were corrected to match HTTP 503 plus exact
`error.message`, independent of JSON formatting, while retaining strict
near-match rejection.

Correction commit:

```text
3faf9cd17f5be89cfff277fc18448f11954cca5d
Correct P11 saturation signature
```

### P11-Q2 — dependent tunnel restart

The second harness incorrectly required the tunnel PID to remain unchanged.
Journal evidence proved the guard targeted only MCP, while systemd propagated
the restart through the existing tunnel `Requires=` dependency and
`Restart=always`. The acceptance criterion was corrected to check the persisted
direct MCP restart target and dependent tunnel recovery.

Correction commit:

```text
178b29568fa8b85833a59b435d26c39bbfc1dc54
Correct P11 tunnel recovery qualification
```

### P11-Q3 — tunnel recovery timing

The third harness sampled the dependent tunnel PID immediately after MCP
recovery, before the tunnel's automatic restart had completed. The acceptance
check was moved after the existing health/readiness recovery loop.

Correction commit:

```text
ee5dad3f2f360262a87a44d7bcad1785b4ec1d76
Fix P11 tunnel recovery timing
```

All failed qualification attempts restored the disposable instance to its
baseline before the next run.

## Final convergence

R4 restored the original declaration:

```text
admission_guard_enabled=false
guard_interval_seconds=30
recovery_cooldown_seconds=120
desired_fingerprint=728dcc4f4086ef9e98f856201741dc16c50bee9a9d0f491009661e94cba14701
```

Conditional guard resources were removed:

```text
workspace-mcp-admission-guard-manager-qual.service: absent
workspace-mcp-admission-guard-manager-qual.timer:   absent
```

Final reconciliation plan contained only `NOOP` operations. The structured P11
evidence remains as historical runtime evidence while the guard itself is
disabled.

Implementation lineage:

```text
73af332833c43eece3f2b8f4dc44eabdb75eea16  Implement P11 admission recovery
3faf9cd17f5be89cfff277fc18448f11954cca5d  Correct P11 saturation signature
178b29568fa8b85833a59b435d26c39bbfc1dc54  Correct P11 tunnel recovery qualification
ee5dad3f2f360262a87a44d7bcad1785b4ec1d76  Fix P11 tunnel recovery timing
```

Final gate:

```text
P11_IMPLEMENTATION=PASS
P11_PURE_VERIFICATION=PASS
P11_LIVE_WSL_QUALIFICATION=PASS
P11_WSL_QUALIFICATION=PASS
```

P11 is closed. P12 was not started.
