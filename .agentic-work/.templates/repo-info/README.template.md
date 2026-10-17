# `<Project Name>`

`<One concise human-readable paragraph explaining what the project is, the problem it solves, and who it is for.>`

<!--
Human-facing template. Populate from verified repository evidence and accepted project specifications as appropriate. Do not invent missing facts or expose secrets. Optional sections may be omitted when not useful.
Paths in this template describe the consuming repository root, not this template's directory.
-->

## Status

| Field | Value |
| --- | --- |
| Repository | `<owner/repo>` |
| Project status | `<prototype / active / production / legacy / mixed / archived>` |
| Primary users | `<users/operators>` |
| Target environment | `<OS / hardware / cloud / runtime>` |
| Default branch | `<branch>` |

`<What currently works, what remains incomplete, and the environment assumed by this README.>`

## Capabilities and boundaries

This project provides:

- `<capability>`
- `<capability>`

Current boundaries:

- `<non-goal or unsupported capability>`
- `<important limitation>`

## Architecture

`<Short project-specific explanation of the major runtime pieces and how they interact. Omit diagrams that are not supported by the project evidence.>`

## Quick start

### Prerequisites

- `<runtime/tool/access requirement>`

### Install and configure

```text
<exact install/configuration commands or pointers>
```

Required configuration:

| Variable / file | Required | Secret | Purpose |
| --- | --- | --- | --- |
| `<name>` | `<yes/no>` | `<yes/no>` | `<purpose>` |

### Run

| Surface | Command / entrypoint | Expected result |
| --- | --- | --- |
| `<surface>` | `<exact command or location>` | `<observable result>` |

## Development and validation

Run commands from the repository root unless stated otherwise.

| Check | Command | Expected result |
| --- | --- | --- |
| Lint | `<command or unavailable>` | `<signal>` |
| Typecheck | `<command or unavailable>` | `<signal>` |
| Test | `<command or unavailable>` | `<signal>` |
| Build | `<command or unavailable>` | `<signal>` |
| Smoke / qualification | `<command or unavailable>` | `<signal>` |

Last verified baseline:

| Field | Value |
| --- | --- |
| Verified at | `<YYYY-MM-DDTHH:MM:SSZ or unknown>` |
| Commit | `<sha or unknown>` |
| Environment | `<environment/profile or unknown>` |
| Result | `<summary>` |

## Project structure

```text
<repo-root>/
  <path>/    <human explanation>
  <path>/    <human explanation>
```

## Deployment / operation

`<Where and how the project runs, including only project-relevant surfaces, storage, services, and operator entrypoints.>`

## Troubleshooting

| Problem | Likely cause | Next check / action |
| --- | --- | --- |
| `<symptom>` | `<cause>` | `<command/action>` |

## Known limitations and open questions

- `<limitation or open question>`

Do not describe planned or unresolved work as implemented behavior.

## Documentation

Include applicable installed locations only; templates in the framework distribution are not materialized repository facts.

| Document | Purpose |
| --- | --- |
| `README.md` | Human-facing project overview and operation |
| `AGENTS.md` | Entry point to Agentic Work integration |
| `.agentic-work/AGENTS.md` | Bootstrap, repository-context contract, installation/update boundaries |
| `.agentic-work/.work-models/AGENTS.md` | Workflow contracts and routing entry |
| `.agentic-work/.execution-models/AGENTS.md` | Compatible execution allocation |
| `.agentic-work/.spec-set/AGENTS.md` | Artifact integration when Spec Set is selected |
| `.agentic-work/REPO_INFO.yaml` | Repository-owned fact index, when materialized |
| `.agentic-work/runtime.yaml` | Optional observed runtime facts |
| `.agentic-work/structure.yaml` | Optional observed path and ownership facts |
| `.agentic-work/validation.yaml` | Optional validation commands and evidence |
| `.agentic-work/hazards.yaml` | Optional hazard evidence |
| `docs/` | Accepted project specifications where present |

Workflow, execution, and artifact requirements are defined by their applicable owners, not by this README. Shared engineering guidance is supplied externally through the project's designated settings or sources. Installation/update behavior is defined by `.agentic-work/AGENTS.md`.
