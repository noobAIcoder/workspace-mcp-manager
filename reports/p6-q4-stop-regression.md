# P6-Q4 — present deployment stop was planned but not executed

## Observation

Live `manager-qual` qualification had already passed first apply, readiness,
status, generated-service authority/NVM checks, and the second NOOP apply. The
next lifecycle step failed at `instance stop`.

Post-failure evidence showed:

- desired lifecycle had been changed to `deployment=present,runtime=stopped`;
- MCP and tunnel processes remained running;
- the manager reported stop failure;
- MCP discovery and the tunnel had previously reached healthy/ready state.

## Cause

`ReconciliationPlanner` correctly emits `STOP` operations when deployment remains
present but runtime target is stopped. The P6 `HostApplyService._execute()`
`present` branch handled CREATE/UPDATE/ENABLE/START/RESTART but ignored STOP.
STOP was handled only in the `deployment=absent` removal branch.

The post-apply qualifier therefore observed active runtime units and rejected the
stop as non-converged.

## Correction

Lifecycle commands now use a lifecycle-specific `HostApplyService` that executes
present-deployment STOP operations inside the existing instance lock and
transaction journal before delegating all remaining work to the base P6
reconciler.

Stop ordering is dependency-safe:

```text
tunnel-client-<instance>.service
→ coding-tools-mcp-<instance>.service
```

No generated resources are removed and units remain enabled. Mutation attempts
and successful stops are persisted in the same transaction journal.

Regression coverage: `tests/test_lifecycle.py`.
