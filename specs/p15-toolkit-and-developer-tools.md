# P15 — Shared toolkit and developer-tool provisioning

Status: **NORMATIVE P15 SPECIFICATION**

P15 owns host-level provisioning that is intentionally separate from per-instance
deployment. It implements CAP-006 through CAP-010, CAP-121, CAP-122, and the P15
portion of CAP-123.

P15 MUST NOT authenticate GitHub or Codex, create/import private keys, mutate
known-host databases, configure repository Git identity/credential helpers, or
install P16 per-instance launchers.

## Capability authority

```text
CAP-006  Shared toolkit installer
CAP-007  Explicit toolkit --upgrade
CAP-008  Validate all existing configs before shared-manager upgrade
CAP-009  Atomic toolkit installation/copy
CAP-010  Refuse silent manager replacement while instances exist
CAP-121  Install/configure Codex CLI
CAP-122  Install/configure gh
CAP-123  SSH/GPG-agent setup (P15 portion)
```

## Architectural split

P15 has two independent layers.

### 1. Standalone shared-toolkit installer

A clean host cannot depend on an already-installed manager to install the
manager. The repository therefore provides a standalone Python installer:

```text
python3 scripts/install_toolkit.py [--prefix <absolute-path>] [--upgrade]
python3 scripts/install_toolkit.py --check [--prefix <absolute-path>]
```

The installer has no third-party Python dependencies and uses only the standard
library. It installs the manager source from the current checkout; it does not
download manager code from the network.

Default prefix:

```text
~/.local
```

Toolkit layout:

```text
<prefix>/lib/workspace-mcp-manager/
  releases/<source-fingerprint>/
    src/workspace_mcp_manager/...
    pyproject.toml
    bin/workspace-mcp-manager
    bin/workspace-mcp-reboot
    install.json
  current -> releases/<source-fingerprint>

<prefix>/bin/workspace-mcp-manager -> ../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager
<prefix>/bin/workspace-mcp-reboot  -> ../lib/workspace-mcp-manager/current/bin/workspace-mcp-reboot
```

The stable `<prefix>/bin` symlinks MUST NOT embed a release fingerprint so a
single atomic `current` switch upgrades both entrypoints together.

### Source fingerprint

The release fingerprint is SHA-256 over deterministic path/content records for:

```text
pyproject.toml
src/workspace_mcp_manager/**/*.py
```

Paths are sorted lexically. File modes, mtimes, repository metadata, tests,
reports, and local Git state do not affect the release fingerprint.

### Initial installation

Initial installation MUST:

1. validate Python >= 3.11;
2. validate the candidate source imports and parser construction;
3. stage a complete release under the target filesystem;
4. fsync staged regular files/directories where supported;
5. atomically rename the staged release into `releases/<fingerprint>`;
6. atomically switch `current` only after the release is complete;
7. create/repair the two stable `<prefix>/bin` symlinks atomically;
8. validate both installed entrypoints after the switch.

The installer MUST NOT create/update instance declarations or touch systemd.

### Replacement / explicit upgrade

If `current` already points to a different source fingerprint, installation
without `--upgrade` MUST fail. This is true even when no instances exist.

When one or more manager declarations exist under:

```text
~/.config/workspace-mcp-manager/instances/*.json
```

the candidate manager source MUST parse **every** declaration successfully
before the atomic `current` switch. Failure of any declaration aborts the
upgrade and leaves the current release untouched.

This is CAP-008/CAP-010 authority. The installer never silently replaces a
shared manager underneath deployed instances.

`--upgrade` authorizes only the shared toolkit switch. It does not restart or
apply instances. Existing systemd units that invoke the stable manager path will
use the new release only on subsequent manager invocations/process starts.

### Check mode

`--check` is read-only and reports:

```text
candidate_fingerprint
installed_fingerprint|null
installed=true|false
upgrade_required=true|false
instance_count
instances_valid=true|false
entrypoints_valid=true|false
```

## 2. Installed manager developer-tool provisioning

Public surface:

```text
workspace-mcp-manager host tools audit

workspace-mcp-manager host tools codex \
  --node-root <absolute-NVM-or-Node-root> [--version <semver>] [--upgrade]

workspace-mcp-manager host tools gh \
  [--source <absolute-executable> --sha256 <digest>] [--upgrade]

workspace-mcp-manager host tools agents audit
workspace-mcp-manager host tools agents configure
```

All mutating tool operations execute through the existing transient user-systemd
host boundary. Public MCP callers do not need direct home/system paths.

No operation accepts a token, private key, passphrase, Codex session, or GitHub
credential.

## Tool audit

`host tools audit` uses deterministic candidate paths in addition to system
`PATH`:

```text
~/.local/bin/uv
~/.local/bin/coding-tools-mcp
~/.local/bin/tunnel-client
~/.local/bin/workspace-mcp-manager
/usr/bin/gh
/usr/bin/ssh-agent
/usr/bin/gpg-agent
/usr/bin/gpgconf
```

For Codex/Node, audit also inspects NVM version roots under:

```text
~/.nvm/versions/node/*/bin/{node,npm,codex}
```

