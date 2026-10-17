# Agentic Work Integration

## Scope and ownership

This contract owns integration bootstrap, repository-context interpretation, and installation/update boundaries. Component entry points are navigation until their bootstrap or activation step applies:

| Component | Entry point | Owner responsibility |
| --- | --- | --- |
| Work Models | [.work-models/AGENTS.md](.work-models/AGENTS.md) | Applicability, routing, composition, progression, gates, completion |
| Spec Set | [.spec-set/AGENTS.md](.spec-set/AGENTS.md) | Artifact integration after selection by Work Models |

Common authorization, execution, validation, and reporting policy remains with the external engineering guidance designated by [Work Instructions](.work-models/WORK_INSTRUCTIONS.md). This integration MUST reference rather than redefine component semantics.

## Path conventions

`REPO_ROOT` is the consuming repository root; `INSTALL_ROOT` is `REPO_ROOT/.agentic-work/`. Each component root is its directory beneath `INSTALL_ROOT`.

Markdown links are document-relative. Component contracts and registries use their declared component-relative bases. Repository integration paths beginning `.agentic-work/` and project artifact paths such as `docs/`, `src/`, `tests/`, and `.reports/` are repository-root-relative. Repo-info shard `file` values are installation-root-relative; their command `cwd`, evidence paths, and repository-source references are repository-root-relative. External sources MUST be identified as external rather than resolved as local paths. Unstated relative bases MUST be resolved before dependent use.

Repo Info repository-path values MUST remain lexically within the repository root on both POSIX and Windows. Absolute, drive-qualified, UNC, and URI locators are invalid in these fields, as is parent traversal above the root. `.` denotes the repository root; relative spelling that stays within it is permitted. This applies to runtime and validation command `cwd`, runtime `env.files[].path`, structure `root.workspace_files[]`, `paths[].path`, and `entrypoints[].path`, and every `authority_refs[].source` whose `kind` is `repository`. Schema-permitted unknown values retain their existing meaning. External authority locators and descriptive text are not repository-path fields. Structural validation does not establish path existence, symlink containment, or factual accuracy.

## Bootstrap

Apply these dependencies in order; a navigation reference or applicability inspection MUST NOT activate a module:

