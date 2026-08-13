# P16 — Per-instance launchers

Status: **NORMATIVE P16 SPECIFICATION**

P16 implements the final frozen MVP capability:

```text
CAP-003  Per-instance launcher `<instance>-mcp`
```

P16 adds no new lifecycle semantics. The launcher is a convenience frontend over
the already-authoritative `workspace-mcp-manager` CLI and manager registry.

## Resource authority

For every desired instance, the normal generated-resource bundle includes:

```text
~/.local/bin/<instance>-mcp
```

The launcher is a manager-owned regular executable file:

```text
mode=0755
ownership marker=# workspace-mcp-manager-instance=<instance>
```

It contains no desired declaration, tunnel ID, credential reference, API key,
GitHub/Codex authentication material, or copied legacy configuration.

The launcher MUST execute the manager using the manager's canonical executable
and registry paths captured by `ManagerPaths` at generation time.

## Lifecycle

The launcher is a normal manager resource:

- `deployment=present` -> launcher MUST exist and match generated content/mode;
- launcher content/mode changes are reconciled by normal apply;
- `deployment=absent` -> launcher MUST be removed;
- an existing symlink, non-regular file, or regular file without the exact
  manager ownership marker is FOREIGN and MUST produce reconciliation conflict;
- P16 MUST NOT overwrite a legacy `<instance>-mcp` launcher.

No additional launcher registry or ownership database is introduced.

## Supported command map

The launcher accepts only instance-scoped manager operations.

Direct instance operations:

```text
show
render
plan
status
logs
git
diagnose
apply
start
stop
restart
remove
```

Example semantic mapping:

```text
manager-qual-mcp status
-> workspace-mcp-manager --registry-dir <registry> instance status manager-qual
```

The launcher also supports the existing instance-bound command families:

```text
<instance>-mcp access list
<instance>-mcp access add-ro <alias> <path>
<instance>-mcp access add-rw <alias> <path>
<instance>-mcp access remove <alias>

<instance>-mcp codex start ...
<instance>-mcp codex status <job-id>
<instance>-mcp codex output <job-id> ...
<instance>-mcp codex cancel <job-id>
```

These map to the existing `access` and `codex` public CLIs with the launcher
instance ID inserted in the already-defined argument position.

## Forbidden passthrough

The launcher MUST NOT expose a generic arbitrary-command trampoline.

It MUST reject:

- host operations;
- legacy cleanup operations;
- toolkit/developer-tool installation operations;
- registry create/update/validate;
- private `_runtime` commands;
- unknown command names.

`-h`, `--help`, `help`, and no arguments print bounded launcher usage and return
success. Unknown commands return a nonzero usage error without invoking the
manager.

## Shell safety

Generated shell text MUST:

- use `/bin/sh`;
- use `set -eu`;
- quote the manager executable and registry path as literal shell words;
- use only the validated manager instance ID as a literal;
- pass user-supplied trailing arguments only through `"$@"`;
- contain no `eval`, command substitution, sourcing, or environment-file load.

## Qualification

Pure verification MUST cover:

1. launcher resource path, mode, ownership marker, and lifecycle flags;
2. deterministic content;
3. direct instance-command mappings;
4. access-family mappings;
5. Codex-family mappings;
6. help/unknown-command behavior;
7. absence of generic/host/private passthrough;
8. refusal to overwrite foreign/legacy launchers;
9. normal `deployment=absent` launcher removal planning;
10. full P6-P15 regression suite.

Live WSL qualification on `manager-qual` MUST prove:

1. the launcher is generated and installed by normal apply;
2. launcher mode is `0755` and content carries the exact manager marker;
3. launcher `status`, `plan`, and `access list` succeed and are bound to
   `manager-qual`;
4. unknown command fails without host mutation;
5. final manager plan is all NOOP;
6. MCP discovery and tunnel health/readiness remain healthy;
7. no external-access bind remains after qualification.

P16 is the final frozen MVP capability phase. Post-MVP items remain governed by
their existing capability dispositions and are not implicitly started by P16.
