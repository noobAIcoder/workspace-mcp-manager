# P13 — Controlled reboot lifecycle status

Status: **IMPLEMENTATION PASS / WSL UNSUPPORTED-GATE PASS / NATIVE-LINUX PHYSICAL REBOOT NOT RUN**

P13 capability authority is `specs/p13-reboot-lifecycle.md` and covers
CAP-066 through CAP-071.

## Implemented behavior

Public surface:

```text
workspace-mcp-manager host reboot --reason <text>
workspace-mcp-manager host reboot-check

workspace-mcp-reboot --check
workspace-mcp-reboot
```

The manager now:

- reads canonical host boot identity from
  `/proc/sys/kernel/random/boot_id`;
- captures every currently managed `present+running` instance and its desired
  fingerprint as the expected post-boot recovery set;
- persists and fsyncs
  `~/.local/state/workspace-mcp-manager/reboot/checkpoint.json` before bridge
  preflight or reboot execution;
- keeps the reboot state directory `0700` and checkpoint `0600`;
- persists authorization failure and synchronous reboot-command failure;
- never automatically retries a failed reboot request;
- reconciles a changed boot ID against the captured desired fingerprints,
  MCP/tunnel unit activity, MCP discovery, and tunnel health/readiness;
- reports changed desired authority as `desired_changed` rather than silently
  accepting a new declaration;
- permits repeated `reboot-check` calls to advance
  `boot_observed_waiting_recovery -> reconciled` when recovery finishes;
- sends the public reboot reason to the host worker over stdin rather than in
  the transient systemd argv;
- applies existing manager redaction to all persisted/public reboot metadata.

## Narrow bridge / authorization authority

The package installs the dedicated sibling executable:

```text
~/.local/bin/workspace-mcp-reboot
```

It accepts exactly two forms.

Safe authorization preflight:

```text
/usr/bin/sudo -n -l /usr/bin/systemctl reboot
```

Privileged action:

```text
/usr/bin/sudo -n /usr/bin/systemctl reboot
```

No arbitrary command, systemctl verb, unit name, shell text, or executable is
accepted by the bridge. P13 does not create/edit sudoers and does not broaden
host authorization.

On WSL, reboot execution is explicitly unsupported. Both the public reboot
request and `workspace-mcp-reboot` fail before checkpoint creation or
sudo/systemctl execution. A future optional WSL implementation may use a small
Windows-side listening/restart service reached over local networking; it is
deferred and must not require WSL command interop.

## Pure verification

Final host-side source gate after P13 implementation and qualification fixes:

```text
Ran 156 tests
OK

bash -n scripts/qualify_p13_wsl.sh   PASS
git diff --check                     PASS
```

Coverage includes:

- exact bridge argv;
- rejection of arbitrary bridge arguments;
- checkpoint existence/state before preflight and actual bridge invocation;
- expected-instance capture;
- authorization failure persistence;
- synchronous reboot-command failure persistence;
- reason/metadata redaction;
- secure file modes;
- unchanged-boot failure reconciliation;
- changed-boot successful reconciliation;
- desired-fingerprint drift rejection;
- repeated waiting-recovery reconciliation;
- public CLI and transient host-boundary routing.

The changed-boot tests use synthetic boot-ID files in isolated test roots; they
do not rewrite or simulate the host's real `/proc` boot identity.

## WSL live non-destructive qualification

Qualification script:

```text
scripts/qualify_p13_wsl.sh
```

The harness never invokes the real bridge without `--check`.

### WSL platform result

On the current WSL host:

```text
P13_REAL_BRIDGE_CHECK_RC=69
P13_WSL_REBOOT_UNSUPPORTED=PASS
```

Therefore reboot execution is blocked by explicit WSL platform policy,
independently of sudo authorization.

### Canonical live checkpoint ordering / failure persistence

The harness used two temporary narrow fake bridge executables while using the
real WSL boot ID, real manager registry, and canonical manager checkpoint path.
This exercised the installed `RebootService` without invoking sudo/systemctl.

The first fake bridge asserted the checkpoint already contained:

```text
state=prepared
```

before its `--check` execution, then returned exit `42`. Result:

```text
P13_AUTHORIZATION_FAILURE_PERSISTENCE=PASS
```

The second fake bridge required `state=prepared` during preflight, authorized
that preflight, then required:

```text
state=requesting
```

before the request invocation and returned exit `43`. Result:

```text
P13_SYNCHRONOUS_REQUEST_FAILURE_PERSISTENCE=PASS
```

The public installed command:

```text
workspace-mcp-manager host reboot-check
```

then correctly reported the persisted `request_failed` state while the host
boot ID was unchanged.

Live qualification final result:

```text
P13_WSL_NONDESTRUCTIVE_QUALIFICATION=PASS
P13_WSL_REBOOT=DEFERRED
P13_NATIVE_LINUX_PHYSICAL_REBOOT_QUALIFICATION=NOT_RUN
```

Qualification regressions corrected during P13:

### P13-Q1 — uv-tool Python environment

The first harness run reached the real bridge preflight successfully but the
embedded integration Python used `/usr/bin/python3`, which cannot import the
uv-tool-installed manager package. The harness now derives the Python
interpreter adjacent to the resolved `workspace-mcp-manager` executable.

### P13-Q2 — Codex smoke PATH

The second harness run completed all reboot/checkpoint assertions and failed
only because its transient shell did not inherit the NVM Codex PATH. The P10
smoke now resolves Codex using the instance's declared `mcp.exec_path`.

R3 then passed end-to-end.

## Final WSL baseline

After R3 cleanup:

```text
boot_id=8c308cb9-f326-472d-be77-3441f75b697d
reboot checkpoint=ABSENT

access=[]
access root=ABSENT

desired fingerprint=
728dcc4f4086ef9e98f856201741dc16c50bee9a9d0f491009661e94cba14701

plan=all NOOP

coding-tools-mcp-manager-qual.service=active/running
tunnel-client-manager-qual.service=active/running
MCP discovery=PASS
healthz=live
readyz=ready

codex-cli 0.147.0
P12 diagnose smoke=PASS
```

The R3 harness explicitly asserted that both MCP and tunnel MainPID values were
unchanged for the whole non-destructive qualification.

## Native Linux physical reboot acceptance

Physical reboot is a separate native-Linux host acceptance gate and was
intentionally not run in this WSL qualification. WSL reboot itself is deferred.

No P14 work was started.

## Current gate

```text
P13_IMPLEMENTATION=PASS
P13_PURE_VERIFICATION=PASS
P13_WSL_NONDESTRUCTIVE_QUALIFICATION=PASS
P13_WSL_REBOOT=DEFERRED
P13_NATIVE_LINUX_PHYSICAL_REBOOT_QUALIFICATION=NOT_RUN
P14=NOT_STARTED
```
