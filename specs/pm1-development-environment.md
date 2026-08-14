# PM1 — Per-instance development environment

Status: **NORMATIVE POST-MVP SPECIFICATION**

PM1 implements the first post-MVP capability group without changing the frozen
P16 MVP contract:

```text
CAP-117  Create/manage per-instance GH_CONFIG_DIR
CAP-119  Configure Git repository identity
CAP-120  Configure repository Git protocol / credential helper
CAP-123  Persistent SSH/GPG-agent setup — post-MVP remainder
```

Authentication material remains external. PM1 MUST NOT accept, persist, copy,
inspect, fingerprint, back up, or delete GitHub tokens, private keys,
passphrases, known-host databases, Codex sessions, or GPG trust/key databases.

## Configuration version

PM1 introduces declaration `config_version=2`. Version 1 declarations remain
valid and retain exactly their existing semantics and canonical serialization.
They implicitly have no manager-owned Git identity/remote and no configured
agent provider.

Version 2 adds required `git` and `agent` objects and extends `github`.

```json
{
  "config_version": 2,
  "github": {
    "mode": "managed",
    "config_dir": "/home/user/.config/workspace-mcp-manager/github/sample",
    "binary": "/usr/bin/gh"
  },
  "git": {
    "identity": {
      "name": "Example User",
      "email": "example@example.invalid"
    },
    "remote": {
      "name": "origin",
      "protocol": "ssh"
    }
  },
  "agent": {
    "mode": "managed-ssh-agent",
    "ssh_auth_sock": null
  }
}
```

### GitHub modes

`github.mode` is one of:

- `disabled`: `config_dir` MUST be null;
- `external`: `config_dir` MUST be an explicit absolute path and the manager
  MUST NOT create/delete it;
- `managed`: `config_dir` MUST equal
  `~/.config/workspace-mcp-manager/github/<instance>` for the manager account.

`github.binary` is null or an explicit absolute executable path. It is required
when `git.remote.protocol=https-gh`.

### Git configuration

`git.identity` is null or contains non-empty `name` and `email` strings.

`git.remote` is null or contains:

```text
name      validated Git remote name
protocol  ssh | https-gh
```

PM1 manages only GitHub remotes. A configured remote MUST already identify a
`github.com/<owner>/<repository>` repository. The manager derives the alternate
SSH/HTTPS URL from that existing identity; declarations MUST NOT duplicate a
repository URL or embed credentials.

`protocol=ssh` requires a non-`none` agent provider.

`protocol=https-gh` requires a configured `GH_CONFIG_DIR` and explicit
`github.binary`.

### Agent modes

`agent.mode` is one of:

- `none`: `ssh_auth_sock` MUST be null;
- `external`: `ssh_auth_sock` MUST be an explicit absolute Unix-socket path;
- `managed-ssh-agent`: `ssh_auth_sock` MUST be null and the manager derives a
  stable per-instance runtime socket.

PM1 does not manage SSH/GPG keys. `external` is the supported integration path
for an operator-owned `ssh-agent` or `gpg-agent` SSH socket. PM1 does not
configure or mutate `~/.gnupg`.

## CAP-117 — managed GitHub profile

For `github.mode=managed`, normal instance generation owns:

```text
~/.config/workspace-mcp-manager/github/<instance>/
~/.config/workspace-mcp-manager/github/<instance>/.owner
```

The directory is mode `0700`; `.owner` is mode `0600` and contains the exact
instance ownership token. The profile directory is **retained** on instance
removal and when authentication data exists. The manager MUST NOT recursively
inspect or delete its other contents.

`GH_CONFIG_DIR` is propagated consistently into:

- the MCP service;
- Git/GitHub diagnostics;
- detached Codex jobs;
- the generated HTTPS credential helper.

External GitHub profiles are propagated but are never generated resources.

## CAP-119 — repository-local Git identity

PM1 MUST NOT mutate system or global Git configuration.

The following local repository keys are manager authority only when ownership
is proven:

```text
user.name
user.email
workspaceMcpManager.identityOwner
workspaceMcpManager.identityVersion
workspaceMcpManager.identityOrigin
workspaceMcpManager.identityOriginalName
workspaceMcpManager.identityOriginalEmail
```

`identityOwner=<instance>` and `identityVersion=1` establish ownership.
`identityOrigin` is `created` or `adopted`. For an adopted identity, the
original non-secret name/email are retained in local Git metadata so cleanup is
exactly reversible.

When no owner marker exists:

- absent identity MAY be created;
- an identity exactly equal to desired state MAY be adopted by adding manager
  ownership/original-value metadata without changing the identity;
- a different existing identity is FOREIGN and MUST produce a conflict.

When the exact owner/version marker exists, normal reconciliation MAY update or
remove the manager-owned identity keys. A foreign owner or unsupported ownership
version MUST fail closed when identity management is requested.

On `deployment=absent` or `git.identity=null`, an adopted identity MUST be
restored to its recorded original values; a manager-created identity MUST be
removed. Manager ownership/original-value metadata is then removed. Foreign
identity keys MUST remain untouched.

## CAP-120 — repository transport

PM1 owns these local repository keys only under the exact remote ownership
marker:

```text
remote.<name>.url
credential.https://github.com.helper     # https-gh only
core.sshCommand                          # ssh only
workspaceMcpManager.remoteOwner
workspaceMcpManager.remoteVersion
workspaceMcpManager.remoteName
workspaceMcpManager.remoteOriginalUrl
```

