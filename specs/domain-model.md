# Domain and lifecycle contract

## State model

The manager uses three distinct states:

1. **Desired state** — versioned declarations under the instance registry.
2. **Observed state** — freshly collected host/runtime evidence.
3. **Applied/ownership state** — non-secret fingerprints proving which resources
   were generated/adopted by the manager and what the last successful apply was.

Observed state remains authoritative for live health. Applied state MUST NOT be
used as a substitute for observing the host.

P5 authorizes destructive planning only from current ownership evidence such as
manager ownership markers. A future applied-state record MAY corroborate that
evidence but MUST NOT override missing/foreign live ownership. P6 is the first
phase permitted to persist `applied.json` after a complete successful apply.

## Lifecycle semantics

The desired declaration contains:

```text
lifecycle.deployment = present | absent
lifecycle.runtime    = running | stopped
```

`deployment=absent,runtime=running` is invalid.

Future command semantics are frozen as follows:

| Command | Desired-state effect | Host effect |
|---|---|---|
| `instance create` | create declaration | none |
| `instance update` | replace declaration after strict validation | none |
| `instance plan` | none | observe only |
| `instance apply` | none | converge to declaration |
| `instance start` | set runtime target `running` | reconcile |
| `instance stop` | set runtime target `stopped` | reconcile |
| `instance remove` | set deployment `absent`, runtime `stopped` | reconcile |
| `instance delete` | delete declaration only after observed resources are absent | observe + registry delete |
| `cleanup/audit` | none | read-only by default; explicit apply required later |

M0 exposes only declaration validation/create/update/list/show and read-only host
inspection. Host mutation commands MUST NOT be implemented before the M0 review.

P4/P5 add deterministic resource rendering and reconciliation planning only.
They MUST NOT write generated resources or invoke mutating systemd operations.

The manager-owned tunnel profile directory is:

```text
~/.config/workspace-mcp-manager/tunnel-profiles/
```

This intentionally avoids co-locating generated profiles with the external
`tunnel-client/runtime.env` credential file.

## Configuration rules

- JSON is the canonical manager declaration format for schema version 1.
- Unknown fields MUST fail validation at every object level.
- Paths MUST be absolute POSIX paths and are normalized lexically.
- Instance IDs MUST use lowercase letters/digits and single hyphens.
- Access aliases preserve the legacy letters/digits/single-hyphen-or-underscore
  contract and MUST remain unique after `-`/`_` normalization.
- RO/RW source paths MUST be unique and MUST be external to `workspace_path`.
- MCP and tunnel-health endpoints MUST NOT collide within one declaration.
- Secrets MUST NOT be accepted as declaration fields.

