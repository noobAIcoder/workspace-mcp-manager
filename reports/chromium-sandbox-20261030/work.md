# Chromium sandbox follow-up

## Current result (supersedes the intermediate records below)

**Chromium acceptance PASSES through the actual local MCP execution tool.**
Both bounded namespace support and directory-only access were explicitly approved
by the user. No additional approval remains pending for these changes.

- MCP command `o4YGSkS1J1rRX3xpZmKrNslR` launched the exact installed executable
  with Playwright `chromiumSandbox:true`, navigated an owned loopback HTTP page,
  asserted its title, executed JavaScript returning 42, and captured a PNG.
- Screenshot: [browser-v4.png](browser-v4.png), 17,671 bytes,
  SHA256 `4a7a4516c57e7f9ffa90bfc0ff049dae8b4cfceb1e9b36d594d41e7aec907183`.
  Raw MCP receipts: `browser-v4-101.json` through `browser-v4-103.json`.
- Browser PID 583297 exited normally (code 0). The owned profile was removed.
  Host service process enumeration confirmed only MCP PID 583243 remained;
  see `browser-final-cleanup.json`.
- MCP command `pXD8GXj-pxlEveQ5vWiVfsWb` rechecked direct, spawned-child and
  worker Node memoryUsage successfully, retaining sensitive-proc/write denials.
  Receipt: `node-final-101.json`.
- Final relevant regression suite: **129 tests pass**, no skips, 7.396 seconds.
  This includes failing-before/fixed-after regressions for the proc anchor,
  inherited self-FD listing, equivalent openat-dot anchor reopen, and nested
  user-namespace mappings, plus the preserved Node/security/lifecycle suite.
- `effective-policy-final.json` confirms Landlock enabled, ABI 7, UID 1000,
  correct workspace, and the precise additional broker scope. Browser cache
  writes remain EACCES and alias writes EROFS.
- Extra isolated production-build recheck: **FAIL**, separately diagnosed.
  MCP command `Nzz44-3REzBVxmEHU-GQbx4C` reached Next/font and failed at
  `google/loader.js:122` because its font URL extension regex returned null.
  MCP probe `L4neMYji8r90gmyUwzuRYkkI` used Next's unmodified fetcher and parser:
  current Cormorant Garamond CSS contains five `/l/font?kit=...` URLs without the
  extensions expected by Next 15.4.11. The other three font families' URLs passed
  that check; no mocked responses were configured. See `font-response-101.json`
  and `build-final-*.json`. This requires no additional proc permission and was
  not addressed by changing application/font/dependency code.
- Build isolation checks passed: root tsconfig/next-env hashes were unchanged,
  `rootTypeConfigPreserved:true`, and owned output `next-build-HpTtiI` was removed.
  This work preserves the existing isolation fix but cannot claim a passing
  production build against the current Google Fonts response.

### Root cause and final implementation

The original stat broker covered neither self maps nor namespace mapping files.
Maps denial was separate from the fatal sandbox-selection failure: mapping
initialization was blocked even though user namespace creation itself worked.
Chromium also needs a proc directory anchor, its own FD directory listing, and
`openat(proc_fd, ".")` to reopen that anchor. Directory FDs grant no Landlock
subtree rule; ordinary proc file opens still run under the existing ruleset.
The kernel rejects seccomp injection of O_PATH FDs with EBADF, so directory-entry
enumeration was explicitly approved as the supported narrow alternative.

Chromium then creates a nested user namespace. Linux rejects its unprivileged
mapping writes from the broker's original namespace (EPERM). A short-lived,
fixed-operation helper enters only the pinned, verified owned parent namespace,
retains UID/GID 1000, performs the already validated mapping write, and exits.
The main supervisor never changes namespace. Helpers are bounded and reaped.
No arbitrary mapping text or writable proc FD is exposed to command processes.

Effective added scope: read-only exact self stat/maps; root proc directory entries;
requester's FD directory entries; equivalent root-anchor reopening; and only deny
plus single existing-ID identity mappings in owned descendant user namespaces.
No other proc content, proc subtree rule, browser-cache write, sandbox-disabling
flag, setuid change, root execution, browser relocation/reinstallation, or global
kernel/AppArmor/systemd security change was introduced.

The final backend changes remain in the four files listed below. The local
Podolsky patched runtime was updated; the shared installed package, service unit
and instance declaration were preserved. Opt-in `CODING_TOOLS_MCP_PROC_DEBUG=1`
logs broker decisions; temporary syscall tracing was removed before acceptance.
Only native x86_64 was qualified. Existing nondumpable-task restrictions remain.

## Intermediate diagnostic history

Authority: original user constraints and approved bounded namespace broker expansion.
Route: SPEC_IMPLEMENT_UPDATE; SDD and TDD; SDD_TDD; no enclosed framework.
Repository context: `.agentic-work/REPO_INFO.yaml` remains absent; inspect sources and runtime directly.

TODO_SDD: specify exact self-maps read and owned descendant namespace initialization,
preserving all original security exclusions. User approval accepts this scope.
Backend contract staged as `docs/proc-broker-contract.md`.

TODO_TDD: failing direct/child maps and namespace mapping tests observed before
implementation. Require denial of original/other namespaces, invalid mappings,
writes via unsupported mechanisms, transferred handles and stale notifications.
Preserve existing Node, lifecycle and runtime-helper coverage. Completion requires
actual target MCP Playwright page/JS/screenshot/cleanup plus direct/child Node.

