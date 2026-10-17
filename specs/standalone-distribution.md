# Standalone GitHub distribution

Status: **NORMATIVE DISTRIBUTION SPECIFICATION**

This specification owns installation, local upgrade, immutable runtime selection,
and GitHub release-channel promotion for `workspace-mcp-manager`. It supersedes
only the shared-toolkit installer portion of P15. P15 remains authoritative for
developer-tool audit/provisioning unrelated to distribution installation.

## Product boundary

The atomic installed product is:

```text
workspace-mcp-manager
workspace-mcp-manager-tui
workspace-mcp-reboot
workspace-mcp-manager-upgrade
workspace-mcp-manager-setup
distribution-owned CPython
distribution-owned uv
distribution-owned coding-tools-mcp
distribution-owned tunnel-client
```

Production acquisition is fixed to:

```text
repository = https://github.com/noobAIcoder/workspace-mcp-manager.git
ref        = refs/heads/release
```

Qualification/development code MAY explicitly override the ref/repository. A
receipt, environment variable, Git configuration, or arbitrary local config
MUST NOT redirect the production channel.

## Distribution layout

```text
~/.local/lib/workspace-mcp-manager/
├── releases/
│   └── dist-<release-commit>/
│       ├── bin/
│       ├── manager/
│       ├── runtimes/python/
│       ├── runtimes/coding-tools-mcp/
│       ├── runtimes/tunnel-client/
│       ├── tools/uv/
│       ├── distribution/
│       └── install.json
└── current -> releases/dist-<release-commit>
```

Stable manager commands MUST target `current/bin/<name>` and MUST NOT identify an
individual release. The setup maintenance command uses the retained-release
anchor defined in the interactive setup contract below. Bundled `coding-tools-mcp`, `tunnel-client`, and `uv` remain
private to the immutable distribution; distribution installation MUST NOT adopt
or replace equivalent host-level tooling.

## Normative invariants

### DIST-01 — Sole mutation authority

`scripts/install.sh` is the only distribution transaction authority.
`scripts/install_toolkit.sh`, if retained, MUST do nothing except `exec`
`scripts/install.sh` with the original arguments.

### DIST-02 — Complete immutable release

Every candidate MUST contain all manager files, runtime/tooling inputs, wrappers,
frontends, manifests, and locked dependencies before activation. Installed
manager/TUI execution MUST use the pinned distribution Python and MUST NOT depend
on mutable host Python. A finalized release MUST NOT be modified in place.

### DIST-03 — Atomic commit authority

For installation, update, migration, and deliberate rollback, the sole transaction
commit point is atomic replacement of `current`. Uninstall uses the quarantine
commit defined in the interactive setup contract below. Before the
switch, the previous distribution remains authoritative. After a successful
switch the candidate is authoritative and recovery MUST converge forward.

### DIST-04 — Receipt is evidence

`~/.local/state/workspace-mcp-manager/distribution.json` describes the committed,
verified distribution. It does not select authority. Missing/stale receipt data
after commit is recoverable evidence drift.

### DIST-05 — Deterministic recovery

Every mutating operation MUST recover an unfinished distribution transaction
before beginning a new one. Recovery MUST inspect actual filesystem/current state
and converge to exactly the complete previous distribution or complete committed
candidate; deleting a journal without convergence is forbidden.

### DIST-06 — Fail-closed ownership

Ownership classification is exactly:

```text
absent
recognized_existing
standalone_distribution
foreign_or_ambiguous
```

Foreign, malformed, mixed, partial, or ambiguous resources MUST NOT be adopted,
overwritten, repaired, or re-owned automatically. Only explicitly recognized
predecessor state MAY be migrated.

### DIST-07 — Installation does not manage instances

Distribution operations MUST NOT modify declarations/generated resources, apply
or reconcile instances, change service state, change Git/GitHub configuration,
or change credentials/tunnel profiles. Declaration compatibility checks are
read-only.

### DIST-08 — Existing runtime paths remain stable

Install/upgrade MUST NOT rewrite existing instance runtime paths or declarations.

### DIST-09 — Executing-release runtime authority

New or explicitly reconfigured candidates derive bundled runtime defaults from
the immutable distribution containing the executing manager process. They MUST
NOT re-read `current` to choose runtime defaults after process launch. Persisted
paths are canonical absolute release-specific paths and normally MUST NOT contain
`/current/` or `~`.

Source execution outside a valid installed distribution MUST use an explicit,
deterministic fallback and MUST NOT pretend that the source checkout is `current`.

### DIST-10 — Exact acquisition provenance

Production acquisition MUST establish and cross-check canonical repository,
requested canonical ref, exact fetched commit, and detached checkout HEAD.

### DIST-11 — Fixed production channel

Production bootstrap/upgrade uses the fixed repository/ref above. Only explicit
qualification/development flags may override them.

### DIST-12 — Credential isolation

Distribution operations MUST NOT authenticate GitHub/Codex or create, inspect,
copy, import, or persist credentials. Secrets MUST NOT enter release manifests,
receipts, journals, logs, diagnostics, or qualification evidence.

### DIST-13 — Read-only check