1. Enter [Work Models → Common contracts](.work-models/AGENTS.md#common-contracts). Load its manifest and Work Instructions, including the latter's baseline and designated external engineering guidance. Missing external authority is handled by Work Instructions, not replaced with an inferred or bundled copy.
2. Load `INSTALL_ROOT/REPO_INFO.yaml` and the applicable shards under the repository-context contract below. Missing context is handled only within its dependent scope.
3. Enter [Work Models → Routing entry](.work-models/AGENTS.md#routing-entry). Resolve or retain the route through its existing contracts, inspect applicability as needed, and load selected modules with their required composition, including routed frameworks.

The repository bootstrap ends with the active workflow obligations and constraints produced by those owners. It does not resolve execution topology from repository instructions or current capabilities.

Rerouting, ambiguity, methodology composition, and workflow failures MUST use their existing Work Model owners. A later material discovery returns to the owning routing contract; this bootstrap does not add a second routing decision or output format.

## Repository context

`REPO_INFO.yaml` is an index of observed facts, not a policy source. Select shards from the requested work's factual dependencies using the index's `use_for` descriptions as navigation hints, not keyword-only routing or mandatory workflow selection. Load additional shards only when their facts become relevant.

Facts, commands, hazards, summaries, and validation observations MUST NOT grant authorization, impose acceptance gates, or redefine intended behavior. `authority_refs` point to governing sources; the sources, not the fact records or their summaries, establish requirements. Current tool capabilities MUST be established in the current context, not inferred from recorded environments.

Interpret evidence at its recorded revision, time, environment, and scope. A file's existence, a command's presence, or a provenance label alone does not verify its claims. Null values and placeholders are unknown; empty collections mean no entries recorded, not verified absence. Positive and negative factual claims require evidence. Unknown or unverified values MUST NOT be promoted to verified facts.

Disagreement between accepted specifications and observed implementation evidence is drift. Record both sources and the affected scope. Consumers MUST NOT silently rewrite requirements to match observations, or relabel intended behavior as observed implementation. Resolve authority through Work Instructions and any active workflow. Facts disagreeing with other facts require checking their evidence and scope, not an unconditional file-precedence rule.

A missing or incomplete index, unknown required fact, unsupported schema, or missing relevant shard MUST be reported and affect only work depending on that context. Optional shards MUST NOT block unrelated work. Read-only evidence gathering follows existing authorization; it does not require persistent fact materialization first.

## Materialization and schemas

These owning templates define the supported field meanings and schema identifiers. Target names are relative to `INSTALL_ROOT`.

| Owning template | Repository-owned target | Supported schema |
| --- | --- | --- |
| [.templates/repo-info/repo_info.template.yaml](.templates/repo-info/repo_info.template.yaml) | `REPO_INFO.yaml` | `repo_info.v2` |
| [.templates/repo-info/runtime.template.yaml](.templates/repo-info/runtime.template.yaml) | `runtime.yaml` | `repo_runtime.v2` |
| [.templates/repo-info/structure.template.yaml](.templates/repo-info/structure.template.yaml) | `structure.yaml` | `repo_structure.v2` |
| [.templates/repo-info/validation.template.yaml](.templates/repo-info/validation.template.yaml) | `validation.yaml` | `repo_validation.v2` |
| [.templates/repo-info/hazards.template.yaml](.templates/repo-info/hazards.template.yaml) | `hazards.yaml` | `repo_hazards.v2` |

The v2 changes are incompatible: policy-valued fields are replaced with source references, provenance explicitly distinguishes unverified context, and index routing and paths change. The historical v1 identifiers retain their historical meanings; this distribution does not interpret v1 as v2. Any incompatible future field or semantic change MUST increment the affected identifier. Templates, consumers, validation, and incompatibility diagnostics MUST agree.

Initialization MUST distinguish the index and task-required facts from optional shards. When materialization is authorized, derive relevant facts from evidence and retain unresolved values as unknown. Templates are scaffolds, not repository-specific instances. The framework distribution MUST remain valid without any materialized fact files.

For an authorized refresh, verify changed facts and update the owning shard first; update the index's affected summaries next, then affected human-facing explanations only within authorized scope. Updating facts MUST NOT silently change their governing references or the referenced requirements.

Unsupported schemas MUST be reported without interpreting their payload as a supported schema, overwriting values, or regenerating files. Preserve the original; any required migration is a separate persistent change subject to existing authorization and evidence requirements. [The repo-info validator](tools/repo_info_validate.py) checks supported structure without writing facts or certifying their truth.

## Installation and updates

Framework-owned installation targets are the root entry-point instruction, this integration contract, and instruction guard, and distributed files within `.work-models/`, `.spec-set/`, `.templates/`, `tools/`, and `tests/` beneath `INSTALL_ROOT`. Generic human-facing repository templates live in [.templates/repo-info/](.templates/repo-info/); generating a repository README is not part of a framework update.

Ownership applies to identified distributed files, not every descendant of a directory. An update MUST compare the prior and replacement distributions to identify update/removal targets. Locally changed distributed files require reconciliation, not blind overwrite. Root-entry installation likewise MUST preserve unrelated existing repository instructions and relocate them only through an authorized reconciliation; the resulting entry file contains only the entry-point instruction.

The five repository-owned fact targets in the table above, all other repository-owned installation material, materialized specifications, reports, source code, and repository documentation are excluded from framework updates. Updates MUST preserve these against overwrite and deletion, including when replacing directories or removing obsolete framework paths. No recursive replacement, mirror deletion, or cleanup may erase excluded material. When prior ownership cannot be established, preserve the material and report the ambiguity.

Initial fact materialization, fact refresh/migration, and framework updates are distinct operations. An update MUST NOT initialize missing facts or regenerate incompatible ones. Delete an obsolete directory only after handling its identified framework files and confirming no repository-owned material remains. Preserve material outside declared update targets.