Initial evidence: MCP `unshare(CLONE_NEWUSER)` succeeds; opening self setgroups,
uid_map and gid_map for write returns EACCES. Existing maps read also EACCES.
The namespace failure is independent of the maps diagnostic. Host service retains
PrivateUsers=yes, RestrictNamespaces=no and no syscall allowlist. Runtime is
unprivileged UID 1000, Landlock ABI 7, on WSL 6.18.54.1.

Staged tests: 132 pass before target deployment (existing broker/runtime tests plus
initial Chromium regressions). No passing browser qualification is yet claimed.

## Implemented and deployed

Changed backend `coding_tools_mcp/proc_stat_broker.py`, policy metadata in
`coding_tools_mcp/server.py`, `tests/compliance/test_chromium_proc_broker.py`, and
`docs/proc-broker-contract.md` in `/home/cloudtoor/repos/coding-tools-mcp`.
`backend-changes.patch` records only this follow-up's delta against the previously
modified source. Existing landlock_exec and self-stat fixes were preserved.
Only the Podolsky patched runtime was updated; the shared installed package,
systemd unit and instance declaration were not changed.

Effective scope: exact read-only self stat/maps plus fixed namespace operations
on the requesting process's owned descendant user namespace. The broker validates
namespace ancestry/ownership, current credentials and process identity; pins the
process and namespace; and accepts only deny or existing-ID identity mappings.
Commands receive sealed memfd placeholders, never writable namespace proc FDs.
Writes through other APIs cannot modify those sealed objects. No proc-directory
rule was added. Browser cache remains RX (mask 13); profiles remain in MCP TMPDIR.
Each command has a 128-handle bound. Nondumpable memory access still fails closed.

## Earlier-stage evidence

- Relevant suite: **125 tests pass**, no skips, 6.969 seconds. Command:
  `PYTHONPATH=/tmp/chromium-broker-fix /home/cloudtoor/.local/share/uv/tools/coding-tools-mcp/bin/python -B -m unittest tests.compliance.test_proc_stat_broker tests.compliance.test_chromium_proc_broker tests.compliance.test_runtime_helpers`
- New tests cover direct/child maps, original namespace denial, invalid mapping
  contents, stale namespaces, inherited handles, alternate writes, repeated writes,
  unsafe flags/aliases, notification expiry, resource bounds and proc root denial.
- Actual MCP command `JDMgy2E9Yn_aY580zFpIJT-h`: self/child maps reads pass;
  all three fixed namespace operations pass; invalid contents denied; UID/GID
  remain 1000. Receipt `mapping-70.json`.
- Actual MCP command `nM4PQ0P6zaujLu4N2jnEVqr1`: direct, child and worker Node
  memoryUsage pass; other-process stat, sensitive self proc and writes denied.
  Receipt `node-50.json`.
- Actual MCP browser command `qhVXU9fa2ZWPkt_VMVIoTTha` launches the exact
  installed executable with chromiumSandbox:true. The maps error disappears;
  namespace setup progresses to Chromium's capability check, which now fails at
  `sandbox/linux/services/proc_util.cc:38` opening the proc directory (EACCES).
  No page, JavaScript or screenshot acceptance is claimed.
- Source cache write remains EACCES; read-only alias remains EROFS.
- Browser PID 566324 is absent, owned temporary profile is absent, and service
  cgroup contains only MCP PID 566171. Recorded in `summary.json`.
- Source and deployed code hashes match; final policy is `effective-policy.json`.
  Rollback copies: `/tmp/chromium-broker-fix/before-deploy`.

## Earlier limitation (approval subsequently received)

Exact Chromium version: 147.0.7727.15. Its `ProcUtil::OpenProc()` opens `/proc/`
with O_RDONLY|O_DIRECTORY, and its capability checks stat self/task relative to
that anchor. Later ProcUtil operations open self/fd/ for descriptor enumeration.
Sources inspected at:
https://raw.githubusercontent.com/chromium/chromium/147.0.7727.15/sandbox/linux/services/proc_util.cc
https://raw.githubusercontent.com/chromium/chromium/147.0.7727.15/sandbox/linux/services/thread_helpers.cc
https://raw.githubusercontent.com/chromium/chromium/147.0.7727.15/sandbox/linux/services/namespace_utils.cc

A metadata-only O_PATH anchor prototype was rejected by the kernel's
SECCOMP_IOCTL_NOTIF_ADDFD with EBADF (9). Independent reproduction is recorded
in `opath-kernel-limit.json`. The unsupported prototype was removed and was
never deployed. No inferred kernel/sysctl/AppArmor relaxation is justified.

Smallest proposed additional scope: inject an actual read-only descriptor to the
proc root directory as Chromium's anchor, plus exact read-only self/fd/ directory
opens (absolute or relative to a verified proc anchor). This would expose root
directory entry names/process IDs and the caller's FD numbers. It would not add
a Landlock proc subtree rule or permit reading other proc files, reopening FD
contents, writes, or other-process data files. Such enumeration is beyond the
previously approved file-only expansion, so approval was requested under original
requirement 5. Approval was received and this capability was implemented; the
current-result section above records the successful browser acceptance.

The application and its isolated build configuration were untouched. At this
earlier stage a build rerun remained gated on browser acceptance; the later rerun
is recorded above. Only native x86_64 was qualified;
aarch64 remains unqualified.
