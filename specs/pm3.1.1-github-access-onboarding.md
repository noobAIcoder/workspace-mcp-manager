# PM3.1.1 — Per-instance GitHub access onboarding

Status: **NORMATIVE IMPLEMENTATION SPECIFICATION**

PM3.1.1 extends qualified PM3.1 with a registered-instance lifecycle for
GitHub CLI/API access. The accepted PM3.1.1 TODO is authoritative where this
implementation specification is silent.

PM3.1.1 narrowly supersedes PM1/PM3.1's statement that GitHub authentication
is wholly external. The manager now owns orchestration of credential onboarding
only for `github.mode=managed`; GitHub CLI remains authoritative for credential
representation and persistence.

## Contracts

```text
config_version                             = 2
github_access_projection_version           = 1
github_access_verification_record_version  = 1
```

Existing PM3/PM3.1 contract versions are unchanged.

## Authority and invariants

The manager MUST remain authoritative for instance identity, managed profile
identity, resolved GitHub CLI binary, verification environment, repository Git
transport, operation serialization, verification freshness/context, and the
deployed MCP verification target.

GitHub CLI MUST remain authoritative for credential acceptance, credential
representation, profile-local persistence, authenticated account identity, and
GitHub API behavior.

The following invariants are normative:

```text
INV-SECRET-NO-PUBLIC
INV-MANAGED-SOURCE
INV-PROFILE-AUTHORITY
INV-GIT-SEPARATION
INV-EXTERNAL-NO-MUTATION
INV-BOUNDED-EXECUTION
INV-OBSERVED-NOT-DESIRED
```

Credential bytes MUST NOT enter declarations, candidate/request contracts,
argv, token-bearing environment variables, manager JSON, Textual state, logs,
diagnostics, plans, provenance, verification records, or retained qualification
evidence.

## Managed profile authority

For `github.mode=managed`, the canonical profile is the existing PM1 path:

```text
~/.config/workspace-mcp-manager/github/<instance-id>/
```

The manager MUST derive this path from the registered/effective instance ID.
Frontends MUST NOT provide a managed `github.config_dir` or `github.binary`.

The managed profile directory MUST be mode `0700`. Its PM1 marker MUST be:

```text
path     <profile>/.owner
mode     0600
content  workspace-mcp-manager-instance=<instance-id>\n
```

Provisioning MUST require an already-registered declaration, MUST be
idempotent, MUST reject symlinks/unexpected types/foreign ownership, and MUST
not inspect unrelated profile contents.

For new/effective candidates:

```text
github.mode=managed
  -> config_dir = PM1 canonical path
  -> binary = real absolute `gh` resolved from effective mcp.exec_path,
              or null when unavailable
```

`github.mode=disabled` MUST materialize `config_dir=null` and `binary=null`.
External declarations retain their explicit path/binary semantics.

## Qualified GitHub CLI

Initial qualified provider/version:

```text
provider = github.com
gh       = 2.45.0
```

Version detection executes exactly:

```text
[<absolute-gh-binary>, "--version"]
```

The first output line MUST match `gh version <semver> ...`. Configuration MUST
fail closed unless the normalized version is exactly `2.45.0`.

The qualified login argv is exactly:

```text
[
  <absolute-gh-binary>,
  "auth", "login",
  "--hostname", "github.com",
  "--with-token",
  "--insecure-storage"
]
```

`--git-protocol` and `gh auth setup-git` MUST NOT be used.

For `gh 2.45.0` with this invocation, profile-local credential persistence is
qualified only when `<GH_CONFIG_DIR>/hosts.yml` exists after a successful
login and is a non-symlink regular file owned by the current UID with no
group/world permission bits. The manager MUST validate metadata only and MUST
NOT read `hosts.yml` contents.

## Managed execution environment

All manager-owned managed-GitHub subprocesses MUST remove:

```text
GH_TOKEN
GITHUB_TOKEN
GH_ENTERPRISE_TOKEN
GITHUB_ENTERPRISE_TOKEN
GH_HOST
GH_DEBUG
GH_FORCE_TTY
GH_REPO
GH_PATH
```

They MUST establish:

```text
HOME=<real account home>
PATH=<effective mcp.exec_path>
GH_CONFIG_DIR=<canonical managed profile>
GIT_TERMINAL_PROMPT=0
GH_PROMPT_DISABLED=1
GH_NO_UPDATE_NOTIFIER=1
```