`install.sh --check` performs no persistent managed mutation and no remote update
lookup. Remote availability belongs to `workspace-mcp-manager-upgrade --check`.

### DIST-14 — Serialized mutation

Exactly one account-level distribution installation, upgrade, migration, rollback,
uninstall, or recovery operation may mutate state at a time.

### DIST-15 — Offline rollback/recovery

Recovery of an existing transaction and rollback of uncommitted local state MUST
require no network acquisition.

### DIST-16 — Retention

The immediately previous distribution and every distribution referenced by an
existing declaration MUST be retained. Automatic release garbage collection is
out of scope.

### DIST-17 — Exact promotion

`release` MUST point to the exact qualified `main` SHA. It contains no release-only
commit, advances by fast-forward only, and published tags never move.

## Transaction contract

Durable journal:

```text
~/.local/state/workspace-mcp-manager/distribution-transaction.json
```

The journal MUST exist before the first persistent manager-owned staging
mutation. It contains only non-secret recovery data for previous authority and
candidate provenance/paths. Candidate construction, immutable finalization, and
prepared receipt occur before `current` replacement.

After commit, completion is:

```text
validate current
→ validate stable entrypoints
→ atomically publish prepared receipt
→ fsync receipt parent
→ durably resolve journal
```

Pre-commit failure recovers the previous authority. Post-commit failure recovers
the candidate. `--recover-only` performs local recovery only and starts no new
network/install transaction.

## Input locking

`install-versions.lock` is strict JSON data. Its parser MUST reject duplicate
keys, unknown/missing fields, unsupported schema versions, malformed identities,
wrong platform/architecture, and digest mismatch. Lock content is never executed.
`validator_id` only selects installer-owned validation logic.

Every external component records exact version/source identity/acquisition
location/SHA-256/platform/architecture and expected observable identity.

## Complete-content manifest anchor

`install.json` MUST contain a deterministic content manifest for every other
distribution-owned immutable regular file and symlink, including SHA-256 and
required mode for regular files and exact target/mode for symlinks. It MUST NOT
attempt to embed the SHA-256 of its own final bytes because that would create an
unsatisfiable cryptographic self-reference.

The pre-commit prepared receipt MUST instead contain the exact SHA-256 of the
final `install.json`. Complete immutable release integrity is therefore anchored
as:

```text
distribution.json
  -> SHA-256 of install.json
  -> deterministic manifest of every other immutable release file/symlink
```

This split does not make the receipt commit authority; `current` remains the sole
authoritative selector under DIST-03/DIST-04.

## Acquisition frontends

`bootstrap.sh` downloads no credentials and performs an isolated Git fetch,
resolves one exact commit, checks it out detached, verifies HEAD, and invokes the
new checkout's `scripts/install.sh` with explicit provenance.

The installed `workspace-mcp-manager-upgrade` first invokes its own immutable
`install.sh --recover-only`, then acquires the fixed production channel and invokes
the newly acquired checkout's `scripts/install.sh --upgrade`. The old installer
does not implement new-candidate upgrade semantics.

Ambient Git configuration capable of redirecting acquisition, including global
or system config and `url.*.insteadOf`, MUST be neutralized for production fetch.

## Supported host profiles and qualification

The original live qualification target (historical evidence remains scoped to it):

```text
WSL2
Ubuntu 24.04
x86_64
systemd --user available
```

No installer invokes `sudo`. Host Python >= 3.11 MAY be a bounded bootstrap
prerequisite before candidate Python exists; installed product execution uses the
pinned distribution Python. System `pip` and system `uv` are not prerequisites.

## Qualification authority

Qualification MUST cover strict lock parsing, complete immutable manifests,
ownership classification, clean install, predecessor migration, NOOP reinstall,
A→B upgrade, pre/post-commit failure and hard-interruption recovery, digest and
collision failures, declaration compatibility, serialization, read-only check,
receipt/provenance, instance preservation, immutable runtime selection, exact
Git acquisition, exact-main qualification, exact-SHA release promotion, public
release bootstrap, installed local upgrade, and full manager regression.

No PASS may be claimed for an unexecuted check or transport timeout.

## Interactive setup and deliberate maintenance (2026-10-17)

The accepted setup-menu plan adds a Bash numbered menu, available from bootstrap
and the installed `workspace-mcp-manager-setup` command. Interactive terminals
open it by default. Automation explicitly selects `--non-interactive`;
existing upgrade CLI behavior remains unchanged. Existing target-home/prefix
options remain available without an interactive settings editor.

Menu opening MUST inspect local state without network acquisition or persistent
mutation. Actions are install/update/recognized migration, remote availability,
local recovery, retained-release rollback, uninstall, open manager, and exit.
Unavailable actions MUST explain why. All changes require inspect, preview,
confirmation (default Cancel), execution, and a refreshed result. EOF cancels;
progress reports phases rather than speculative percentages.

Distribution maintenance remains delegated to scripts/install.sh. Additive
structured previews identify affected releases, instances, preserved data,
blockers, and a fingerprint rechecked under the distribution lock before apply.
Foreign ownership, malformed manifests/journals, and changed preview state block
mutation. Recovery takes precedence over another maintenance transaction.

