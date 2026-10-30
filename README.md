# workspace-mcp-manager

Greenfield deterministic control plane for multiple `coding-tools-mcp` +
`tunnel-client` deployments.

The project deliberately separates:

- **desired state** — strict, versioned instance declarations;
- **observed state** — freshly inspected host/runtime facts;
- **applied/ownership state** — non-secret provenance and ownership evidence.

M0 implements the non-host-mutating foundation:

1. reconciled capability dispositions;
2. domain/configuration contracts;
3. read-only host inspection;
4. the persistent desired-state registry.

P4/P5 add deterministic resource rendering and reconciliation planning.

P6 adds the authoritative host-side apply lifecycle through transient user-systemd
workers. P6 is live-qualified on the disposable `manager-qual` instance, including
apply/NOOP apply/stop/start/restart/remove/recreate, ownership, rollback/recovery,
transaction evidence, MCP discovery, tunnel health/readiness, and deterministic
NVM-managed Node toolchain rendering.

P7 live-qualifies the manager-owned `manager-qual` deployment through the real
ChatGPT MCP/plugin surface: discovery, protocol initialization/session cleanup,
workspace and permission identity, workspace confinement, WSL GitHub/NVM
environment propagation, Node toolchain execution, Git status, and tunnel
health/readiness. P7 also regression-covers safe updates of an active owned MCP
unit without weakening endpoint-collision checks.

P8 adds deterministic RO/RX/RW/RWX external-folder access. Desired access mutations run
through the host boundary, generated user-systemd units mount sources under
`.workspace-mcp-access/<alias>`, access ownership is tracked and cleaned from
applied-state evidence, and both namespace preflight and live plugin enforcement
are qualified on `manager-qual`.

P9 adds non-mutating per-instance Git/GitHub diagnostics through the same host
boundary. The manager detects the repository and working-tree state, probes a
configured remote with `ls-remote`, diagnoses Git identity and GitHub CLI auth,
forces the real account HOME for credential resolution, and uses the same
configured execution PATH rendered into the MCP service.

P10 implements durable detached Codex jobs through transient user-systemd
workers. Jobs resolve Codex only from the instance's declared MCP execution
PATH, preserve read-only/workspace-write modes, persist request/status/result
and separate stdout/stderr logs, support bounded output and cancellation, and
survive the initiating MCP request. The configured WSL Codex installation and
the full detached read/write/cancel/restart lifecycle are live-qualified on
`manager-qual`.

P11 implements exact local MCP admission recovery. A manager-owned 30-second
timer probes MCP `initialize`, deletes every healthy probe session, and restarts
only the local MCP unit when HTTP 503 carries the exact JSON-RPC error message
`maximum HTTP session count reached`. Persisted recovery evidence enforces the
120-second cooldown across guard invocations. Controlled live saturation on
`manager-qual` qualified the exact-signature restart, dependent tunnel recovery,
cooldown suppression, and reversible guard enable/disable lifecycle.

P12 adds persisted non-mutating resilience snapshots. `instance diagnose`
captures separate MCP/tunnel service, process, cgroup and memory evidence;
host meminfo/PSI; current MCP discovery/initialize with session cleanup; current
tunnel health/readiness; and time-windowed structured tunnel-log evidence. It
keeps local MCP 5xx, tunnel control-plane 5xx/poll timeout, process-restart, and
memory-pressure classifications in explicit independent incident domains.
Controlled live qualification on `manager-qual` proved current healthy evidence,
retained real poll-timeout history, local MCP saturation classification without
service restart, snapshot redaction/persistence, and final NOOP convergence.

P13 adds controlled reboot checkpointing and reconciliation for native Linux.
`host reboot`
fsyncs a manager-owned checkpoint containing the current boot ID and all
present+running instance fingerprints before it performs even the authorization
preflight. The separately installed `workspace-mcp-reboot` bridge accepts only
`--check` or the exact preserved `sudo -n /usr/bin/systemctl reboot` action;
the manager never creates or broadens sudo policy. `host reboot-check` compares
the persisted boot ID with `/proc/sys/kernel/random/boot_id` and reconciles the
captured expected instances after a real boot change. WSL reboot execution is
explicitly deferred: both `host reboot` and `workspace-mcp-reboot` fail closed
on WSL before checkpoint creation or sudo/systemctl execution. A future optional
Windows-side listener may provide WSL restart without enabling WSL interop.
Native-Linux physical reboot remains a separate host acceptance gate.

