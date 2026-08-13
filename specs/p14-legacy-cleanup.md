# P14 — Legacy cleanup lifecycle

Status: **NORMATIVE P14 SPECIFICATION**

P14 replaces the legacy `cleanup` lifecycle with an explicit, ownership-safe
manager cleanup surface and implements deferred legacy cleanup support.

## Capability authority

P14 implements exactly:

```text
CAP-013  REPLACE P6/P14  cleanup lifecycle
CAP-042  P14             legacy cleanup support
```

P14 MUST preserve the already-qualified P6 cleanup/removal safety properties:

```text
CAP-036  safe orphan MCP/tunnel process cleanup
CAP-037  refuse termination of unverified foreign PID
CAP-038  managed-resource ownership markers
CAP-039  ownership-protected removal
CAP-040  cleanup audit defaults to read-only
CAP-041  cleanup preserves shared binaries/credential files
```

P14 MUST NOT implement P15 component installation/removal or mutate unrelated
manager desired state.

## Public command surface

```text
workspace-mcp-manager cleanup audit [<legacy-instance>]
workspace-mcp-manager cleanup execute <legacy-instance>
```

`cleanup audit` is read-only. With no instance argument it inventories all
recognized legacy candidates.

`cleanup execute` requires one explicit legacy instance ID. It may remove only
resources that the immediately preceding host-side revalidation proves belong
to that exact legacy instance.

There is no wildcard destructive cleanup command.

## Recognized legacy layout

The accepted WSL/native-Linux legacy `workspace-mcp` layout is:

```text
~/.config/workspace-mcp/<id>.conf
~/.config/tunnel-client/workspace-mcp-<id>.yaml
~/.config/systemd/user/coding-tools-mcp-<id>.service
~/.config/systemd/user/tunnel-client-<id>.service
~/.local/bin/<id>-mcp
~/.local/state/workspace-mcp/<id>/
```

Known per-instance state files are:

```text
mcp.log
tunnel.log
tunnel-supervisor.log
```

The state directory MUST NOT be recursively removed when it contains any other
file, directory, symlink, device, or unknown entry.

## Shared resources that MUST be preserved

Legacy cleanup MUST NOT remove or modify shared implementation/tooling or
credential material, including:

```text
~/.local/lib/workspace-mcp/
coding-tools-mcp installation
tunnel-client installation
~/.config/tunnel-client/runtime.env
CONTROL_PLANE_API_KEY or any env file containing it
Codex installation/auth/session material
GitHub/SSH credentials
new workspace-mcp-manager registry/state/binaries
```

Cleanup results MUST NOT print secret values.

## Legacy ownership proof

Every removable resource is independently revalidated.

### Legacy config

`~/.config/workspace-mcp/<id>.conf` MUST be a regular non-symlink UTF-8 file and
contain exactly one data assignment:

```text
INSTANCE_ID=<id>
```

The file MUST use the legacy data-file format rather than shell sourcing.

Unknown fields do not by themselves invalidate ownership because legacy
versions gained fields over time, but malformed/duplicate `INSTANCE_ID`
evidence does.

### Legacy tunnel profile

The profile MUST be a regular non-symlink UTF-8 file and contain the exact
ownership marker:

```text
# workspace-mcp-instance=<id>
```

### Legacy user-systemd units

Each unit MUST be a regular non-symlink UTF-8 file and contain:

```text
# workspace-mcp-instance=<id>
```

Manager-owned units using:

```text
# workspace-mcp-manager-instance=<id>
```

are never legacy resources.

Before unit-file removal, cleanup MUST verify the installed unit path is the
expected user-unit path and re-read the marker immediately before mutation.

### Legacy launcher

`~/.local/bin/<id>-mcp` MUST be a regular non-symlink file and be a legacy
wrapper that:

1. references exactly `~/.config/workspace-mcp/<id>.conf` through
   `WORKSPACE_MCP_CONFIG`;
