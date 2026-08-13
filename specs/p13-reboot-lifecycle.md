# P13 — Controlled reboot lifecycle

Status: **NORMATIVE P13 SPECIFICATION**

P13 preserves the reboot/checkpoint behavior already qualified in the legacy
`workspace-mcp` implementation while moving authority into the greenfield
manager.

## Capability authority

P13 implements:

```text
CAP-066  Checkpoint written before reboot request
CAP-067  reboot-check
CAP-068  Narrow workspace-mcp-reboot bridge
CAP-069  Reboot authorization preflight
CAP-070  Reboot checkpoint reconciliation after boot change
CAP-071  Synchronous reboot-command failure persistence
```

P13 MUST NOT implement P14 legacy cleanup or P15 component installation.

## Public command surface

```text
workspace-mcp-manager host reboot --reason <text>
workspace-mcp-manager host reboot-check

workspace-mcp-reboot --check
workspace-mcp-reboot
```

`host reboot` is a host operation. It snapshots all currently managed
`deployment=present,runtime=running` instances as the expected post-boot
recovery set.

`host reboot-check` is read/reconciliation behavior over the persisted reboot
checkpoint. It MUST NOT itself request another reboot.

## Narrow bridge

`workspace-mcp-reboot` is installed as a separate console entrypoint from the
same package as `workspace-mcp-manager`.

It accepts exactly:

```text
workspace-mcp-reboot --check
workspace-mcp-reboot
```

All other arguments fail.

The bridge contains no generic command execution facility and MUST NOT accept a
unit name, systemctl verb, shell text, or arbitrary executable.

Authorization policy is external. P13 MUST NOT create, edit, or broaden sudoers
rules. The preserved privileged action is exactly:

```text
/usr/bin/sudo -n /usr/bin/systemctl reboot
```

The non-destructive authorization check is exactly scoped to that command:

```text
/usr/bin/sudo -n -l /usr/bin/systemctl reboot
```

Failure of `--check` means reboot is not authorized from the current host
configuration. It is a preflight result, not a reason to alter authorization.

## Checkpoint persistence

The manager persists one authoritative checkpoint at:

```text
~/.local/state/workspace-mcp-manager/reboot/checkpoint.json
```

Directory mode is `0700`; checkpoint mode is `0600`.

The checkpoint schema is version 1 and includes at least:

```text
schema_version
checkpoint_id
reason
requested_at
updated_at
state
boot_id_before
boot_id_after
bridge_path
authorization
request_result
expected_instances[]
reconciliation
```

`expected_instances[]` records non-secret authority for every managed instance
that was present+running immediately before the reboot request:

```text
instance_id
desired_fingerprint
workspace_path
mcp_unit
tunnel_unit
mcp_host
mcp_port
tunnel_health_host
tunnel_health_port
```

The checkpoint MUST be written and fsynced before either bridge preflight or the
actual reboot bridge is executed.

The checkpoint never stores tunnel API keys, GitHub/Codex authentication,
environment dumps, sudo output, or arbitrary command output.

## Boot identity

Boot identity is read from:

```text
/proc/sys/kernel/random/boot_id
```

It MUST be a non-empty UUID-shaped value. Failure to read/validate boot identity
fails closed.

## Reboot request lifecycle

`host reboot --reason ...` performs this exact order:

1. validate non-empty bounded reason;
2. read current boot ID;
3. snapshot the expected running instance set;
4. persist checkpoint state `prepared`;
5. resolve the sibling `workspace-mcp-reboot` entrypoint;
6. execute bridge `--check`;
7. persist authorization result;
8. if authorization failed, persist `authorization_failed` and return failure;
9. persist `requesting` before the actual bridge call;
10. invoke `workspace-mcp-reboot` with no arguments;
11. if it returns nonzero or raises synchronously, persist `request_failed` with
    bounded/redacted failure metadata;
12. if it returns zero before the host disappears, persist `request_accepted`.

The reboot command is allowed to terminate the initiating MCP/tunnel request.
Checkpoint persistence is therefore authoritative, not the initiating response.

## Synchronous failure persistence

If authorization or reboot execution fails synchronously, the checkpoint MUST
remain present with:

```text
state=authorization_failed | request_failed
request_result.ok=false
request_result.exit_code=<int|null>
request_result.error=<bounded redacted summary|null>
boot_id_before=<original boot ID>
boot_id_after=null
```

No retry is automatically attempted.

## `reboot-check`

When no checkpoint exists:

```text
state=no_checkpoint
ok=true
```

When a checkpoint exists, `reboot-check` reloads current boot identity.

### No boot change

If current boot ID equals `boot_id_before`:

- `prepared`, `requesting`, or `request_accepted` report `pending_no_boot_change`;
- `authorization_failed` and `request_failed` remain terminal failure states;
- no instance recovery claim is made.

The checkpoint is updated with `last_checked_at` but the original request
evidence is retained.

### Boot change observed

If current boot ID differs from `boot_id_before`, P13 persists
`boot_observed`/`boot_id_after` and evaluates the captured expected-instance set.

For each captured instance:

1. current desired declaration MUST still exist;
2. its fingerprint MUST equal the checkpointed fingerprint;
3. the captured MCP and tunnel units MUST be active;
4. MCP discovery MUST return HTTP 200;
5. tunnel `/healthz` MUST return HTTP 200 with `live`;
6. tunnel `/readyz` MUST return HTTP 200 with `ready`.

If all expected instances recover, persist:

```text
state=reconciled
reconciliation.ok=true
```

If boot changed but one or more expected instances are not recovered yet,
persist:

```text
state=boot_observed_waiting_recovery
reconciliation.ok=false
```

Repeated `reboot-check` calls are idempotent and may advance
`boot_observed_waiting_recovery -> reconciled` when recovery completes.

If desired authority changed after the checkpoint, the affected instance is
reported `desired_changed`; P13 does not silently bless the changed declaration
as the expected pre-reboot recovery target.

## Secret/redaction contract

All returned and persisted reboot metadata is passed through the manager's
existing redaction rules. The bridge's stdout/stderr is never persisted in full;
only a bounded redacted error summary may be retained for synchronous failure.

## Qualification gate

Pure verification MUST cover:

1. exact bridge argv and rejection of arbitrary arguments;
2. non-destructive authorization check;
3. checkpoint fsync before preflight/reboot subprocesses;
4. expected-instance capture;
5. synchronous authorization failure persistence;
6. synchronous reboot-command failure persistence;
7. unchanged-boot `reboot-check` semantics;
8. changed-boot recovery reconciliation;
9. desired-fingerprint mismatch handling;
10. repeated reconciliation to success;
11. secret redaction and secure file modes.

Live non-destructive WSL qualification MUST prove:

1. current boot ID can be captured;
2. `workspace-mcp-reboot` is installed;
3. `workspace-mcp-reboot --check` reflects the host's existing sudo policy;
4. an unauthorized reboot request, when authorization is unavailable, persists
   a checkpoint before returning failure and changes no service PID;
5. `host reboot-check` reports the unchanged boot/failure state;
6. P6–P12 baseline remains all-NOOP and healthy afterward.

Physical reboot qualification is a separate host acceptance gate. It MUST NOT
be simulated by rewriting `/proc` or the real checkpoint boot ID, and P13 MUST
NOT create sudo authorization merely to make that gate runnable.
