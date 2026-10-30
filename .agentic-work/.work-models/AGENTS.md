# Work Models Entry Point

Paths and Markdown links in this file are component-relative.

## Common contracts

Load [WORK_MODELS.md](WORK_MODELS.md) and [WORK_INSTRUCTIONS.md](WORK_INSTRUCTIONS.md). Follow Work Instructions' baseline and designated external engineering-guidance dependencies and its missing-authority handling.

Common-contract loading MUST remain separate from routing. Return to the integration bootstrap to load the repository fact index and relevant shards before resolving the route. Registry entries are navigation, not blanket module activation.

## Routing entry

After repository-context loading, use [ROUTER.md](ROUTER.md), which coordinates [PRE_ROUTER.md](PRE_ROUTER.md). Use those owners for route retention, rerouting, composition closure, routing ambiguity, and route output. This entry point MUST NOT add another routing decision or redefine their semantics.

## Applicability inspection

Use the [manifest registry and applicability contract](WORK_MODELS.md#applicability-and-composition) to locate applicable operations and independent modules. Inspect their registered applicability contracts as needed. Inspection MUST NOT itself activate a module or its downstream dependencies.

## Selected dependencies

After route resolution, load the selected operation, methodologies, execution profiles, conditional and lifecycle flows, and framework integrations, together with the composition required by their owners. Load a routed framework's component contract through its selected integration, not merely because its directory exists.

Workflow stages, gates, acceptance, and completion remain defined by those owners; this entry point MUST NOT restate them.

## Route result

Make the route and its resolved obligations and constraints available to the enclosing integration. Unresolved ambiguity remains visible under the Router's handling.

Execution topology is outside Work Model routing. This entry point MUST NOT discover, choose, load, or substitute topology based on repository contents or available capabilities.
