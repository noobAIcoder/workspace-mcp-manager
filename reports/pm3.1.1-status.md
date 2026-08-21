# PM3.1.1 — Per-Instance GitHub Access Onboarding Status

Status: **IMPLEMENTATION QUALIFIED — AWAITING OPERATOR VALIDATION**

PM3.1.1 implements the accepted per-instance GitHub CLI/API access lifecycle on
top of qualified PM3.1. The implementation does not mark final GitHub access
onboarding complete because the required real-credential operator validation has
not yet been performed.

## Implementation authority snapshot

```text
repository: workspace-mcp-manager
branch: main
authority HEAD: df465ff203f570eb0079b7bdfc679b0513abc2bf
qualified PM3.1 implementation parent: 1f8cc85d98f6bf672de82fb77b68616e1034a404
qualified PM3.1 hardened authority: df465ff203f570eb0079b7bdfc679b0513abc2bf
qualified PM3 parent: 20900125fe9800690313271592befacd7979c597
qualified PM2 parent: 7a84c1c4ad31b9115cc66d1a91f92ea08bde8dcc

pre-existing unrelated worktree state:
  scripts/install_toolkit.sh executable-bit change only
  preserved and excluded from the PM3.1.1 commit

workspace-mcp-manager: 0.1.1
GitHub CLI binary: /usr/bin/gh
GitHub CLI raw version: gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)
GitHub CLI normalized version: 2.45.0
GitHub CLI supported: true
coding-tools-mcp: 0.2.2
MCP protocol: 2025-11-25
qualified MCP execution tool: exec_command
Textual: 8.2.8
```

## Qualified implementation

- `github.mode=managed` is frontend intent only. The manager derives the
  canonical PM1 `GH_CONFIG_DIR` and resolves `github.binary` from the effective
  MCP execution `PATH`.
- Registered managed profiles are provisioned idempotently with the existing
  PM1 owner marker and fail closed on foreign, symlinked, wrong-type, or unsafe
  metadata.
- Existing unsafe `hosts.yml` storage is rejected before any authentication or
  API command can execute. Credential-bearing file contents are never read.
- Managed GitHub execution removes ambient `GH_TOKEN`, `GITHUB_TOKEN`,
  enterprise-token, host/debug/repository/path override variables and pins the
  canonical profile, real account `HOME`, desired execution `PATH`, disabled
  prompting, and update-notifier behavior.
- Generated MCP units explicitly use `UnsetEnvironment=` for managed GitHub
  override variables. Generated HTTPS-gh helpers independently neutralize the
  same sources before `gh auth git-credential`.
- Credential onboarding uses a dedicated foreground helper attached to the
  controlling terminal. Textual never owns a credential widget or credential
  string.
- Credential input intentionally uses normal terminal echo. The helper passes
  the credential only to one qualified `gh auth login` invocation through
  stdin, discards secret-bearing child output, and performs no automatic retry.
- CLI and Textual onboarding now show the same fine-grained PAT setup guidance
  for coding-tools repository work: select only the target repository, with
  Contents RW, Issues RW, Metadata RO (required), and Pull requests RW. Where
  known, the exact repository owner/name is projected without exposing secret
  material.
- Qualified `gh 2.45.0` login is pinned to:

  ```text
  gh auth login --hostname github.com --with-token --insecure-storage
  ```

- Status is network-free and combines local metadata with a versioned,
  secret-free observation record. Verification is separately serialized,
  freshness-aware, and context-aware.
- Verification independently projects profile authentication, manager
  environment, deployed MCP execution, repository read access, Git transport,
  and SSH-agent readiness.
- MCP verification uses the actual deployed coding-tools MCP `exec_command`
  surface with the configured absolute `github.binary` and requires the MCP
  account identity to match independent profile verification.
- External profiles are never configured, copied, re-owned, logged out, or
  migrated. Fresh verification fails closed as unavailable where write
  prevention cannot be guaranteed.
- GitHub CLI/API access does not invoke `gh auth setup-git`, does not pass
  `--git-protocol`, and does not mutate repository remotes, SSH configuration,
  or Git transport ownership.

## Public task surface

```text
workspace-mcp-manager instance github-access status <instance-id>
workspace-mcp-manager instance github-access verify <instance-id>
workspace-mcp-manager instance github-access configure <instance-id>
```

