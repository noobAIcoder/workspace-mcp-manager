# M0 reconciliation evidence

The P0 capability freeze was derived from the verified reconciliation workspace.

| Artifact | SHA-256 |
|---|---|
| `reports/implemented-functionality-inventory.md` | `4daf6201f5b1c155759f44c9fd9f655f7bcae8097e66ab8266db8ca6e46a1892` |
| `reports/reconciliation-table.md` | `80acf06e92c7b88699f16a9bf5aeb5b032f7d0db79ab3ae045308d7904a76881` |
| `reports/canonicalization-backlog.md` | `0bc7b7825cf078fb1459f1dffe426103f707a83af6461d9bdccf35e4aa1d2bd8` |

Verified evidence snapshots:

```text
inputs/wsl/collection-20260809T193106+0300/
inputs/jetson/jetson-20260809T200925+0300/
```

The previous canonicalization backlog is historical planning evidence only. The
greenfield manager supersedes the "canonicalize workspace-mcp first" strategy.

`specs/capabilities.tsv` MUST contain exactly 130 explicit dispositions, one
for every row in the authoritative capability matrix. Allowed dispositions are:

```text
PRESERVE
REPLACE
RETIRE
DEFER
EXTERNAL
```

Each row also names its target phase. Acceptance semantics are disposition-wide:

- `PRESERVE` — the target phase MUST provide behavioral parity evidence;
- `REPLACE` — equivalent required outcomes MUST be demonstrated and intentional
  semantic changes documented;
- `RETIRE` — the capability MUST remain explicitly retired rather than silently
  disappearing;
- `DEFER` — the named target/post-MVP disposition remains tracked;
- `EXTERNAL` — the manager MUST NOT own the underlying secret/host facility.

