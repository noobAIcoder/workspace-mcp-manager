# Fresh-system setup work

Authority: user instruction to implement the supplied fresh-system installation
and guided setup plan. Route: SPEC_IMPLEMENT_UPDATE, SDD, TDD, SDD_TDD.
The repository context index is absent; facts are inspected directly.

TODO_SDD: revise standalone-distribution.md and README for prerequisite
preparation, interpreter acquisition, guided configuration and verified results.
Acceptance is supplied by the instruction to implement this identified plan.

TODO_TDD: first reproduce installed TUI discovery with a custom prefix, no PATH
entry and current pointing at another release. Then cover strict lock validation
under Python 3.10, runtime acquisition failures, no-Python menu reachability,
host preparation cancellation, target identity, guided result validation and
deployment verification. Preserve installation-only overrides, ordinary TUI,
JSON interfaces, transaction ownership and offline recovery.

Completion requires affected regression suites and separate live qualification
on WSL2 Ubuntu 24.04 x86_64 and native Ubuntu 22.04 ARM64. Mocked evidence does
not establish either fresh-environment qualification.

Implemented: option 7 keeps host preparation, reviewed distribution installation
and guided instance setup separate. Target checks precede account writes; package
and WSL edits require default-cancel reviews. Existing-instance continuation is
explicit. The TUI uses a private versioned result and verifies applied declaration
identity, services, MCP protocol responses and tunnel endpoints before completion.
Unknown/failing checks, stale applied state and declaration-only creation remain
incomplete. Polling is bounded and retry is offered. PATH additions are reviewed
and idempotent. Ordinary TUI flags, setup numbers and installation automation are
retained. No third-party dependencies or runtime pins changed.

The strict lock validator is shared Python 3.10 standard-library code. Bootstrap
validates the selected architecture lock after provenance before acquiring a
digest-checked temporary Python when needed. Interpreter selection avoids shell
function recursion and prefers immutable owned runtimes. Local checks do not
download Python. Shared helpers ship in immutable content manifests and recovery
capsules. install.sh retains distribution mutation authority.

Observed verification, 2026-11-07, Python 3.12 on Ubuntu 24.04 ARM64 (an unsupported
live target, so supported profiles are represented only by synthetic fixtures):

- The new executing-release TUI discovery regression failed before the fix;
  resolver/TUI regressions then passed (24 tests).
- Guided result and menu regressions failed before implementation.
- Affected setup, maintenance, acquisition, resolver, client, instance-upgrade,
  recovery and import-edge suites passed 162 tests in 93.6 seconds.
- The distribution/installer/local-setup run passed 33 of 34 tests. Its remaining
  extracted-function fixture omitted SCRIPT_DIR after helper extraction. After
  correcting that fixture, three targeted tests passed: strict repository lock,
  manifested release contents, and installed setup plus recovery after deletion
  of the acquisition checkout (22.6 seconds).
- Additional guided host/result tests passed (11 tests): package cancellation and
  failure, unavailable systemd, restart/resume, unrelated WSL settings preservation,
  duplicate-free continuation, invalid/missing handoff results, stale applied
  state and declaration-only creation.
- Bash syntax, Python compilation and affected-file whitespace checks passed.
- Final focused checks passed 39 tests; host-stage checks also verify invoking
  account/prefix mismatch rejection and repeated PATH additions without duplicates.
  Runtime-failure checks cover digest rejection, interrupted acquisition and
  refusal to download for a local candidate check.

Unverified: real Python 3.10 execution (not installed here); live locked-runtime
download/extraction; fresh WSL2 Ubuntu 24.04 x86_64 and native Ubuntu 22.04 ARM64
qualification. Textual's 29 frontend tests were skipped because the optional
runtime is unavailable. These are qualification gaps, not passing evidence.
No live package installation, WSL configuration edit, distribution activation,
provider authentication or instance deployment was performed.

Option 7 follow-up: accepted scope adds native Ubuntu 24.04 ARM64. Existing
specification profile lists updated; runtime pins preserved. Regression first
reproduced rejection of native Noble. Bootstrap readiness now reuses the host
predicate; installer accepts the same additional tuple. Terminal-only return
acknowledgment preserves scripted input. Live qualification remains pending.

Working-tree redesign: interactive checkout bootstrap now selects an isolated deterministic local snapshot rather than mixing local helpers with the production release. Snapshot capture preserves original Git state and records manifested provenance. Focused validation does not establish live deployment qualification.

Snapshot verification: real local --check passes and option 7 reaches candidate
review without a manager-source fetch; cancellation applies no installation.
Focused bootstrap/menu suite: 42 tests passed. Maintenance/local/host suite:
63 passed. Snapshot-specific suite: 4 passed. Installer/runtime/result suite:
10 passed. These runs overlap. Real ARM64 packages were installed under an
isolated /tmp account home; installed manager and TUI smoke run, and the actual
guided NewInstanceScreen opened and cancelled successfully. Textual suite ran
29 tests: 28 passed, one existing access-edit pilot click raised OutOfBounds.
A trial scroll correction did not resolve it and was reverted. No authenticated
instance deployment or fresh supported-profile qualification is claimed.

Final live snapshot 839c3cfa64a29b377c1c6a191c86f8cb2b240348 installed successfully; a second full bootstrap returned action=noop for the same distribution identity. The first live trial activated successfully but its mutable caller script was edited concurrently during qualification, causing a trailing shell parse error; the final trial avoided concurrent installation-input edits.