P14 replaces the legacy cleanup toggle with an explicit ownership-gated cleanup
surface. `cleanup audit` is read-only and recognizes legacy
`workspace-mcp` configs, tunnel profiles, MCP/tunnel user units, per-instance
launchers, and bounded legacy state logs. `cleanup execute <id>` requires one
explicit target, revalidates every ownership marker immediately before mutation,
stops/disables only the exact legacy units, removes only proven per-instance
residue, and preserves shared binaries plus credential/runtime files. Live WSL
qualification used only the synthetic `p14-legacy-qual` instance and proved the
existing `leadbot`, `manager`, and `wsl-reconcile` legacy deployments remained
unchanged.

P15's developer-tool provisioning remains authoritative for explicit Codex,
GitHub CLI, and SSH/GPG-agent host-tool capabilities without managing
authentication, private keys, or session credentials. Manager installation and
upgrade have moved to the standalone distribution authority described below;
that distribution workstream supersedes only P15's historical shared-toolkit
installer model.

P16 adds deterministic manager-owned per-instance launchers at
`~/.local/bin/<instance>-mcp`. Each launcher is a narrow frontend over the
authoritative manager registry/CLI, exposes only existing instance/access/Codex
operations, rejects generic host/private passthrough, and is reconciled through
the normal instance lifecycle. P16 is live-qualified on `manager-qual` and
completes the frozen MVP capability set.

PM1 begins post-MVP development-environment management. Declaration schema v2
adds explicit GitHub-profile, repository-local Git identity/transport, and
agent-provider policy while preserving v1 declarations and fingerprints. The
manager can own a retained per-instance `GH_CONFIG_DIR`, reversible local Git
identity/transport keys, an HTTPS GitHub-CLI helper, or a per-instance
`ssh-agent` service/socket. Authentication tokens, private keys, passphrases,
known-host data, Codex sessions, and GPG key/trust stores remain external. PM1
is implemented, pure-verified, and reversibly live-qualified on disposable
`manager-qual` across managed GitHub profile, Git identity, SSH agent, HTTPS
helper, cleanup/restoration, final all-NOOP convergence, and endpoint health.

PM2 established `workspace-mcp-manager-tui` and its bounded snapshot/smoke
contracts over the public manager CLI. PM3 replatforms the interactive frontend
to the isolated Textual runtime while preserving those external contracts and
keeping registry, systemd, Git/Access interpretation, reconciliation, and
mutation authority in `workspace-mcp-manager`.

PM3.1 makes instance setup context-aware and manager-composed. Launch context or
a selected directory is resolved through `instance discover`; effective setup
state is constructed through `instance candidate` with field provenance and
structured operator edits; `host ports` projects declared/listening endpoints
and recommendation/collision evidence. The Textual wizard supplies intent only,
uses an existing-instance dropdown and filesystem-only directory picker, shows
known ports and local Git/authentication evidence, and hides the manager-derived
tunnel profile from normal editing. Authentication credentials remain external
and secret values do not enter public setup contracts or TUI state. PM3.1 is
pure/Pilot/live WSL-qualified with PM3 and PM2 regression gates and final
`manager-qual` all-NOOP convergence.

PM3.1.1 narrowly supersedes PM3.1's external-authentication rule for
`github.mode=managed`. The manager now owns orchestration of GitHub CLI
credential onboarding into the canonical per-instance `GH_CONFIG_DIR`, while
GitHub CLI remains authoritative for credential representation and persistence.
Managed execution strips ambient GitHub token/config overrides, verification is
bounded and freshness/context-aware, and GitHub CLI/API readiness remains
independent from repository Git transport. Credential entry occurs only in a
foreground controlling-terminal helper with intentionally visible terminal
echo; credential bytes do not enter manager/TUI contracts, argv, token-bearing
environment variables, logs, diagnostics, or verification records. Automated
implementation qualification remains separate from operator real-credential
validation.

## Standalone installation and upgrade

Use `bash bootstrap.sh` for the remote release, or
`bash scripts/setup-local.sh` to install a clean local checkout.
`scripts/install.sh` is the transaction backend and requires acquisition
provenance supplied by these entry points. Local checkout installation does not
fetch manager source; bundled dependencies may still require downloads.


The production distribution channel is fixed to the canonical repository and
`release` branch. The qualified public installation form downloads the bootstrap
script completely before executing it. In a terminal, it opens a numbered
setup menu:

```sh
tmp="$(mktemp)"
curl -fsSL -o "$tmp" \
  https://raw.githubusercontent.com/noobAIcoder/workspace-mcp-manager/release/bootstrap.sh
bash "$tmp"
status=$?
rm -f "$tmp"
exit "$status"
```

