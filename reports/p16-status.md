# P16 — Per-instance launcher status

Status: **PASS — LIVE WSL QUALIFIED**

Normative authority: `specs/p16-instance-launchers.md`.

P16 implements the final frozen MVP capability, CAP-003: the deterministic
manager-owned `<instance>-mcp` launcher. The launcher is a narrow frontend over
the authoritative manager registry/CLI and exposes only existing instance,
access, and Codex operations.

## Verification

Before the live gate:

```text
focused P16 verification: 21 PASS
complete pure suite:      194 PASS, 1 skipped
```

The skipped check is the existing systemd verification case blocked by the MCP
sandbox; generated-unit behavior is covered elsewhere and live qualification
passed.

## Live WSL gate

Qualified on disposable `manager-qual`:

```text
P16_LAUNCHER_RESOURCE=PASS
P16_LAUNCHER_DISPATCH=PASS
P16_LAUNCHER_REJECTION=PASS
P16_WSL_QUALIFICATION=PASS
```

Final live state was converged with empty external access, all manager plan
operations `NOOP`, healthy MCP discovery, and healthy tunnel readiness.

## Current gate

```text
P16_IMPLEMENTATION=PASS
P16_PURE_VERIFICATION=PASS
P16_LIVE_WSL_QUALIFICATION=PASS
FROZEN_MVP=COMPLETE
```