The audit reports paths/versions only. It MUST NOT inspect or emit auth/session
contents.

## Codex installation/configuration — CAP-121

OpenAI's supported npm package is:

```text
@openai/codex
```

P15 requires an explicitly selected existing Node root. Installing Node/NVM is
outside the frozen P15 capability matrix.

For:

```text
--node-root /home/user/.nvm/versions/node/vX.Y.Z
```

P15 requires executable:

```text
<node-root>/bin/node
<node-root>/bin/npm
```

and executes exactly one of:

```text
npm install -g @openai/codex
npm install -g @openai/codex@<explicit-version>
```

using `<node-root>/bin` at the front of PATH.

If Codex already exists under the selected root, replacement requires
`--upgrade` unless the requested explicit version already matches.

Post-install validation requires:

```text
<node-root>/bin/codex --version
```

The result reports:

```text
codex_path
codex_version
required_exec_path=<node-root>/bin
required_external_root=<node-root>
```

These last two fields are the deterministic values that P10 instance declarations
must use. P15 does not silently rewrite every instance declaration.

Codex login/authentication remains external (CAP-127).

## GitHub CLI installation/configuration — CAP-122

If `/usr/bin/gh` is already executable, `host tools gh` validates it and is a
NOOP unless an explicit user-local source artifact is supplied.

P15 does not invoke a privileged OS package manager. To install/upgrade `gh`,
the caller provides an already obtained executable artifact and its expected
SHA-256:

```text
--source /absolute/path/to/gh
--sha256 <64-lowercase-hex>
```

The manager verifies the digest and executable regular-file type, copies it into
a versioned manager-owned user-local release path, validates `gh --version`, and
atomically switches:

```text
~/.local/bin/gh
```

to the verified release. Existing foreign/non-manager `~/.local/bin/gh` content
is never overwritten without explicit `--upgrade`.

This keeps P15 non-privileged and deterministic. Acquisition of the official
GitHub CLI release artifact is outside the manager mutation itself; the manager
does not add package repositories or use sudo.

`gh auth login`, tokens, and actual auth state remain external under CAP-118 and
CAP-126. Existing P9 diagnostics remain the authority for reporting auth state.

## SSH/GPG agents — P15 portion of CAP-123

CAP-123 is explicitly targeted `P15/Post-MVP`. P15 owns the prerequisite and
configuration **audit** portion only:

```text
/usr/bin/ssh-agent
/usr/bin/gpg-agent
/usr/bin/gpgconf
current SSH_AUTH_SOCK presence
candidate manager-owned socket/unit/environment paths
```

P15 does not create/import keys, change trust databases, manage passphrases, or
replace an operator's existing agent. CAP-124/CAP-125 remain external.

The reserved command:

```text
workspace-mcp-manager host tools agents configure
```

fails closed with `FEATURE_NOT_IMPLEMENTED` in P15. Persistent manager-owned
SSH-agent service/socket creation and GPG-agent launch policy are the explicit
post-MVP remainder of CAP-123.

## Failure / redaction rules

- command stdout/stderr is bounded and passed through existing redaction;
- no secret-bearing environment values are persisted;
- mutating operations persist only non-secret result metadata under
  `~/.local/state/workspace-mcp-manager/tooling/`;
- a failed tool install MUST NOT rewrite instance desired state;
- toolkit source upgrade failure MUST leave `current` unchanged;
- SSH-agent setup failure MUST fail closed on foreign/modified owned resources.

## P15 qualification

Pure verification MUST cover:

1. source fingerprint determinism;
2. initial standalone install into an isolated prefix;
3. no-op reinstall of the same fingerprint;
4. differing replacement rejected without `--upgrade`;
5. every existing declaration validated before an upgrade switch;
6. invalid declaration prevents switch;
7. atomic current/stable-link behavior and candidate validation;
8. deterministic explicit-path tool audit;
9. Codex npm argv/version-root/P10 path contract;
10. Codex replacement requires explicit `--upgrade`;
11. gh artifact digest validation, atomic user-local install, and foreign-path
    fail-closed behavior;
12. SSH/GPG agent prerequisite audit and fail-closed reserved configure command;
13. no key/token/session operations;
14. full P6–P14 regression suite.

Live WSL qualification MUST be non-destructive to existing instances and:

1. run standalone installer `--check` against the current source;
2. install the candidate toolkit into an isolated temporary prefix and prove
   both entrypoints execute;
3. prove replacement of a modified candidate requires `--upgrade` in the
   isolated prefix;
4. audit actual WSL tools using explicit paths;
5. qualify Codex against the existing NVM v24.15.0 root without changing auth;
6. qualify existing `/usr/bin/gh` as a NOOP;
7. qualify agent audit and confirm `agents configure` remains a fail-closed
   post-MVP reservation without mutating the operator's existing agent;
8. leave `manager-qual` fingerprint/no-access/all-NOOP and endpoints unchanged.

The live gate MUST NOT upgrade the actual installed shared manager during its
own qualification. Candidate atomic upgrade semantics are proven in an isolated
prefix; the repository/installed development manager remains under the existing
Git/uv-tool workflow during P15 implementation.