After installation, open the same menu with:

```sh
workspace-mcp-manager-setup
```

Setup inspects local status without contacting the release server, explains what
it found, and recommends a next step. Its main choices are:

1. **Install manager**, **Upgrade manager installation**, or **Check for manager
   updates**, depending on the detected installation. This opens a task page;
   checking the release server is a separate choice, followed by review and
   confirmation before any change. This updates the manager only; existing
   instance runtime paths and services are preserved.
2. **Open instance manager** to manage workspaces and instance settings.
3. **Maintenance** for interrupted-operation recovery, restoring a previous
   manager version, or uninstalling.
4. **Installation details** for the selected account and installation path, full
   release IDs, runtime versions, prerequisites, and diagnostics.
5. **Import legacy toolkit instances**, shown only when an old toolkit is
   detected. Importing transfers instances into manager control; upgrading an
   older manager installation preserves its existing instance settings and
   running services and does not import toolkit instances.
6. **Upgrade manager and all instances** to update the manager and then refresh
   every configured instance against its bundled MCP server and tunnel client.
   The instance stage has its own preview and requires typing `UPGRADE`.

Use **0** or blank input to go back or cancel; at the main menu, either exits.
EOF also exits safely. Unavailable actions explain the prerequisite. Recovery
takes priority when work was interrupted. After a successful installation or
upgrade, setup offers to open the instance manager, return to setup, or exit.
Manager-only updates and restoring a previous manager version preserve existing instance
declarations, runtime paths, and services. Restoring a version keeps a
maintenance-capable release available through the setup command.

The full upgrade always refreshes deployments, including when the manager release
is already current. Running instances restart one at a time and briefly lose
connections; configured stopped instances remain stopped and absent instances
receive updated runtime references without deployment. The preview identifies
all runtime-path replacements, including custom binary paths. Other settings,
credentials, identities, ports and workspaces are retained. Separately installed
Codex CLI, GitHub CLI and OS packages are excluded.

Original declarations are backed up privately under
`~/.local/state/workspace-mcp-manager/backups/instance-upgrade-<id>/` before the
instance stage changes anything. A failure stops subsequent upgrades. Use
**Maintenance → Recover interrupted operation** to validate and resume the pinned
target; completed instances are checked and skipped rather than restarted again.
Previous releases and backups stay available. Cancelling the instance review
leaves instance runtimes unchanged even if the manager stage already completed.

The public manager also exposes `instance upgrade preview --all`,
`instance upgrade apply --preview PATH --expected-preview-fingerprint VALUE`, and
`instance upgrade recover`. Run these from the installed target distribution.
Preview is read-only; apply requires the complete reviewed JSON and its matching
fingerprint. Progress is written to stderr and the final result remains JSON.

Uninstall requires typing `UNINSTALL`. It first removes instance deployments
through the normal lifecycle reconciler. If any removal fails, the manager
remains installed; completed removals stay removed and recovery enables retry.
Original declarations, removed profiles/logs, and any retained historical toolkit
are saved under `~/.local/state/workspace-mcp-manager/backups/uninstall-<id>/`
with a private backup directory. Credentials, workspaces, and other configuration
remain in place. Declaration records remain absent/stopped, so reinstall does
not automatically redeploy instances. Unknown program files or remaining
service references block deletion.

Recovery, rollback, and uninstall work offline. Before an uninstall, the preview
shows a recovery helper command that survives removal of the program files:

```sh
bash ~/.local/state/workspace-mcp-manager/setup-recovery/setup.sh --interactive
```

Keep any custom `--account-home` and `--prefix` arguments when using that helper.
Rollback validates actual retained runtimes and declaration compatibility; it
never downloads a replacement for an invalid local release.

For automation, download the bootstrap as above and invoke it explicitly with
`bash "$tmp" --non-interactive`. That mode installs, updates, or migrates without
a prompt; `--non-interactive --check` only checks the acquired release. Running
without a terminal and without an explicit mode exits without installing.
Existing `workspace-mcp-manager-upgrade` automation remains noninteractive.
Use `--interactive` to select the menu explicitly; `--configure` can open the
manager after a successful installation/update.

