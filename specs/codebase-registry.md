# Codebase registry

| Path | Responsibility | M0 mutation |
|---|---|---|
| `src/workspace_mcp_manager/domain.py` | Strict desired/observed/applied domain model | none outside explicit registry writes |
| `src/workspace_mcp_manager/registry.py` | Atomic desired-state persistence | manager config state only |
| `src/workspace_mcp_manager/host.py` | Read-only host discovery/doctor | none |
| `src/workspace_mcp_manager/redaction.py` | Cross-cutting output/env redaction | none |
| `src/workspace_mcp_manager/paths.py` | Real-account/config/state path abstraction | none |
| `src/workspace_mcp_manager/cli.py` | Thin CLI over application services | registry writes only in `create/update` |
| `specs/capabilities.tsv` | Frozen P0 capability dispositions | none |
| `specs/external-contracts.md` | Verified external runtime contracts | none |
| `tests/` | Stdlib-only M0 qualification | temp directories only |

No CLI/TUI layer may directly manipulate systemd resources. Future host
mutation MUST enter through application/reconciliation services added after M0.

