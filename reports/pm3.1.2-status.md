# PM3.1.2 — Repository-Aware Development Toolchain Status

Status: **IMPLEMENTATION QUALIFIED — ELECTROCAD TOOLCHAIN REPAIRED; REPOSITORY LOCKFILE BLOCKS FULL REPOSITORY QUALIFICATION**

## Original defect

The manager-created `electrocad` declaration pinned:

```text
mcp.exec_path=/home/cloudtoor/.local/bin:/usr/local/bin:/usr/bin:/bin
mcp.external_roots=[]
```

while the WSL host's Node toolchains lived below `~/.nvm/versions/node/`.
Consequently the deployed ElectroCAD coding-tools MCP could execute Git/Python
but could not resolve `node`, `npm`, `pnpm`, or `corepack`.

This contradicted the existing external NVM contract, which requires the chosen
Node `bin` directory in `mcp.exec_path` and the complete version root in
`mcp.external_roots`.

## Repository-aware discovery

PM3.1.2 adds `development_toolchain_projection_version=1` and bumps PM3.1
workspace `discovery_version` to 2.

For ElectroCAD, discovery reads only bounded non-secret `package.json` metadata
and projects:

```text
Node requirement: >=24.18.0 <25
package manager:  pnpm 11.17.0
```

Before remediation the host contained Node 22.22.3 and 24.15.0; both were
correctly classified incompatible with the ElectroCAD Node requirement.

## Host remediation and manager reconciliation

Node 24.18.0 was installed as a separate NVM version and pnpm 11.17.0 was
installed in that version root. Existing NVM versions were preserved.

The manager then projected:

```text
selected node root:
  /home/cloudtoor/.nvm/versions/node/v24.18.0

recommended mcp.exec_path:
  /home/cloudtoor/.nvm/versions/node/v24.18.0/bin:
  /home/cloudtoor/.local/bin:
  /usr/local/bin:
  /usr/bin:
  /bin

recommended mcp.external_roots:
  /home/cloudtoor/.nvm/versions/node/v24.18.0
```

ElectroCAD was updated only through manager candidate construction, preview,
expected-current-fingerprint update, reviewed plan fingerprint, and normal
apply. No systemd file was manually edited. The post-apply manager plan is all
NOOP.

The live generated MCP unit now includes the selected NVM version root in both
PATH and `CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS`.

## Live deployed MCP evidence

Through the ChatGPT-facing `ElectroCADCodingTools` MCP after reconciliation:

```text
node      v24.18.0
pnpm      11.17.0
corepack  0.35.0
npm       11.16.0
```

This closes the original Node/pnpm-unavailable MCP finding.

## New independent repository blocker

The required ElectroCAD command `pnpm check` now reaches pnpm correctly. The
first non-TTY invocation identified pnpm's expected noninteractive purge guard;
with `CI=true`, that guard is satisfied and the next repository-owned gate is
exposed:

```text
ERR_PNPM_OUTDATED_LOCKFILE
```

ElectroCAD's current `package.json` and `pnpm-lock.yaml` disagree. pnpm reports
eight dependencies present in the manifest but absent from the lockfile plus an
`@eslint/js` version mismatch.

PM3.1.2 qualification deliberately does **not** use `--no-frozen-lockfile`,
because doing so would mask a repository reproducibility failure. Updating the
ElectroCAD lockfile is a separate repository change outside this manager fix.

Therefore:

```text
manager toolchain discovery              PASS
compatible Node selection                PASS
manager declaration reconciliation       PASS
deployed MCP Node/pnpm/corepack           PASS
ElectroCAD frozen lockfile                BLOCKED_OUTDATED_LOCKFILE
ElectroCAD pnpm check/build acceptance    BLOCKED_REPOSITORY_STATE
```

## Qualification

```sh
bash scripts/qualify_pm3_1_2_wsl.sh
```

Final manager source suite during this implementation:

```text
Ran 331 tests
OK (skipped=30)
```

The isolated Textual 8.2.8 frontend suite completed 28/28 tests with exit 0.

Final inherited regression chain:

```text
PM3_1_1_IMPLEMENTATION_QUALIFICATION=PASS
PM3_1_LIVE_WSL_QUALIFICATION=PASS
PM3_1_CONTEXT_AWARE_UX_QUALIFICATION=PASS
PM3_LIVE_WSL_QUALIFICATION=PASS
PM3_OPERATOR_UX_QUALIFICATION=PASS
PM2_TUI_QUALIFICATION=PASS
```

PM3.1's own qualifier remains a frozen `discovery_version=1` milestone
regression. Current `discovery_version=2` authority is qualified by PM3.1.2;
this avoids requiring an older installed PM3.1 parent manager to implement a
newer public contract merely to prove backward regression.

Expected manager markers include:

```text
PM3_1_2_REPOSITORY_REQUIREMENT_DISCOVERY=PASS
PM3_1_2_NODE_RANGE_EVALUATION=PASS
PM3_1_2_NVM_SELECTION=PASS
PM3_1_2_PNPM_VERSION_CONTRACT=PASS
PM3_1_2_CANDIDATE_TOOLCHAIN_RECOMMENDATION=PASS
PM3_1_2_ELECTROCAD_DISCOVERY=PASS
PM3_1_2_ELECTROCAD_DECLARATION_TOOLCHAIN=PASS
PM3_1_2_ELECTROCAD_MCP_NODE=PASS
PM3_1_2_ELECTROCAD_MCP_PNPM=PASS
PM3_1_2_IMPLEMENTATION_QUALIFICATION=PASS
```

Until ElectroCAD's repository-owned lockfile is repaired, its repository-level
acceptance remains blocked independently of MCP-manager readiness.
