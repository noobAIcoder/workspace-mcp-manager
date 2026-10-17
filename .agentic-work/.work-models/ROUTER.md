# Router

## Routing Coordination

For each user instruction, obtain the decision from [PRE_ROUTER.md](PRE_ROUTER.md). `KEEP` preserves the active route; `ROUTE` performs selection below.

A material condition discovered during execution that may change applicable modules MUST trigger selection directly. A stage transition alone does not change the requested operation.

## Selection

Use the current outcome, applicable authority, active route, and known repository/runtime facts. Apply the [manifest's applicability and composition rules](WORK_MODELS.md#applicability-and-composition), then evaluate registered independent modules using their own applicability contracts.

Compute required composition closure before returning the route. Preserve selections only while applicable. Routing MUST NOT depend on keywords alone or broaden authorization.

If materially incompatible routes remain plausible, record the unresolved ambiguity rather than selecting arbitrarily.

## Output

Return only:

```text
operation: <key | none>
frameworks: [<keys>]
methodologies: [<keys>]
execution_profiles: [<keys>]
flows: [<keys>]
routing_ambiguity: <none | concise ambiguity>
```
