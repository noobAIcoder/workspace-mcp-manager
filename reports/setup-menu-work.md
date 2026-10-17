# Setup menu implementation record

Authority: user-approved “Simple installation and maintenance menu” plan and
instruction to implement it (2026-10-17). Route: SPEC_IMPLEMENT_UPDATE; SDD/TDD
composition; no added dependencies or Spec Set artifacts. The newly added
agent integration has no REPO_INFO.yaml; implementation facts are inspected
from source rather than inferred from missing context.

## TODO_SDD

Target: standalone-distribution specification. Add interactive setup, explicit
noninteractive bootstrap, rollback and uninstall previews, preservation and
recovery semantics. Amend the install-only commit contract for uninstall's
atomic quarantine rename, and retain a maintenance frontend independently of
current so rollback to earlier distributions remains manageable. Existing
installation transaction and declaration formats remain compatible.
Acceptance: user instruction to implement the identified plan. Validation:
compare the specification additions to each menu action and failure behavior.

## TODO_TDD

New coverage: offline status/menu, confirmation cancellation, stale previews,
validated retained releases, rollback compatibility and interruption recovery,
uninstall ownership rejection, preservation, partial instance removal/retry,
and uninstall interruption/reinstallation. Existing coverage: distribution
installation/provenance, declaration preservation, runtime selection, recovery.
Regression boundary: existing upgrade CLI remains noninteractive, fixed channel,
exact manifest checks, locking, old journal recovery, and unchanged updates to
instances. Python/Bash checks and applicable suite required. Live WSL and
frontend/runtime limitations must be reported separately, never passed by
weakening production host/integrity checks.

## Implemented result

The specification additions are accepted through the user's instruction to
implement the reviewed plan. Source comparison confirmed that the existing
transaction authority could be reused; deliberate rollback and uninstall needed
new maintenance transactions. No third-party dependencies were added.

- Bootstrap now defaults to a numbered menu in an interactive terminal; explicit
  `--interactive` permits scripted menu input. Automation must select
  `--non-interactive`. The installed setup command retains the selected home and
  prefix and remains available after rollback to an older release.
- Local inspection and maintenance do not acquire remote code. Installation
  freezes the fetched commit, shows the candidate and runtime versions, confirms,
  and rechecks the approved state under the installer lock. Existing upgrade
  automation remains noninteractive.
- Maintenance previews are fingerprinted and rechecked under the distribution
  lock. Rollback validates actual runtime identities and declaration compatibility;
  instance declarations and services remain unchanged.
- Confirmed uninstall backs up original declarations, removed profiles/logs, and
  recognized historical toolkit releases. It uses the normal lifecycle reconciler
  before quarantining program files. Failed removal retains the manager and
  reports the backup and offline recovery command. Surviving records remain
  absent/stopped; credentials and workspaces are untouched.
- A registry write gate, instance locks, pending-maintenance guards, and final
  service-reference checks protect the removal/activation boundary. Quarantine
  recovery permits partial deletion but rejects unexpected or changed content.
- Recovery capsules are staged before installation transactions and before
  deliberate maintenance. The established recover-only command still emits one JSON object
  with recovery_result and ownership; maintenance adds only relevant fields.

## Observed validation — 2026-10-17

Python 3.12.13:

- `PYTHONPATH=src python3.12 -m unittest tests.test_setup_maintenance -v`:
  **33 passed**. Includes real Bash bootstrap acquisition/review against a local
  Git fixture, default cancellation, stale-state rejection, exact uninstall
  confirmation, custom-prefix wrappers, adapters to lifecycle/planning, registry
  exclusion, preserved user data and declaration records, and interruption
  recovery before/after activation and after partial quarantine cleanup.
- The installer recovery integration tests invoke actual install.sh and the
  standalone recovery capsule against temporary synthetic distributions. They
  verify offline recovery and the single-document JSON contract without changing
  host services or installed applications.
- Applicable unittest discovery: **375 run, 347 passed, 28 skipped**. The 28
  Textual Pilot cases require the separate isolated frontend runtime.
- 25 test methods excluded for known environment constraints: 23 existing
  distribution installer methods require WSL Ubuntu 24.04 x86_64; two Unix-socket
  cases cannot bind inside this sandbox. The current host is Ubuntu 22.04 ARM64.
  These exclusions are not passing full-suite evidence.
- Python compilation of src/tests, Bash syntax checks for bootstrap and all
  scripts, and `git diff --check` passed.

Tests-first evidence: the initial setup tests failed because the backend
was absent; the registry mutation regression then failed until the maintenance
write guard existed; the acquired-install preview race test failed until stale
state was rejected. A service-enumeration assertion was corrected to compare
membership rather than imposing an irrelevant ordering.

## Remaining qualification boundary

No live installation, migration, instance removal, service restart, rollback,
uninstall, release promotion, commit, push, or deployment was performed.

Run full discovery and scripts/qualify_distribution_wsl.sh on supported WSL
Ubuntu 24.04 x86_64. The script now exercises the installed offline setup menu
and opts into explicit bootstrap automation. Live qualification must additionally
exercise deliberate rollback, deployment-removing uninstall, and interruption
recovery against disposable real instances. No production host, integrity, or
permission checks were relaxed to obtain passing local evidence.

A final lifecycle-adapter regression reproduced an important failure sequence:
the worker persists absent/stopped intent before reconciliation can fail. Recovery
now tracks successful removals explicitly in its journal instead of treating
absent intent as proof of actual deployment removal. Failure results and recovery
identify completed removals and remaining work; the regression passed after the
fix. Recovery capsules are prepared for rollback as well as uninstall, including
when the active release predates maintenance support.

## Bootstrap inspection correction — 2026-10-17

TODO_SDD: distinguish manager installation from the legacy workspace-mcp toolkit;
retain inspection errors and permit stdlib status inspection on Python 3.10.
TODO_TDD: reproduce suppressed backend errors and legacy detection, then verify
read-only system-Python status without filesystem changes.

Both diagnostic regressions failed before the correction. The installer now
selects a compatible interpreter for package operations while status accepts
Python 3.10. The setup menu retains backend diagnostics instead of replacing
them with “unavailable or blocked” and identifies legacy tooling separately.
The existing platform support policy remains in force. No user services or
installation files were changed.

Validation: setup/maintenance suite 36 tests passed; system Python 3.10.12
successfully returned absent-installation status; Bash syntax and diff checks
passed. These checks do not establish live installation or service migration
qualification.