2. executes a shared binary path whose basename is `workspace-mcp`;
3. passes through `"$@"`.

Arbitrary same-named scripts are foreign and must not be removed.

### Legacy state directory

The exact directory `~/.local/state/workspace-mcp/<id>` may be removed only when:

- it is a real directory, not a symlink;
- every child is a regular non-symlink file;
- every child basename is in the recognized state-file set above.

Unknown residue is a conflict.

## Candidate discovery

Audit derives candidate IDs from recognized names under:

- legacy config files;
- legacy tunnel profiles;
- legacy MCP/tunnel user-unit filenames;
- legacy launcher filenames;
- legacy state-directory names.

Candidate names MUST pass the manager instance-ID syntax before they are exposed
as actionable IDs. Invalid names may be reported as warnings but never acted on.

## New-manager collision rule

If `<id>` exists in the `workspace-mcp-manager` desired-state registry,
`cleanup execute <id>` MUST refuse with a conflict even if some legacy-looking
files also exist.

This prevents cleanup from deleting resources involved in a current manager
instance and keeps migration/renaming outside P14.

## Audit result contract

Each candidate reports per resource:

```text
path
kind
present
ownership = legacy-owned | absent | foreign | unsafe
detail
```

Candidate summary state:

```text
clean              no recognized legacy resources remain
removable          one or more legacy-owned resources, no conflicts
conflict           foreign/unsafe evidence or manager-registry collision
```

Audit MUST NOT stop/disable units, kill processes, remove files, rewrite configs,
or modify manager registry/applied state.

## Apply ordering

`cleanup execute <id>` performs a fresh audit inside the host worker and requires
`state=removable` before any mutation.

Mutation order is:

1. revalidate legacy ownership and installed-path identity;
2. stop legacy tunnel unit if loaded/active;
3. stop legacy MCP unit if loaded/active;
4. disable legacy tunnel unit if enabled;
5. disable legacy MCP unit if enabled;
6. revalidate unit-file ownership markers again;
7. remove legacy tunnel/MCP unit files;
8. `systemctl --user daemon-reload` when unit files were removed;
9. remove legacy tunnel profile;
10. remove legacy launcher;
11. remove legacy config;
12. remove only recognized legacy state files, then the empty state directory;
13. run a final audit and require `state=clean`.

If a unit does not exist in systemd, stop/disable is a no-op. P14 MUST NOT kill
arbitrary PIDs directly merely because their command line resembles MCP/tunnel;
existing P6 process ownership safeguards remain authoritative.

## Failure semantics

If any resource changes between audit/revalidation/mutation or ownership becomes
foreign/unsafe, cleanup fails closed and does not remove that resource.

Apply returns structured evidence including the operations completed and final
audit state. It MUST NOT pretend atomic rollback of already stopped/removed
legacy resources. Instead, mutation ordering minimizes irreversible work and
every operation is ownership-gated immediately before execution.

## Qualification

Pure verification MUST cover:

1. candidate discovery across every recognized legacy resource type;
2. read-only audit;
3. exact config/profile/unit/launcher ownership proof;
4. refusal of manager-owned and foreign same-named resources;
5. unknown state residue fail-closed behavior;
6. manager-registry collision refusal;
7. shared binary/runtime.env preservation;
8. stop/disable/remove ordering;
9. daemon reload only when unit files are removed;
10. final clean audit and idempotent second cleanup audit.

Live WSL qualification MUST use only a synthetic unique legacy ID such as
`p14-legacy-qual`. It MUST NOT modify existing `leadbot`, `manager`,
`wsl-reconcile`, `manager-qual`, Vigilus, or any other existing deployment.

The live harness MUST create deterministic synthetic legacy-owned resources,
prove read-only audit, clean them with `cleanup execute`, prove shared sentinel
resources remain untouched, prove a second audit is clean, then restore the
normal `manager-qual` no-access/all-NOOP baseline.