Supported installation profiles are **WSL2 Ubuntu 24.04 x86_64** and **native Ubuntu 22.04 ARM64 (aarch64)**. Live migration qualification remains a separate gate. Bootstrap
prerequisites are Bash, Git, curl, Python >= 3.11, `sha256sum`, `realpath`,
`mktemp`, `flock`, GNU coreutils, `tar`, `unzip`, and an available user-systemd
manager. The installer does not invoke `sudo`, require system `pip`, or require
system `uv`.

Installation creates one complete immutable distribution per exact release Git
commit:

```text
~/.local/lib/workspace-mcp-manager/
├── releases/dist-<release-commit>/
│   ├── bin/
│   ├── manager/
│   ├── runtimes/python/
│   ├── runtimes/coding-tools-mcp/
│   ├── runtimes/tunnel-client/
│   ├── tools/uv/
│   ├── distribution/
│   └── install.json
└── current -> releases/dist-<release-commit>
```

The stable user commands are:

```text
workspace-mcp-manager
workspace-mcp-manager-tui
workspace-mcp-reboot
workspace-mcp-manager-upgrade
```

Each stable command resolves through `current`. Bundled Python, uv,
`coding-tools-mcp`, and `tunnel-client` remain private to the immutable release;
the distribution does not replace equivalent host-level commands. Installed
manager/TUI execution uses the pinned distribution Python rather than mutable
host Python.

`scripts/install.sh` is the sole distribution mutation authority. It stages and
verifies the complete candidate, validates all existing declarations with the
candidate manager, finalizes the immutable release, prepares the receipt, and
then atomically replaces `current`. That `current` replacement is the transaction
commit point. `~/.local/state/workspace-mcp-manager/distribution.json` is evidence
of the committed release, not the selector.

The immutable integrity chain is:

```text
distribution.json
→ SHA-256 of install.json
→ deterministic manifest of every other immutable release file/symlink
```

An unfinished mutation is described by
`~/.local/state/workspace-mcp-manager/distribution-transaction.json`. Before the
commit point, failure/recovery converges to the previous distribution. After the
commit point, recovery converges forward to the committed candidate and repairs
receipt evidence if necessary. Recovery and rollback of already-started local
transactions require no network acquisition.

Local inspection/recovery commands are available through the immutable release:

```sh
~/.local/lib/workspace-mcp-manager/current/distribution/install.sh --check
~/.local/lib/workspace-mcp-manager/current/distribution/install.sh --recover-only
workspace-mcp-manager-upgrade --check
workspace-mcp-manager-upgrade
```

`install.sh --check` is local and read-only. `workspace-mcp-manager-upgrade
--check` performs the separate remote release-availability query. Normal upgrade
first runs the old immutable installer's local recovery, then acquires the fixed
production channel and delegates the new transaction to the newly acquired
installer. `--configure` launches the committed installed TUI after the
distribution transaction; TUI cancellation/failure does not roll back the
committed release.

A recognized historical P15 installation can be migrated transactionally.
Foreign, malformed, partial, or mixed ownership fails closed. Distribution
installation/upgrade does not rewrite declarations, generated systemd units,
tunnel profiles, GitHub metadata, credentials, or service state. Existing
instances keep their release-specific runtime paths. New or explicitly
reconfigured candidates use bundled runtime paths from the immutable manager
release that is actually executing, even if `current` moves concurrently.

No automatic release pruning is performed. At minimum this preserves the
immediately previous release and every release still referenced by a declaration.

The bundled runtime lock selects coding-tools-mcp **0.5.0** with PyJWT **2.10.1**
and the full Linux amd64 tunnel-client **0.0.15** artifact. The tunnel archive
includes its NOTICE, dependency licenses, and SPDX SBOM. Managed GitHub
verification supports coding-tools-mcp 0.2.2, 0.3.0, and 0.5.0, including
sessionless verification with explicit command timeouts. Python, uv, Textual,
and the manager package version are unchanged. No experimental runtime flags
are enabled by this update.

Repository validation and the remaining live WSL qualification gate are recorded
in [the runtime update report](reports/runtime-update-0.5.0-status.md). Earlier
qualification reports remain historical evidence for their original runtimes.

Version/release inspection:

```sh
workspace-mcp-manager --version
readlink -f ~/.local/lib/workspace-mcp-manager/current
cat ~/.local/state/workspace-mcp-manager/distribution.json
```

Development occurs on `main`. A production promotion fast-forwards `release` to
the exact qualified `main` SHA; no release-only commit is permitted and published
version tags are immutable.

## Install or update a local checkout

Commit your changes, then run:

```sh
bash scripts/setup-local.sh
```

