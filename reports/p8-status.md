# P8 — External-folder access lifecycle status

Status: **PASS — LIVE WSL + MCP QUALIFIED 2026-08-13**

Implementation checkpoints:

```text
5d58939  Implement P8 access lifecycle
e92ccc2  Harden P8 access namespace preflight
8198b65  Fix P8 systemd bind serialization
```

## Implemented contract

Public commands:

```text
workspace-mcp-manager access list <instance>
workspace-mcp-manager access add-ro <instance> <alias> <path>
workspace-mcp-manager access add-rw <instance> <alias> <path>
workspace-mcp-manager access remove <instance> <alias>
```

P8 implements CAP-095 through CAP-110:

- strict RO/RW desired-state declarations;
- normalized alias and source-path validation;
- host-boundary desired-state mutation serialized with lifecycle apply;
- `.workspace-mcp-access/<alias>` namespace generation;
- RO `BindReadOnlyPaths=` and RW `BindPaths=` enforcement;
- `PrivateUsers=true` only while external access exists;
- transient user-systemd namespace preflight;
- exact-target namespace preflight before MCP-unit mutation;
- MCP unit regeneration/restart on access changes;
- access-root ownership marker and `.gitignore`;
- applied-state-driven stale access cleanup;
- fail-closed cleanup on malformed, foreign, or unverified ownership evidence.

`access add-*` and `access remove` mutate desired state only and explicitly
report `apply_required=true`; normal lifecycle reconciliation remains the sole
authority for host mutation.

## Validation

The final pure suite completed with:

```text
Ran 97 tests
OK (skipped=1)
```

The single skip is the existing environment-dependent `systemd-analyze` sandbox
case. P8-specific validation and all other tests passed.

Additional source checks passed:

```text
bash -n scripts/qualify_p8_wsl.sh
git diff --check
```

## Live qualification regression — P8-Q1

The first live `manager-qual` apply created the planned access resources and
restarted MCP, but MCP failed repeatedly with:

```text
status=226/NAMESPACE
Failed to set up mount namespacing: ... No such file or directory
```

The transaction timed out at readiness and rolled back successfully; no P8
`applied.json` state or access root survived the failed apply.

Investigation established:

1. transient RO-only, RO+RW, mode-0700, and workspace-working-directory
   namespace probes all passed;
2. reconciling `manager-qual` to `present+stopped` installed the exact access
   directories and generated unit successfully;
3. `systemctl show` exposed a malformed duplicated parsed representation for the
   generated bind directives;
4. a known-good transient unit retained the intended single
   `source:destination:rbind` representation and ran successfully.

Cause: the generator quoted the entire systemd bind triplet. On the qualified
WSL user-systemd host this was not equivalent to the direct bind definition and
produced a malformed parsed bind mapping.

Correction:

- serialize bind definitions directly as `source:destination:rbind` using
  validated `systemd_path()` components;
- preserve staging namespace preflight before the transaction;
- create manager-owned access mountpoints before replacing the MCP unit;
- run an exact-target namespace preflight before MCP-unit mutation;
- retain rollback and ownership protection;
- update deterministic resource fingerprints and regression assertions.

After correction, `systemctl show` reported exactly one canonical RO and one RW
bind mapping, and `manager-qual` started normally.

## Real MCP/plugin enforcement

Qualification sources:

```text
RO: ~/.local/state/workspace-mcp-manager/p8-qualification/ro
RW: ~/.local/state/workspace-mcp-manager/p8-qualification/rw
```

Mounted aliases:

```text
.workspace-mcp-access/p8-ro
.workspace-mcp-access/p8-rw
```

Verified through `@ManagerQual`:

- RO source marker readable through the mounted alias;
- RW source marker readable through the mounted alias;
- direct MCP file-tool read of the external source path rejected with
  `ABSOLUTE_PATH_DENIED`;
- `apply_patch` under `p8-ro` rejected with `Read-only file system`;
- `apply_patch` under `p8-rw` created a file successfully;
- the created RW file appeared in the host source directory with identical
  content;
- the RW test file was deleted through MCP;
- qualification workspace Git remained clean throughout.

## Cleanup qualification

Both aliases were removed with the public access commands. The subsequent plan
identified the obsolete manager-owned access root, marker, `.gitignore`, and
mountpoint directories from applied-state evidence. Apply restarted MCP with the
no-access unit before deleting those obsolete resources.

Final state:

```text
access entries:       []
PrivateUsers:         no
BindPaths:            empty
BindReadOnlyPaths:    empty
access root:          absent
qualification source: absent
qualification Git:    clean
tunnel /healthz:      live
tunnel /readyz:       ready
plan:                 NOOP
NOOP apply:           PASS
```

The desired fingerprint returned to the pre-P8 no-access value:

```text
728dcc4f4086ef9e98f856201741dc16c50bee9a9d0f491009661e94cba14701
```

P7 smoke after cleanup also passed: exact workspace identity, trusted permission
mode, Landlock enabled, and clean Git.

Final live result:

```text
P8_ACCESS_LIFECYCLE_QUALIFICATION=PASS
```

P8 is complete. P9 was not started.
