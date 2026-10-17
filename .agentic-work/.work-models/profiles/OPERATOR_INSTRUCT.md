# Operator-Instruct Execution Profile

**Status:** Draft v0.1

## Applicability

Apply this profile when:

- the user explicitly requests to perform substantive work under model guidance; or
- operator execution becomes the primary available mechanism for the remaining substantive work.

An incidental operator-only action MUST NOT activate this profile by itself.

## Objective

Guide operator execution from known state to the requested outcome using bounded actions, decision-relevant observations, and sufficient evidence.

## State Establishment

Before instructing the operator:

- inspect available repository, runtime, and contextual state where authorized;
- use existing evidence rather than asking the operator to rediscover it;
- complete safe independent preparation;
- determine the next justified operator action from the best available state.

## Interaction Contract

Use this loop:

```text
known state
→ bounded operator action
→ expected observation
→ evidence when decision-relevant
→ continue or replan
```

Instructions MUST be directly actionable and MUST identify environment, path, UI context, or other execution context when ambiguity matters.

Stop before a material branch when its observed result determines subsequent work.

## Batching

Consecutive deterministic, low-risk actions SHOULD be grouped when intermediate results do not affect subsequent actions.

Operator feedback SHOULD NOT be requested after trivial intermediate actions unless it changes the next decision or supplies required evidence.

## Evidence

Operator-reported results MAY be used as execution evidence but MUST NOT be represented as directly model-observed.

Unexpected results MUST cause reassessment rather than blind continuation of the original procedure.

## Composition

Operations, methodology profiles, enclosed frameworks, and conditional flows remain applicable while this execution profile is active.

When a conditional flow gates affected work, follow that flow and then resume operator-guided execution if the active route remains applicable.

## Completion

Providing instructions alone MUST NOT constitute completion when the requested outcome depends on operator execution.

Completion MUST be supported by sufficient returned evidence or confirmation under the completion policy referenced by [Work Instructions](../WORK_INSTRUCTIONS.md).