`configure` accepts no credential argument, file, JSON field, or environment
credential interface.

## Textual workflow

The new-instance step is now:

```text
Git, GitHub & Folder Access
```

It presents Git repository state, managed GitHub access intent,
configure-now/configure-later workflow state, provider-owned token management,
Git transport, and external folder access as separate concepts.

Existing instances expose:

```text
disabled -> Enable managed GitHub access -> reviewed declaration update
managed  -> Re-check | Configure/Reconfigure GitHub access
external -> Re-check only; credential mutation unavailable
```

Configure-now occurs only after declaration registration. Textual suspends while
the foreground helper owns the terminal and resumes with authoritative status
refresh afterward.

## Deterministic and integration verification

Final source suite:

```text
Ran 323 tests
OK (skipped=30)
```

The skips are environment-dependent tests, primarily Textual/PTY cases that are
qualified separately in the isolated runtime/host layer.

Additional PM3.1.1 verification established:

```text
fake-gh exact argv and stdin-only credential handoff: PASS
ambient credential-source isolation: PASS
managed profile ownership/symlink/storage safety: PASS
unsafe hosts.yml pre-auth fail-closed behavior: PASS
operation serialization: PASS
verification-record atomicity/context invalidation: PASS
MCP exec_command request/identity-match contract: PASS
secret-sentinel scan: PASS
ElectroCAD non-mutating dry run: PASS
all 28 Textual Pilot tests in isolated Textual 8.2.8 runtime: PASS
fine-grained PAT CLI/TUI guidance projection and rendering: PASS
explicit Git & GitHub settings action and focused reviewed edit surface: PASS
focused github.mode edit -> manager candidate request: PASS
pseudo-TTY fine-grained PAT permission instructions: PASS
pseudo-TTY visible terminal echo in isolated runtime: PASS
```

Linux PTY master `EIO` after slave closure is normalized to EOF in the test
harness; this is terminal lifecycle behavior, not a helper authentication
failure.

## Live WSL qualification

The final PM3.1.1 qualification was run as a transient user-systemd unit so
parent regression restarts could not invalidate the initiating MCP connection.
Final unit result:

```text
Result=success
ExecMainStatus=0
ActiveState=inactive
SubState=dead
```

The successful script includes specification validation, deterministic tests,
candidate toolkit installation, isolated Textual Pilot, pseudo-TTY
qualification, registered-instance secret-free status, MCP verification
contract, LeadBot boundary classification, and PM3.1/PM3/PM2 parent regression.

## ElectroCAD live read-only observation

The existing ElectroCAD instance preserves the required independent state:

```text
repository: noobAIcoder/ElectroCAD
Git transport: ssh
Git transport readiness: ready
SSH agent: available
GitHub CLI: /usr/bin/gh 2.45.0, supported
managed GitHub access: disabled / not_applicable
enable_managed_allowed: true
```

No repository remote, Git ownership metadata, SSH configuration, or GitHub
credential was modified by this observation.

## LeadBot read-only runtime reference

`LeadBotCodingTools` was observed only through its deployed MCP execution
surface. No credential/profile contents or authentication configuration were
read or changed.

```text
coding-tools-mcp: 0.2.2
protocol: 2025-11-25
GH_CONFIG_DIR present: true
GH_CONFIG_DIR: /home/cloudtoor/.config/workspace-mcp/leadbot-gh
gh binary: /usr/bin/gh
gh version: 2.45.0
non-secret account identity: noobAIcoder
SSH_AUTH_SOCK present: true
SSH_AUTH_SOCK: /run/user/1000/gnupg/S.gpg-agent.ssh
repository Git protocol: https
repository host: github.com
```

The LeadBot profile path is reference evidence only and is explicitly **not**
PM3.1.1 canonical managed-profile-path authority.

## Implementation qualification markers

