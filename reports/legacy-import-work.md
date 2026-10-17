# Legacy import implementation

Authority: user-approved Import legacy instances and replace the toolkit plan,
2026-10-17, and instruction to implement. Route: SPEC_IMPLEMENT_UPDATE;
methodologies SDD/TDD; flow SDD_TDD. No new package dependencies. Missing
.agentic-work/REPO_INFO.yaml is confined to repository fact discovery; source
is inspected directly. No live user installation or service mutation authorized.

## TODO_SDD

Target specs/standalone-distribution.md: add native Ubuntu22.04 aarch64,
architecture-selected immutable locks, import preview/confirmation, upgraded
bundled runtimes, selected-instance recovery, sequential batches and guarded
shared-toolkit retirement. Existing declaration schema and strict ownership
remain authoritative. User instruction accepts this scoped specification delta.
Validate by comparing menu, CLI, recovery and qualification behavior with plan.

## TODO_TDD

Tests-first: architecture selection, strict tunnel layouts, strict legacy parsing,
settings mapping, fingerprint freshness, unknown ownership/settings, no secrets,
service states, batch stop-on-failure, import rollback/commit recovery and
retirement. Preserve current distribution, maintenance and lifecycle regressions.
Use disposable fixtures and injected host adapters. Live artifact identity checks
are observational; full live handover requires disposable systemd instances and
must be reported separately if unavailable. Required completion: applicable unit
suite, Python/Bash syntax, diff check, and explicit qualification limits.

## Implemented behavior

The default numbered setup menu offers option 7, Import and replace legacy
toolkit. Installation/update and MIGRATE confirmation are separate. The manager
and selected account/prefix must agree. JSON CLI import list/preview/apply shares
the host boundary and fingerprint checks. Empty --all previews can retire an
unused toolkit after manager installation. Full reviewed v2 declaration maps can
resolve lifecycle/recovery ambiguity; unknown launch semantics stay blocked.

The importer recognizes the inspected workspace-mcp 0.3.2 program identity
326f79235cd11ffec99c0356ee27c686c18464412ef613106afbdcbfd259d77b.
It compares semantic unit/profile templates, audits known drop-ins, retains
credential references and permission settings, and selects the executing release's
0.5.0/0.0.15 runtime paths. Copied predecessor templates are data renderers;
legacy programs are never imported or executed. Modified builds, inline or
unsupported credential syntax, and config/unit drift require review.

Per-instance journals precede stopping the guard/tunnel/MCP. Original resources
are moved into protected backups, including state only when credentials do not
reference it. The normal lifecycle reconciler performs installation and endpoint
checks. Receipt publication is the commit point. Recovery restores before commit
and converges forward after commit. Recovery verifies actual restored states,
preserves log growth after restart attempts, and retains unresolved journals.
Original enablement link targets are restored on rollback; persistent boot
policy survives handover and later applies.

Imports use account distribution and instance coordination locks. Registry writes
and lifecycle operations reject a pending selected-instance import. Distribution
changes reject pending imports. Toolkit retirement quiesces registry/lifecycle
writes, rechecks references and ownership, archives verified program files, and
retires exact legacy command links only after all instances have transferred.
Installation can replace the verified legacy reboot command link and restore it
on precommit failure. Custom manager prefixes resolve their own host executable.

## Artifact evidence - 2026-10-17

Downloaded ARM64 Python, uv, and tunnel artifacts into /tmp. Each downloaded
SHA-256 equals the architecture lock and the current GitHub release asset digest:

- CPython: 11e713ae1f969385907a76533cc554f6b2e3f1a84896009f91c3fc871034a170
- uv: bb66cb52e7b1823aed1183630d8d8e5c958840d584a4c55ec10a4cfc168dcca2
- tunnel: c51bfd883fc22e3445494a03c0179875176564bde470661b308fd83af5d01abb

Published metadata: astral-sh/python-build-standalone tag 20260807, astral-sh/uv
tag 0.12.3, openai/tunnel-client tag v0.0.15. Observed identities after extraction:
Python 3.12.13; uv 0.12.3 (aarch64-unknown-linux-gnu); tunnel-client
0.0.15+a390c168ff1b2d14e73a95991c186c6aba3ff5a0.
The tunnel archive has exactly LICENSE, NOTICE, cloudflared,
cloudflared-manifest.json, tunnel-client, the arm64 license list and SPDX SBOM.

## Validation evidence

Tests-first failures reproduced missing import support, hidden diagnostics in
previous work, unsupported native artifact selection, stale inputs, failed
handover/recovery, the empty-backup retry boundary, wrong-account dispatch,
unverified restored states, and unsafe registry paths. Each affected regression
was rerun after its correction. Archive fixture root permissions/layout were
corrected to represent the published uv archive rather than weakening validation.

Final non-frontend suite: 413 passed in 460.772 seconds, including all
distribution fixture and installer edge tests. The two socket-dependent cases
and 28 Textual frontend cases were excluded from that run as described below.

Latest focused suite: 72 passed (legacy import, setup/maintenance, platform lock,
and path selection). Installer edge suite: verified legacy command replacement
and precommit restoration, both host profiles, ARM seven-file archives, and uv
regular-file-only layout for both architectures. All edge cases were rerun in
the later targeted suite (39 passed before the final registry-path guard).
Python compilation, Bash syntax and git diff --check passed.

With the locked Textual runtime installed, the frontend suite timed out after
180 seconds during asynchronous-generator shutdown, after the first test had
reported success. A broader run also stalled during frontend cleanup and was
interrupted. These runs do not establish a frontend-suite pass. Two Unix-socket
tests cannot run in this sandbox: both were retried and failed at bind with
PermissionError (operation not permitted). They are excluded from the separate
non-frontend suite; installer fixture tests remain included.

## Qualification boundary

All service transitions and manager installations in tests target disposable
fixtures and test-owned host adapters. Actual ARM artifacts were executed only
for version identity checks. A real user-systemd session is unavailable inside
the sandbox, so fixture success does not qualify live native/WSL handover,
cloud tunnel connectivity, or recovery against real services.

Read-only inspection found config/unit drift and nonstandard credential entries
in this machine's legacy profiles. The importer blocks those for review; it has
not rewritten them or read/copied environment credential contents. No running
user instance was migrated, stopped, restarted or removed. No release was
promoted/published/pushed. The production channel must contain the updated
manager for guided installation to expose this implementation.
