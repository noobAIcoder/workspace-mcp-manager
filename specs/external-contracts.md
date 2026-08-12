# Verified external contracts — M0 freeze

Date: 2026-08-09

## `coding-tools-mcp` 0.2.2

Verified directly from the installed WSL executable/package.

Public CLI relevant to the manager:

```text
--workspace
--host
--port
--shell-env-inherit {core,all,none}
--permission-mode {safe,trusted,dangerous}
```

The filesystem/exec sandbox contract is external to the manager.

### External exec/read roots

`CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS` is read by `coding-tools-mcp` and split with
Python `os.pathsep`. On Linux/WSL this is a colon-separated list. Each non-empty
entry is `expanduser()`/`resolve()`d; existing directories are added to the
Landlock read/execute roots.

The manager MUST therefore render multiple desired `mcp.external_roots[]` as an
`os.pathsep`-joined environment value. It MUST NOT invent a different delimiter.

External roots grant Landlock read/execute admission for `exec_command`; they do
not expand the direct MCP file tools' workspace boundary.

Desired `mcp.binary` and `tunnel.binary` values MUST be absolute POSIX paths.
The manager does not defer binary resolution to a mutable service `PATH`.

### NVM-managed Node toolchain

An MCP instance MAY use a Node toolchain installed by NVM without sourcing an
interactive shell or `nvm.sh`.

For a selected NVM version such as:

```text
~/.nvm/versions/node/v24.16.0/
```

the desired declaration MUST make the toolchain explicit:

```text
mcp.exec_path      -> includes ~/.nvm/versions/node/<version>/bin
mcp.external_roots -> includes ~/.nvm/versions/node/<version>
```

The external root MUST be the complete selected version root rather than only
its `bin/` directory because executables/shims such as `npm`, `corepack`, and
`pnpm` may resolve implementation files under the version's `lib/node_modules/`.

The generated MCP service MUST preserve these explicit values in `PATH` and
`CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS`. It MUST NOT depend on shell startup files or
the `nvm` shell function. P6 host qualification MUST verify availability and
rendering for `node`, `npm`, `npx`, `corepack`, and `pnpm`; execution through the
MCP protocol remains part of P7 qualification.

### RO/RW external folders

Legacy `workspace-mcp 0.4.0` exposes external folders by mounting them *inside*
the MCP workspace namespace:

```text
.workspace-mcp-access/<alias>
```

using user-systemd:

```text
RO -> BindReadOnlyPaths=
RW -> BindPaths=
PrivateUsers=true when mounts exist
```

The manager MUST preserve this enforcement model unless a later verified
`coding-tools-mcp` contract provides an equivalent stronger primitive.

## Tunnel credential/runtime boundary

Generated manager-owned tunnel profiles live under:

```text
~/.config/workspace-mcp-manager/tunnel-profiles/
```

They reference the credential only as:

```text
env:CONTROL_PLANE_API_KEY
```

The external `tunnel.env_file` is parsed only by the tunnel runtime path. The
verified parser accepts `KEY=VALUE` and `export KEY=VALUE`, rejects duplicate or
malformed assignments, and requires a non-empty `CONTROL_PLANE_API_KEY` without
printing it.

## Host-observation boundary

P5 MUST fail closed when collision-sensitive identity metadata for an existing
MCP/tunnel deployment cannot be observed. In the current MCP sandbox,
`systemctl show` exposes MCP working directories and ports, but legacy tunnel
profile/health/tunnel-ID markers cannot be fully read because their generated
unit/config files are outside the MCP read roots.

This is a P6 host-execution prerequisite. The solution MUST be narrow and MUST
NOT expose tunnel credential contents merely to make reconciliation convenient.

## Existing lifecycle evidence

The captured implementation proves behavioral requirements for:

- generated MCP/tunnel user units and tunnel profiles;
- explicit instance/tunnel/profile/port isolation;
- listener/process/cgroup ownership checks;
- exact MCP initialization/session cleanup/workspace qualification;
- detached Codex jobs;
- exact saturation recovery signature with 30 s guard / 120 s cooldown;
- checkpointed reboot bridge/recovery;
- structured diagnostics and secret redaction;
- RO/RW systemd namespace generation.

The greenfield manager treats these as acceptance oracles, not architectural
source code.

## Secrets

The following values remain external and MUST NOT enter manager desired/applied
state or reports:

```text
CONTROL_PLANE_API_KEY
GitHub PAT/OAuth token values
SSH private key contents
Codex authentication/session data
```

Only non-secret references such as `tunnel.env_file` or optional
`github.config_dir` belong in desired state.
