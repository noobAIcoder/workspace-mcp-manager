# Pre-Router

**Status:** Draft v0.1

## Purpose

Decide whether the current user instruction can continue under the active route or requires full routing.

## Inputs

Use only:

- the current user instruction;
- the active route, if any;
- already-known context material to route selection.

## Decision

Return `ROUTE` when any of the following is true:

- no active route exists;
- the requested work operation may have materially changed;
- applicability of a framework, methodology profile, execution profile, or conditional flow may have materially changed;
- the current instruction supersedes or invalidates part of the active route;
- the active route is materially uncertain under the new instruction.

Otherwise return `KEEP`.

## Output

Return exactly one routing decision:

```text
KEEP
```

or:

```text
ROUTE
```
