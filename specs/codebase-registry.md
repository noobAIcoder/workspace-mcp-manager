# Codebase registry

| Path | Responsibility | M0 mutation |
|---|---|---|
| `src/workspace_mcp_manager/domain.py` | Strict desired/observed/applied domain model | none outside explicit registry writes |
| `src/workspace_mcp_manager/registry.py` | Atomic desired-state persistence | manager config state only |
| `src/workspace_mcp_manager/host.py` | Read-only host discovery/doctor | none |
| `src/workspace_mcp_manager/redaction.py` | Cross-cutting output/env redaction | none |
| `src/workspace_mcp_manager/paths.py` | Real-account/config/state path abstraction | none |
| `src/workspace_mcp_manager/cli.py` | Thin CLI over application services | registry writes only in `create/update` |
| `src/workspace_mcp_manager/generation.py` | P4 deterministic generated-resource model/renderers | none |
| `src/workspace_mcp_manager/planning.py` | P5 desired/observed reconciliation planner | none |
| `src/workspace_mcp_manager/runtime.py` | Secret-safe tunnel runtime helper referenced by generated tunnel units; admission runtime remains deferred | none unless invoked later by systemd |
| `specs/capabilities.tsv` | Frozen P0 capability dispositions | none |
| `specs/external-contracts.md` | Verified external runtime contracts | none |
| `tests/` | Stdlib-only M0 qualification | temp directories only |

No CLI/TUI layer may directly manipulate systemd resources. Future host
mutation MUST enter through application/reconciliation services. P4/P5 are
strictly render/observe/plan phases.