```text
PM3_1_1_SPECIFICATION=PASS
PM3_1_1_AUTHORITY_SUPERSESSION=PASS
PM3_1_1_GITHUB_ACCESS_PROJECTION=PASS
PM3_1_1_MANAGED_PROFILE=PASS
PM3_1_1_PROFILE_OWNERSHIP=PASS
PM3_1_1_CREDENTIAL_STORAGE_SEPARATION=PASS
PM3_1_1_CREDENTIAL_SOURCE_ISOLATION=PASS
PM3_1_1_PROFILE_STORAGE_SAFETY=PASS
PM3_1_1_GH_VERSION_CONTRACT=PASS
PM3_1_1_VISIBLE_CREDENTIAL_INPUT=PASS
PM3_1_1_HELPER_PROCESS_BOUNDARY=PASS
PM3_1_1_SECRET_BOUNDARY=PASS
PM3_1_1_GH_STDIN_HANDOFF=PASS
PM3_1_1_GH_STORAGE_CONTRACT=PASS
PM3_1_1_TTY_REQUIREMENT=PASS
PM3_1_1_OPERATION_SERIALIZATION=PASS
PM3_1_1_PROFILE_VERIFICATION=PASS
PM3_1_1_MANAGER_ENV_VERIFICATION=PASS
PM3_1_1_MCP_EXECUTION_VERIFICATION=PASS
PM3_1_1_REPOSITORY_READ_VERIFICATION=PASS
PM3_1_1_VERIFICATION_CONTEXT=PASS
PM3_1_1_VERIFICATION_FRESHNESS=PASS
PM3_1_1_CREATION_WORKFLOW=PASS
PM3_1_1_EXISTING_INSTANCE_WORKFLOW=PASS
PM3_1_1_DISABLED_TO_MANAGED=PASS
PM3_1_1_RECONFIGURATION=PASS
PM3_1_1_CANCELLATION=PASS
PM3_1_1_GIT_TRANSPORT_SEPARATION=PASS
PM3_1_1_EXTERNAL_PROFILE_READ_ONLY=PASS
PM3_1_1_SECRET_LEAK_SCAN=PASS
PM3_1_1_ELECTROCAD_DRY_RUN=PASS
PM3_1_1_LEADBOT_REFERENCE=PASS
PM3_1_1_TEXTUAL_PILOT=PASS
PM3_1_1_PM3_1_REGRESSION=PASS
PM3_1_1_PM3_REGRESSION=PASS
PM3_1_1_PM2_REGRESSION=PASS
PM3_1_1_IMPLEMENTATION_QUALIFICATION=PASS

PM3_1_1_GITHUB_ACCESS_ONBOARDING=AWAITING_OPERATOR_VALIDATION
```

`PM3_1_1_OPERATOR_AUTH_VALIDATION=PASS` and final
`PM3_1_1_GITHUB_ACCESS_ONBOARDING=PASS` MUST NOT be emitted before the operator
completes the real-authentication validation below.

## Operator real-authentication validation

After pulling/installing this implementation, use one dedicated managed profile
(ElectroCAD is suitable because its Git transport is already independently SSH
ready).

1. Open the TUI and enable managed GitHub access through the reviewed settings
   flow if the instance is currently disabled:

   ```sh
   workspace-mcp-manager-tui
   ```

2. Run foreground configuration from the terminal:

   ```sh
   workspace-mcp-manager instance github-access configure electrocad --pretty
   ```

   The helper MUST display the visible-input warning. Paste the real GitHub
   fine-grained PAT only into that terminal prompt. Before the prompt, both the
   TUI and CLI helper show the recommended repository-scoped permissions:

   ```text
   Repository access: Only select repositories
   Contents: Read and write
   Issues: Read and write
   Metadata: Read-only (required)
   Pull requests: Read and write
   ```

   Do not capture or return the token-bearing terminal transcript.

3. Run fresh verification:

   ```sh
   workspace-mcp-manager instance github-access verify electrocad --pretty
   ```

4. Run network-free status projection:

   ```sh
   workspace-mcp-manager instance github-access status electrocad --pretty
   ```

Return only the sanitized JSON from steps 3 and 4, plus the credential class
exercised if known (for example fine-grained or classic PAT). Do **not** return
the credential, `hosts.yml`, helper stdin, terminal transcript, or raw
secret-bearing GitHub CLI output.

Successful operator validation requires managed/ready profile storage,
`gh 2.45.0`, authenticated account identity, passed manager-environment and
repository-read verification, passed deployed-MCP verification when the MCP is
running, and unchanged independent Git transport readiness.

Only after that evidence is reviewed may the final markers be recorded:

```text
PM3_1_1_OPERATOR_AUTH_VALIDATION=PASS
PM3_1_1_GITHUB_ACCESS_ONBOARDING=PASS
```
