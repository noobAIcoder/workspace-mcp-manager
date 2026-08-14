# Codebase registry

| Path | Responsibility | Mutation authority |
|---|---|---|
| `src/workspace_mcp_manager/domain.py` | Strict v1/v2 desired-state model and validation | none |
| `src/workspace_mcp_manager/registry.py` | Atomic declaration persistence and one-way schema upgrades | manager registry only |
| `src/workspace_mcp_manager/paths.py` | Real-account/config/state path abstraction | none |
| `src/workspace_mcp_manager/host.py` | Host discovery/doctor | read-only |
| `src/workspace_mcp_manager/redaction.py` | Cross-cutting output/environment redaction | none |
| `src/workspace_mcp_manager/generation.py` | Deterministic manager-owned files, units, profiles, launchers and PM1 resources | render only |
| `src/workspace_mcp_manager/planning.py` | Desired/observed reconciliation and ownership-gated plans | observe only |
| `src/workspace_mcp_manager/host_apply.py` | Locked, journaled host reconciliation and rollback | generated resources, systemd, owned PM1 Git keys |
| `src/workspace_mcp_manager/lifecycle.py` | Complete lifecycle wrapper including present/stopped execution | delegates to host apply |
| `src/workspace_mcp_manager/development_environment.py` | PM1 GitHub profile, local Git identity/transport and agent-provider reconciliation | exact repository-local owned keys only |
| `src/workspace_mcp_manager/admission_recovery.py` | Admission guard plus applied-evidence reconstruction for obsolete owned resources | reconstruction only |
| `src/workspace_mcp_manager/git_diagnostics.py` | Instance Git/GitHub diagnostics under effective environment | read-only |
| `src/workspace_mcp_manager/endpoint_projection.py` | PM3.1 TCP listener evidence and shared bind-overlap semantics | read-only |
| `src/workspace_mcp_manager/setup_projection.py` | PM3.1 workspace discovery, candidate/provenance, local Git/auth and port projections | read-only candidate construction |
| `src/workspace_mcp_manager/operator_contracts.py` | PM3/PM3.1 operator template, state and plan-fingerprint contracts | none |
| `src/workspace_mcp_manager/operator_projection.py` | Manager-authoritative summaries/preview/review projections | read-only |
| `src/workspace_mcp_manager/tui.py` | Public-CLI adapter plus pure frontend helpers | manager CLI delegation only |
| `src/workspace_mcp_manager/tui_textual.py` | PM3/PM3.1 Textual projection/editor frontend | manager CLI delegation only |
| `src/workspace_mcp_manager/runtime.py` | Secret-safe tunnel/admission runtime helpers used by systemd | runtime-scoped |
| `src/workspace_mcp_manager/cli.py` | Thin public/private command routing | delegates only |
| `scripts/qualify_*.sh` | Reproducible live qualification harnesses | disposable qualification targets only |
| `specs/capabilities.tsv` | Capability disposition authority | none |
| `specs/external-contracts.md` | Verified external runtime contracts | none |
| `specs/pm1-development-environment.md` | CAP-117/119/120/123 post-MVP authority | none |
| `specs/pm2-tui.md` | Post-MVP terminal frontend authority | none |
| `tests/` | Stdlib-first pure verification | temporary fixtures/repositories only |

CLI/TUI code MUST NOT manipulate host resources directly. Host mutations enter
through the host-boundary application/reconciliation services. Authentication,
private keys, tokens, passphrases, known-host databases and trust stores remain
outside manager ownership.