`remoteOwner=<instance>` and `remoteVersion=1` establish ownership. The exact
managed remote name and original credential-free GitHub URL are retained as
non-secret local metadata so cleanup is deterministic and reversible.

When no owner marker exists, the configured remote URL MUST already equal the
derived desired protocol URL before the manager may adopt it. A different URL
is FOREIGN and MUST conflict. PM1 never changes a foreign remote merely because
the same GitHub repository can be inferred.

Adoption also requires the repository-local helper and SSH-command keys to be
absent. On `deployment=absent` or `git.remote=null`, PM1 restores the recorded
original URL, removes only its local transport keys, and removes its ownership
metadata.

### SSH

Canonical managed URL:

```text
git@github.com:<owner>/<repository>.git
```

Authentication is supplied only through the configured agent provider.
PM1 pins repository-local `core.sshCommand` to `/usr/bin/ssh` with
`BatchMode=yes` and `IdentityAgent=<effective SSH socket>`, so detached tooling
does not depend on an ambient interactive-shell agent variable.

### HTTPS + GitHub CLI

Canonical managed URL:

```text
https://github.com/<owner>/<repository>.git
```

PM1 generates an executable manager-owned helper:

```text
~/.local/bin/<instance>-git-credential-github
```

The helper sets only the configured `GH_CONFIG_DIR` and executes exactly:

```text
<github.binary> auth git-credential "$@"
```

The repository-local credential helper key points to that absolute helper path.
The helper contains no credential material. Switching away from `https-gh`
MUST remove the obsolete manager-owned helper after Git configuration has been
reconciled away from it.

## CAP-123 — agent provider

### Managed SSH agent

`managed-ssh-agent` generates:

```text
workspace-mcp-ssh-agent-<instance>.service
```

The service runs `/usr/bin/ssh-agent` in foreground mode with one stable socket
under the account runtime directory. The agent unit MUST start before the MCP
unit and stop after dependent MCP/tunnel units.

The manager owns the unit/process/socket namespace, but not identities loaded
into the agent. Restart/reboot therefore recreates an empty agent unless an
external operator mechanism loads keys.

### External provider

For `agent.mode=external`, the manager validates that the configured path is an
existing Unix socket before a present deployment may apply. It injects the
exact path as `SSH_AUTH_SOCK` but MUST NOT start, stop, delete, chmod, or
otherwise mutate the provider.

The configured socket parent is added to the MCP external-root allowance only
for filesystem reachability; this does not transfer ownership of the provider.

## Environment propagation

When configured, MCP execution uses:

```text
GH_CONFIG_DIR=<effective instance GitHub profile>
SSH_AUTH_SOCK=<effective instance SSH-agent socket>
```

Detached Codex execution continues to receive the instance `GH_CONFIG_DIR`.
Git-over-SSH inside Codex uses the repository-local `core.sshCommand` contract,
so it is bound to the same agent without depending on an ambient shell socket.

Git diagnostics use the same environment. `HOME` remains the real account home,
`PATH` remains `mcp.exec_path`, and `GIT_TERMINAL_PROMPT=0` remains mandatory.

## Planning and transactions

Git configuration is a shared foreign file and MUST NOT be modeled as a whole
generated resource. `instance plan` adds ownership-aware grouped operations for:

```text
dev-git-identity
dev-git-transport
```

Apply MUST recompute the plan on the host boundary, back up the exact local Git
config before the first Git mutation, execute Git with `GIT_TERMINAL_PROMPT=0`,
and restore the original local config if the transaction fails.

Generated credential-helper and managed-agent resources participate in normal
resource ownership and stale-resource cleanup. Obsolete manager-owned agent
units MUST stop/disable before removal.

## Failure conditions

PM1 MUST fail closed when:

- a managed GitHub profile path is not the canonical instance path;
- an external agent path is not a Unix socket;
- identity/remote ownership belongs to another instance;
- a foreign identity differs from desired state;
- a foreign remote URL differs from the desired canonical protocol URL;
- a configured remote is not an existing GitHub repository URL;
- HTTPS mode lacks an explicit GitHub CLI executable/profile;
- SSH mode lacks an agent provider;
- generated helper/agent resources fail normal ownership validation.

## Qualification

Pure verification MUST cover:

1. v1 parsing/serialization/fingerprints remain stable;
2. strict v2 schema and cross-field validation;
3. managed/external/disabled GitHub profile behavior;
4. exact-adoption, conflict, update, remove and rollback semantics for identity;
5. exact-adoption, conflict, protocol-switch and remove semantics for remote;
6. HTTPS helper generation and stale cleanup;
7. managed-agent unit generation/order/stale cleanup;
8. external-socket validation and environment propagation;
9. no global Git mutation or secret-bearing desired/applied state;
10. full P6-P16 regression suite.

Live WSL qualification on `manager-qual` MUST be reversible and prove:

1. migrate a disposable declaration copy to v2;
2. create the managed GitHub profile without authenticating it;
3. adopt exact existing local Git identity or use isolated qualification values;
4. add/adopt a temporary exact SSH qualification remote without changing the
   repository's pre-existing remote identity;
5. start the managed SSH-agent unit and expose its stable socket to MCP;
6. switch the temporary remote to `https-gh`, exercise the generated helper in
   non-authenticating `--help` mode, and return to SSH with stale-helper cleanup;
7. restore the original declaration/repository-local Git state;
8. final manager plan all `NOOP`, MCP discovery healthy, tunnel health/readiness
   healthy, and complete pure suite green.

Final gate:

```text
PM1_DEVELOPMENT_ENVIRONMENT_QUALIFICATION=PASS
```
