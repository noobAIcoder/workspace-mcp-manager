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

