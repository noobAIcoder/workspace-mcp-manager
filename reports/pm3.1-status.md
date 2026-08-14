# PM3.1 — Context-aware instance setup status

Status: **QUALIFIED**

PM3.1 implements the normative context-aware instance setup specification on
top of the qualified PM3 Textual frontend while preserving declaration
`config_version = 2` and PM3/PM2 external behavior.

## Qualified implementation

- `instance discover <path>` resolves canonical physical workspace identity,
  local Git worktree context, existing-instance matches, deterministic instance
  ID suggestions, sanitized local Git evidence, and non-secret authentication
  status.
- `instance candidate` owns effective declaration construction, RFC 6901
  operator edits, copy/adoption precedence, field provenance, Access edits,
  tunnel-profile derivation, fingerprints, unresolved fields, and endpoint
  projection.
- `host ports` exposes manager declarations, explicit listener observation
  state, merged endpoint evidence, deterministic recommendations, and shared
  collision semantics.
- Planner, preview, recommendation, create/update preconditions, and the Textual
  workflow use the same manager-owned endpoint model. Listener uncertainty is
  preserved and endpoint changes fail closed.
- Normal workspace Git discovery is local/read-only and does not run
  `git ls-remote`. Secret-bearing remote userinfo is discarded.
- The Textual New Instance and Settings workflows retain structured operator
  intent and consume manager candidates; they do not synthesize effective
  declarations, derive profiles, or implement collision/Git/Access policy.
- The new-instance wizard uses an existing-instance dropdown, filesystem-only
  directory picker, manager-populated workspace/ID/Git/auth/ports, known-port
  projections, external Access folder selection, and provenance-aware review.
- `tunnel.profile` is manager-derived for new instances and absent from normal
  editable setup surfaces. Existing custom persisted profiles remain intact.

## Qualification evidence

Final live WSL qualification was executed through the established user-systemd
host boundary against `manager-qual` and a disposable unregistered Git
workspace. The combined qualification unit completed successfully with:

```text
Result=success
ExecMainStatus=0
PM3_1_PM3_REGRESSION=PASS
PM3_1_PM2_REGRESSION=PASS
PM3_1_LIVE_WSL_QUALIFICATION=PASS
PM3_1_CONTEXT_AWARE_UX_QUALIFICATION=PASS
```

Verification also established:

```text
full pure suite: 288 tests, OK (20 environment-dependent skips)
isolated Textual Pilot: 20 tests, OK
installed toolkit candidate/installed fingerprint:
1ccc4b04adbd3a27ad20071bcd1918c8d4f2bed1f79a776599e8c7b29f7647fd
```

The live regression exposed and corrected two pre-existing qualification/runtime
issues during PM3.1 qualification:

1. MCP normal stop returned status 143 and was classified failed by systemd;
   generated MCP units now declare `SuccessExitStatus=143`, producing the
   intended `inactive/dead` stopped state.
2. PM2/PM3 pseudo-TTY harnesses had stale assumptions about the isolated Textual
   runtime/interrupt path; interactive smoke now uses the toolkit Textual runtime.

A post-qualification clipboard follow-up removed the project-level `Ctrl+C` quit
override so Textual can handle selected-text copy/cut/paste normally, retained
`Ctrl+Q` as the explicit quit path, changed the pseudo-TTY smoke termination to
`Ctrl+Q`, and added focused Pilot coverage for an input `Ctrl+C`/`Ctrl+V`
round-trip without leaving the active screen.

## Final baseline

`manager-qual` was restored and revalidated after qualification:

```text
plan valid: true
operations: NOOP only
runtime: Running
health: Healthy
tunnel: Ready
reconciliation: Applied
recovery: Clean
```

The installed toolkit matched the candidate fingerprint and reported one valid
instance with valid entrypoints and no pending upgrade.