The numbered menu offers manager-only installation/update and **Update all
bundled components and instances** (option 6). Both install the checkout's pinned
Python, uv, MCP server, tunnel client, and UI dependencies. Option 6 additionally
reviews and refreshes all instance deployments, even if the manager is already
current; running instances restart. Codex CLI, GitHub CLI, Node, and OS packages
are excluded. No newer upstream versions are selected automatically.

The launcher detects SSH/HTTPS origins and the current commit automatically.
Uncommitted and untracked files must be committed first. It also supports
`--account-home PATH` and `--prefix PATH`. The existing installed setup command
continues to use the published release channel.

## Run from source

```sh
PYTHONPATH=src python3 -m workspace_mcp_manager host inspect --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager host doctor --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager instance validate examples/manager.json --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager --registry-dir examples/registry instance render manager-qual --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager --registry-dir examples/registry instance plan manager-qual --pretty
PYTHONPATH=src python3 -m workspace_mcp_manager.tui --snapshot
```

Run the pure test suite without third-party dependencies:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Registry

By default declarations are stored under the real Unix account home, not the
possibly sandboxed `$HOME` inherited from an MCP request:

```text
~/.config/workspace-mcp-manager/instances/<instance>.json
```

The CLI supports `--registry-dir` for explicit testing/bootstrap isolation.

## Current command surface

```text
workspace-mcp-manager host inspect
workspace-mcp-manager host doctor
workspace-mcp-manager host components
workspace-mcp-manager host ports
workspace-mcp-manager host reboot --reason <text>
workspace-mcp-manager host reboot-check

workspace-mcp-reboot --check
workspace-mcp-reboot

workspace-mcp-manager cleanup audit [<legacy-id>]
workspace-mcp-manager cleanup execute <legacy-id>

workspace-mcp-manager instance validate <file>
workspace-mcp-manager instance create <file>
workspace-mcp-manager instance update <file>
workspace-mcp-manager instance list
workspace-mcp-manager instance show <id>
workspace-mcp-manager instance template
workspace-mcp-manager instance discover <path>
workspace-mcp-manager instance candidate        # request JSON on stdin
workspace-mcp-manager instance preview <file>
workspace-mcp-manager instance summary <id>
workspace-mcp-manager instance summaries
workspace-mcp-manager instance render <id>
workspace-mcp-manager instance plan <id>
workspace-mcp-manager instance status <id>
workspace-mcp-manager instance logs <id>
workspace-mcp-manager instance git <id>
workspace-mcp-manager instance diagnose <id> [--since-seconds N]
workspace-mcp-manager instance github-access status <id>
workspace-mcp-manager instance github-access verify <id>
workspace-mcp-manager instance github-access configure <id>
workspace-mcp-manager instance client-compatibility <id>
workspace-mcp-manager instance session-continuity <id>  # legacy alias
workspace-mcp-manager instance apply <id>
workspace-mcp-manager instance start <id>
workspace-mcp-manager instance stop <id>
workspace-mcp-manager instance restart <id>
workspace-mcp-manager instance remove <id>

workspace-mcp-manager access list <id>
workspace-mcp-manager access add-ro <id> <alias> <path>
workspace-mcp-manager access add-rw <id> <alias> <path>
workspace-mcp-manager access add-rx <id> <alias> <path>
workspace-mcp-manager access add-rwx <id> <alias> <path>
workspace-mcp-manager access remove <id> <alias>
workspace-mcp-manager access update <id> <existing-alias> ro|rx|rw|rwx <alias> <path>

workspace-mcp-manager codex start <id> --mode read|write (--prompt <text> | --prompt-stdin)
workspace-mcp-manager codex status <id> <job-id>
workspace-mcp-manager codex output <id> <job-id> [--limit-bytes N]
workspace-mcp-manager codex cancel <id> <job-id>

workspace-mcp-manager-tui
workspace-mcp-manager-tui --snapshot [--instance <id>]
```

Access modes are read-only (RO), read-execute (RX), read-write (RW), and
read-write-execute (RWX). RO/RW aliases block direct execution using systemd
`NoExecPaths=`; RX/RWX retain execution subject to host file permissions and
filesystem restrictions. Existing RO/RW grants become non-executable on the
next apply. Use `access update` to select RX/RWX when execution is needed.
Readable scripts can still be passed to an interpreter. These restrictions
apply through the managed alias; source permissions are never changed.

