# P14 — Legacy cleanup lifecycle status

Status: **IMPLEMENTATION PASS / LIVE WSL QUALIFIED**

P14 capability authority is `specs/p14-legacy-cleanup.md` and covers:

```text
CAP-013  replacement cleanup lifecycle
CAP-042  legacy cleanup support
```

P14 preserves CAP-036 through CAP-041 ownership/removal safeguards.

## Public surface

```text
workspace-mcp-manager cleanup audit [<legacy-instance>]
workspace-mcp-manager cleanup execute <legacy-instance>
```

`audit` is read-only. `execute` requires one explicit target; there is no
destructive wildcard cleanup command.

## Recognized legacy resources

P14 recognizes the verified legacy `workspace-mcp` per-instance layout:

```text
~/.config/workspace-mcp/<id>.conf
~/.config/tunnel-client/workspace-mcp-<id>.yaml
~/.config/systemd/user/coding-tools-mcp-<id>.service
~/.config/systemd/user/tunnel-client-<id>.service
~/.local/bin/<id>-mcp
~/.local/state/workspace-mcp/<id>/
```

State cleanup is allowed only when every child is one of:

```text
mcp.log
tunnel.log
tunnel-supervisor.log
```

Unknown entries, symlinks, foreign ownership markers, malformed ownership
evidence, or new-manager registry collisions fail closed.

Shared resources are never cleanup targets, including:

```text
~/.local/lib/workspace-mcp/
~/.config/tunnel-client/runtime.env
coding-tools-mcp / tunnel-client installations
manager registry/state
Codex/GitHub/SSH credential material
```

## Real legacy read-only audit

The installed public audit on the current WSL host reports exactly:

```text
leadbot        removable
manager        removable
wsl-reconcile  removable
```

`manager-qual` is not classified as legacy and none of these existing legacy
instances were modified during P14.

### P14-Q1 — launcher false positive

The first read-only host audit also discovered the installed `coding-tools-mcp`
executable symlink because its filename matched the broad `*-mcp` lexical
pattern. It had no legacy ownership evidence and was correctly not removable,
but its presence made the all-instance audit noisy/conflicting.

The public all-instance audit projection now exposes only candidates with at
least one independently proven `legacy-owned` resource. Explicit
`cleanup audit <id>` remains strict and still reports foreign/unsafe evidence.

After the correction the host audit returns only the three verified legacy
instances above with `ok=true`.

## Pure verification

Final implementation gate before documentation:

```text
Ran 172 tests
OK

bash -n scripts/qualify_p14_wsl.sh  PASS
git diff --check                    PASS
```

P14-specific coverage includes:

- complete legacy candidate discovery/audit;
- read-only audit preservation;
- config/profile/unit/launcher ownership proof;
- refusal of manager-owned/foreign same-named resources;
- unknown state residue rejection;
- manager-registry collision refusal;
- user-systemd stop/disable/removal ordering;
- foreign FragmentPath refusal;
- shared implementation/runtime-env preservation;
- final clean convergence and idempotent clean target;
- public CLI and transient host-boundary routing;
- P14 qualification-harness contracts.

## Live WSL qualification

Qualification script:

```text
scripts/qualify_p14_wsl.sh
```

The live qualification created only the synthetic unique legacy instance:

```text
p14-legacy-qual
```

Its synthetic MCP and tunnel units used `/usr/bin/sleep infinity`, so no ports,
tunnels, credentials, or production workspaces were involved.

Qualification proved:

1. baseline public legacy audit was captured before synthetic preparation;
2. synthetic config/profile/units/launcher/state all passed exact legacy
   ownership audit;
3. both synthetic units were enabled and running;
4. `cleanup audit p14-legacy-qual` left both PIDs and all files unchanged;
5. `cleanup execute p14-legacy-qual` stopped tunnel then MCP, disabled tunnel
   then MCP, removed the owned unit files, daemon-reloaded systemd, then removed
   profile/launcher/config/known state logs;
6. the shared legacy `workspace-mcp` implementation hash was unchanged;
7. the shared tunnel `runtime.env` hash was unchanged;
8. second target audit returned `state=clean`;
9. the final all-instance legacy audit exactly matched the baseline audit;
10. `manager-qual` MCP/tunnel PIDs were unchanged;
11. final manager plan remained all `NOOP` and discovery/health/readiness stayed
    green.

Live result:

```text
P14_READ_ONLY_AUDIT=PASS
P14_SHARED_RESOURCES_PRESERVED=PASS
P14_LEGACY_CLEANUP_QUALIFICATION=PASS
```

Post-qualification synthetic residue check:

```text
legacy config=ABSENT
legacy tunnel profile=ABSENT
legacy MCP unit=ABSENT
legacy tunnel unit=ABSENT
legacy launcher=ABSENT
legacy state dir=ABSENT
```

The three pre-existing legacy instances remain present/removable and unchanged.

## Implementation checkpoint

```text
25c06eac868bc4d9bfe2f84b30b879599b867f67
Implement P14 legacy cleanup
```

## Current gate

```text
P14_IMPLEMENTATION=PASS
P14_PURE_VERIFICATION=PASS
P14_LIVE_WSL_QUALIFICATION=PASS
P15=NOT_STARTED
```
