# Four-mode folder access implementation

Authority: user-approved four-mode access plan and instruction to implement it.
Route: SPEC_IMPLEMENT_UPDATE with SDD/TDD analysis and execution. No new direct
dependencies; the existing Textual 8.2.8 dependency was installed only under /tmp
for frontend validation. The repository context index is missing; source and
existing specifications supplied implementation facts.

## TODO_SDD disposition

Updated P8's desired-state, command, namespace, cleanup, and qualification
contracts for RO/RX/RW/RWX. Updated the operator contract and README. The user's
confirmed decisions are enforced noexec for RO/RW and adoption of those meanings
by existing grants at next apply. RX/RWX remain subject to underlying host
permissions. No source chmod, direct file-tool boundary expansion, or automatic
apply is introduced. Empty optional execute groups preserve old fingerprints.

## TODO_TDD disposition

Added coverage for four-mode declaration roundtrips, all-mode mutations, CLI
parsing, cross-group collisions, mount directives, preflight restrictions,
resource labels, setup provenance, mode-change ownership retention, final
cleanup, and the optional Textual dialog validation for all four modes. The summary test
first failed for missing read_execute_count, then passed after implementation.
Core backend changes from the preceding turn were already present; subsequent
tests establish regression evidence rather than retroactive RED evidence.

Existing access, generated-unit verification, host-apply rollback/restart,
domain, legacy-import, stale-state, and operator contract tests remain relevant.
Golden changes were inspected and limited to the MCP unit (NoExecPaths) and
launcher (new access commands and update dispatch).

## Validation

- Targeted backend regression run: 138 tests passed.
- Follow-up access, operator-contract, and generation run: 22 tests passed,
  including the added execute-mode review-label test.
- Full default-environment discovery: 518 tests, 488 passed, 28 optional Textual
  tests skipped, and two Unix-socket PermissionErrors caused by the sandbox.
  Both socket tests passed when rerun individually with approved escalation.
- Python compilation, Bash qualification-script syntax, and git diff whitespace
  checks passed.
- Live user-systemd/MCP namespace qualification was not performed. The disposable
  P8 script now prepares four modes and executable probes; the specification
  requires direct execution denied for RO/RW, permitted for RX/RWX, the write
  matrix, unchanged source permissions, and cleanup before live qualification
  can be claimed.

- The full optional Textual run was interrupted after it stalled during shutdown
  following the existing RW add/review/apply case. A copied RWX flow also reached
  an ok assertion result but stalled during shutdown; it was replaced by a
  focused four-mode dialog test. No complete optional UI-suite pass is claimed.
- Focused Textual Pilot dialog test passed for RO, RX, RW, and RWX with Textual
  8.2.8 installed under /tmp (one test, four subcases, clean process exit).