For managed GitHub access, the CLI foreground prompt and Textual GitHub-access
screen show the recommended fine-grained PAT profile for coding-tools repository
work: **Only select repositories**, with **Contents RW**, **Issues RW**,
**Metadata RO (required)**, and **Pull requests RW**. Repository identity is
shown when the manager can derive it. The token itself is accepted only by the
foreground terminal helper and never by a TUI field or CLI argument.

`plan` remains explicitly non-mutating. Lifecycle mutations execute through the
manager's narrow host-worker boundary and never require manual systemd/profile
editing.

P7 through P12 are complete and live-qualified. P13 implementation and pure
verification are complete. Its WSL platform gate is qualified to fail closed as
unsupported; WSL restart is deferred. Native-Linux physical reboot acceptance
has not yet been run for the greenfield manager. P14 implementation, pure
verification, and synthetic WSL legacy-cleanup qualification are complete.
P15 and P16 are implemented and WSL-qualified, completing the frozen MVP. PM1
is also implemented, pure-verified, and reversibly WSL-qualified on
`manager-qual`. PM2 external terminal contracts remain qualified; PM3 is the
qualified Textual operator frontend; PM3.1 is qualified for context-aware,
manager-authoritative instance setup with PM3/PM2 regression preservation.
PM3.1.1 implements per-instance managed GitHub access onboarding and remains
`AWAITING_OPERATOR_VALIDATION` until one real manager-owned profile is configured
and its sanitized manager/MCP/repository verification results are reviewed.
PM3.1.2 adds repository-aware Node/pnpm discovery and deterministic NVM
selection so context-aware setup can project the compatible Node version into
`mcp.exec_path` and the complete version root into `mcp.external_roots` rather
than hard-coding a Node-less PATH. Revised P7.1 qualifies stateless-client
compatibility instead of requiring protocol-session persistence. It supports
both the legacy coding-tools 0.2.x `session_id` / `kill_session` model and the
coding-tools 0.3.x sessionless `command_id` / `kill_command` model. Ordinary
atomic coding operations remain ready for either era; explicit process/output
handles are classified separately. A qualified 0.3.x runtime can expose
cross-request command handles without requiring `Mcp-Session-Id` affinity.

For automation, successful/valid public results return exit 0, structured public
results with `ok=false` return exit 1, and public `ManagerError` failures return
exit 2. Internal JSON host-worker commands use transport-success semantics so
structured semantic failures are preserved across the host boundary.

### Import and replace an existing workspace-mcp toolkit

Run the updated setup frontend and choose **7. Import and replace legacy toolkit**.
The wizard guides installation/update of the manager if necessary, lists legacy
instances, and lets you select IDs or `all`. Review settings and the upgrade to
coding-tools-mcp **0.5.0** and tunnel-client **0.0.15**, then type `MIGRATE` to
confirm the service interruption. Empty input cancels. The installed manager
release must include import support; changing a local bootstrap alone does not
publish that release to the production channel.

Instances migrate sequentially. A failure stops the batch; completed migrations
stay committed, the failing instance is restored when possible, and unattempted
instances keep running. Choose **Recover** after an interruption. Unresolved
recovery blocks changes to the affected instance and distribution. After all
legacy instances have transferred, verified program files are archived and old
commands retired. Unknown files or remaining references retain the toolkit and
report the reason.

Credential files and GitHub profiles remain in place. The importer never displays
or copies their contents. The recognized predecessor is the inspected workspace-mcp **0.3.2** program; modified builds require compatibility review. Configurations with inline tunnel credentials, custom
unit directives, or config/unit drift are blocked for review. Known mixed service
intent produces a draft; an optional JSON map of selected IDs to reviewed v2
declarations can resolve lifecycle/recovery intent. Other settings cannot be
silently overridden. Previously disabled boot enablement remains disabled on
subsequent manager applies without changing the declaration schema.

For JSON automation, separate preview from apply:

```bash
workspace-mcp-manager instance import list --pretty
workspace-mcp-manager instance import preview --all > import-preview.json
# Inspect import-preview.json and obtain its fingerprint before applying.
workspace-mcp-manager instance import apply --preview import-preview.json \
  --expected-preview-fingerprint <reviewed-fingerprint>
```

`preview one two` selects specific IDs. `preview --all` also offers retirement
when no legacy instances remain. Preview is read-only. Optional
`--candidate reviewed-declarations.json` resolves supported intent ambiguities.
Recovery data and protected archives live under
`~/.local/state/workspace-mcp-manager/imports/`. State containing a referenced
credential file is retained at its original location so that reference stays
valid. Source changes and fixture tests do not establish live migration
qualification; see `reports/legacy-import-work.md` for observed evidence.