Rollback MUST validate a selected retained standalone release, its runtime
identities and declaration compatibility before activation. It switches manager
entry points/current and repairs receipt evidence transactionally, preserving
all instance declarations and service/runtime paths. Recovery must converge to
the original or selected complete release. A maintenance-capable release MUST
remain accessible through the setup entry point even if current selects a release
that predates setup. This setup entry point alone may reference a retained
immutable release rather than current; it is validated as owned content.

Uninstall MUST show affected deployments and request the literal UNINSTALL.
Confirmed removal uses the existing host lifecycle reconciler, retaining
original declarations and removed persistent profiles/logs in a mode-0700
backup. Credential directories and workspaces MUST remain untouched. Records
remain absent/stopped after deployment removal; reinstall MUST NOT redeploy
them automatically. Failures halt deletion of program files and identify
completed removals and remaining work. Removing multiple deployments is not
one atomic transaction. Uninstall MUST refuse unknown or changed files and
remaining services referencing the distribution.

After successful deployment removal and ownership revalidation, uninstall
atomically moves the owned distribution root to a journaled sibling quarantine;
this rename is the uninstall commit point, specializing DIST-03. Recovery then
converges forward by removing only exact owned entry points, the install receipt,
and validated quarantine contents. Protected backups, user configuration,
credentials, workspaces, and logs survive. Recovery and uninstall require no
network access. Old installation journals remain supported; maintenance
journals are distinct and validated before use. No automatic release pruning
is introduced outside explicitly confirmed uninstall.

Confirmed uninstall specializes DIST-16: after all deployments are absent,
program releases may be removed despite references in preserved absent declaration
records. Those records require explicit runtime reconfiguration before future
redeployment. Historical schema-2 toolkit releases are preserved whole in the
private backup; they are not selectable standalone rollback targets.

Successful installation/update refreshes the setup anchor to the newly committed
maintenance-capable release. Deliberate rollback retains that anchor so older
manager releases remain manageable. Installations also stage a private offline
recovery capsule before starting their first transaction, so recovery does not
depend on a completed release or a surviving temporary acquisition directory.

### Setup inspection compatibility

Read-only setup inspection uses the standalone standard-library backend and
supports Python 3.10. Installation and package operations require Python 3.11
or newer; the installer tries `python3`, `python3.12`, then `python3.11` without
changing the account's PATH or default Python. Inspection failures must display
the underlying diagnostic. A detected legacy `workspace-mcp` toolkit is reported
separately from the manager installation and does not establish manager ownership
or authorize changes to existing services.

## Legacy instance import and toolkit replacement

The setup menu MUST offer Import and replace legacy toolkit. It MUST discover
legacy instances before manager installation, guide confirmed installation when
needed without stopping legacy services, then obtain separate confirmation of a
fingerprinted migration preview and connection interruption. Multiple selections
MUST run sequentially, stopping on first failure. Unattempted instances MUST
remain unchanged. CLI JSON import list/preview/apply MUST expose the same flow.

Recognized version-1 legacy configs, unit templates and known drop-ins MUST map
to validated version-2 declarations. IDs, workspaces, ports, permissions, PATH,
external roots, access mappings, GitHub and credential references, tunnel identity
and effective service intent MUST be preserved. Runtime binaries MUST upgrade to
the executing immutable distribution's bundled 0.5.0 MCP and 0.0.15 tunnel.
Unknown or ambiguous settings/ownership and ID collisions MUST block handover;
unresolved values MUST be reviewed explicitly. Credentials MUST remain in place
and MUST NOT enter previews, logs, or backups. Drafts remain outside the registry.

Each handover MUST recheck reviewed inputs under distribution/instance locks,
record durable recovery data before stopping the legacy guard/tunnel/MCP, and use
the existing lifecycle reconciler. Service and endpoint verification precedes the
commit marker. Precommit recovery MUST restore original resources and unit
states; committed recovery MUST converge forward. Recovery conflicts retain the
journal and block competing selected-instance/distribution mutations. Setup
Recover MUST cover import and retirement. Receipts permit identical retries.

Only after every discovered legacy instance has committed may verified shared
program files and legacy entrypoints be archived/retired. Unrecognized contents
or remaining service references block retirement. Credentials, backups, and
unselected instances MUST remain intact. Failed installation MUST restore any
verified legacy command collision it displaced.

Native Ubuntu 22.04 aarch64 and WSL Ubuntu 24.04 x86_64 are supported installation
profiles. Each uses a strict architecture-specific artifact lock staged as
install-versions.lock; artifact versions and declaration schemas remain shared.
Archive validation MUST reject missing, duplicate, unexpected and unsafe members.

Import receipts and pending journals MAY carry per-unit boot enablement metadata
used by generation; this preserves existing disabled services on later applies
without extending declarations. Referenced credentials inside a predecessor state
directory take precedence over moving that directory: retain it in place and
record that preservation. Toolkit retirement MUST quiesce registry/lifecycle
writes for the final reference check and archive commit. Credential references
into the program directory block retirement.