`SSH_AUTH_SOCK` is included only when required by the operation.

The generated MCP unit MUST contain this exact service directive:

```text
UnsetEnvironment=GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GH_HOST GH_DEBUG GH_FORCE_TTY GH_REPO GH_PATH
```

and MUST explicitly set `GH_CONFIG_DIR` when configured.

The generated HTTPS-gh helper MUST `unset` the same variables before executing
`<configured-gh> auth git-credential "$@"`; it MUST also establish the same
non-secret HOME/PATH/GH_CONFIG_DIR/prompt controls. This changes only the
credential source boundary and MUST NOT change Git transport authority.

## Interactive credential helper

Credential configuration MUST use a dedicated foreground helper attached to
the controlling terminal. The normal JSON/systemd-run bridge and Textual
mutation runner MUST NOT transport credential bytes.

The parent may pass only non-secret manager-derived values:

```text
instance ID
canonical profile path
absolute qualified gh binary
real HOME
effective PATH
bounded timeout
```

The helper MUST:

1. open `/dev/tty` directly and require `isatty()`;
2. retain/enable terminal `ECHO` for the single input line;
3. display the visible-input warning before reading input;
4. read exactly one bounded line and remove only terminal CR/LF termination;
5. reject empty input before starting GitHub CLI;
6. execute under `umask 0077`;
7. pass the credential only to the child stdin;
8. direct child stdout/stderr to `DEVNULL`;
9. use direct argv, `shell=false`, `start_new_session=false`;
10. perform at most one login attempt;
11. bound execution to 120 seconds;
12. settle timeout/interruption with terminate then kill if required;
13. return only a coarse process outcome;
14. drop the Python credential reference after handoff as best-effort hygiene.

Helper outcomes are:

```text
succeeded | failed | timeout | interrupted | cancelled
```

They MUST remain distinct from the resulting authentication state.

## Operation serialization

GitHub-access configure/reconfigure/verify operations MUST use an advisory
per-instance `flock` under:

```text
~/.local/state/workspace-mcp-manager/github-access/.locks/<instance-id>.lock
```

Lock files MUST be mode `0600` and contain no credentials. Acquisition is
bounded to two seconds using non-blocking retries. POSIX lock release on
process exit is the stale-lock recovery rule. A competing operation returns
`GITHUB_ACCESS_BUSY`.

Verification-record replacement MUST be atomic and mode `0600`.

## Status and verification record

`status` MUST be provider/network-free. It may execute only bounded local
metadata/version/Git/systemd inspection and combine it with the last secret-free
record.

Records are stored under:

```text
~/.local/state/workspace-mcp-manager/github-access/<instance-id>.json
```

and may contain only the v1 fields defined by the accepted TODO. Raw command
output, environment dumps, provider payloads, authorization data, and profile
contents MUST NOT be persisted.

Verification freshness is:

```text
fresh  <= 300 seconds and context fingerprint matches
stale  > 300 seconds or context fingerprint differs
never  no record
```

The context fingerprint MUST include instance ID, profile mode/path/safety,
GitHub binary realpath/version, repository identity when known, deployment
state, and MCP service/endpoint identity. Context mismatch always overrides a
young timestamp to `stale`.

## Fresh verification

Managed profile authentication executes exactly:

```text
[<gh>, "auth", "status", "--hostname", "github.com"]
```

No `--show-token`, `gh auth token`, or profile-content parsing is permitted.
Only exit status `0` is affirmative authentication evidence.

Account identity then executes exactly:

```text
[<gh>, "api", "--hostname", "github.com", "user", "--jq", ".login"]
```

Only a bounded single-line GitHub login matching `[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})`
may enter public/persisted state.

Where a GitHub repository identity is manager-derivable from the configured
remote, repository read executes exactly:

```text
[<gh>, "api", "--hostname", "github.com", "repos/<owner>/<repo>", "--jq", ".full_name"]
```

Repository failure MUST NOT erase successful global authentication.

External-profile verification is initially fail-closed as:

```text
verification = unavailable
reason = EXTERNAL_PROFILE_WRITE_PROTECTION_UNAVAILABLE
```

because this implementation does not claim a write-prevention boundary for an
arbitrary GitHub CLI profile. External profiles MUST never be copied or mutated.

## Deployed MCP execution verification

Qualified MCP contract:

```text
coding-tools-mcp version  0.2.2
protocol                  2025-11-25
transport                 Streamable HTTP
local endpoint            http://<mcp.host>:<mcp.port>/mcp
tool                      exec_command
tool timeout_ms           8000
manager request timeout   12 seconds
max_output_bytes          8192
preview_bytes             4096
verbosity                 full
```

The manager MUST initialize a real MCP session, send
`notifications/initialized`, and call `exec_command` with one manager-constructed
shell command equal to `shlex.join()` of the exact account-identity argv above.
No operator-controlled command fragment is allowed.

Success requires JSON-RPC success, `isError=false`, structured result `ok=true`,
command exit `0`, non-truncated bounded stdout, and a validated account identity
matching independent profile/manager verification. The session SHOULD be
deleted after the call. Merely inspecting a unit/environment is not MCP
execution verification.

No deployed service yields `not_deployed`; an inactive service yields
`unavailable`; execution/protocol failure yields `failed`/`unknown` with a
bounded reason code.

## Public task surface

The public CLI is:

```text
workspace-mcp-manager instance github-access status <instance-id>
workspace-mcp-manager instance github-access verify <instance-id>
workspace-mcp-manager instance github-access configure <instance-id>
```

`status` and `verify` may cross the existing secret-free host JSON bridge.
`configure` MUST execute directly in the foreground with the controlling TTY
and MUST NOT accept token arguments, token stdin, JSON credentials, or
credential environment variables.

`configure` requires `github.mode=managed`. Disabled-to-managed enablement is
an ordinary candidate/preview/conditional declaration update and MUST complete
before configure begins. External-to-managed migration is not provided.

## Textual workflow

New-instance Step 4 is named:

```text
Git, GitHub & Folder Access
```

It MUST visually separate:

```text
Git Repository
GitHub CLI Access
Git Transport
Folder Access
```

The GitHub section exposes managed/disabled intent and workflow-only
`Configure during creation | Configure later`. It MUST NOT contain a credential
Input widget. The provider token-management action is the manager-projected
credential-free destination `https://github.com/settings/tokens`.

Configure-during-creation sequencing is declaration registration -> managed
profile provisioning -> foreground helper -> fresh authoritative verification
-> optional deployment. PM3 reviewed-plan/fingerprint protections remain in
force: if provisioning changes the reviewed reconciliation context, deployment
MUST stop for re-review rather than bypass PM3 concurrency protection.

Existing-instance Git/GitHub view MUST consume `github_access_status`, provide
fresh `Re-check`, managed Configure/Reconfigure, disabled Enable-managed via
ordinary Settings, and no external credential mutation control.

Textual MUST suspend terminal UI interaction around the foreground helper and
resume/refresh authoritative status afterward. Credential bytes MUST never
enter Textual state.

## Failure conditions

Implementation MUST fail closed for the accepted TODO failure conditions,
including any secret leak, unsafe/foreign profile, non-canonical managed path,
unsupported GitHub CLI, unbounded execution, stale/context-invalid success,
MCP verification without real MCP execution, repository denial misclassified
as global authentication failure, external-profile mutation, Git-transport
mutation, or operation race.

## Qualification

Deterministic qualification MUST cover the accepted 90-item matrix using unit,
fake-`gh`, pseudo-TTY, Textual Pilot, source/environment, and regression tests.
The synthetic secret sentinel is:

```text
github_pat_PM311_DO_NOT_LEAK_123456
```

It may appear only in controlled fake-`gh` stdin and a temporary test-local PTY
transcript that is deleted before qualification completes.

Automated/live implementation qualification MUST NOT authenticate, log out, or
otherwise mutate a real GitHub credential. ElectroCAD checks are non-mutating;
LeadBot is a read-only runtime reference.

Successful implementation qualification ends at:

```text
PM3_1_1_IMPLEMENTATION_QUALIFICATION=PASS
PM3_1_1_GITHUB_ACCESS_ONBOARDING=AWAITING_OPERATOR_VALIDATION
```

Only sanitized operator validation of one real managed-profile onboarding may
later emit:

```text
PM3_1_1_OPERATOR_AUTH_VALIDATION=PASS
PM3_1_1_GITHUB_ACCESS_ONBOARDING=PASS
```
