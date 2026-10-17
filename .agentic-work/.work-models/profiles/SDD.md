# Specification-Driven Development

## Stage Contract

This profile owns specification comparison, `TODO_SDD`, candidate production, validation, acceptance, specification no-op behavior, and SDD completion. It consumes a validated desirable end result, governing requirements, current affected specifications or their absence, and the authorized output boundary.

Apply [Work Instructions](../WORK_INSTRUCTIONS.md). Framework-specific artifact structure and propagation come from the selected specification framework.

## Specification Comparison

Review affected specifications against the desirable end result and governing requirements. Classify each delta as creation, missing requirement, modification, removal, contradiction, or material ambiguity.

When deltas exist, record `TODO_SDD` with, for each delta:

| Field | Required content |
| --- | --- |
| Target | Existing specification path and paragraph/section, or required new authority |
| Change | Required normative addition, revision, removal, or ambiguity resolution |
| Reason | Governing requirement and desirable-end-result discrepancy |
| Validation | Observable condition establishing the corrected specification |
| Acceptance | Applicable mechanism and accepting authority |

The record MUST include SDD completion conditions and affected cross-document consequences. A project service artifact MAY hold this record; it MUST NOT create a second delta authority.

Review the comparison and proposed changes for coverage, coherence, contradiction, redundancy, scope, and authority before editing. Correct material defects, resolve material ambiguities, and re-review conclusions invalidated by revision.

When no delta exists, specifications MUST NOT be modified. Validate their sufficiency and establish applicable acceptance under Acceptance below; missing acceptance requires that process, not an artificial specification edit.

## Candidate Production and Validation

Create or revise only the affected specification content and required governed derivations. Remove or explicitly supersede obsolete authority. Preserve unaffected requirements.

A specification candidate MUST distinguish normative requirements from rationale, examples, and unresolved decisions. Existing implementation is evidence, not a source of normative intent.

Review the candidate against `TODO_SDD`, governing requirements, the desirable end result, and applicable artifact contracts. Correct material defects and repeat affected review. Required validation MUST establish completeness, consistency, verifiability, and closure of affected specification consequences before acceptance.

## Acceptance

Accepted authoritative specifications define intended behavior; tests, implementation, and plans MUST NOT redefine that behavior.

Identify the applicable acceptance rule, decision-maker (the user or project-designated authority), candidate revision and scope, and any expressly required operator approval. A validated candidate becomes accepted only through current user instruction or that project-defined mechanism. Validation alone MUST NOT confer acceptance.

Reuse sufficient existing acceptance when its scope, revision, and governing conditions remain applicable. An instruction to implement an identified specification MAY also establish acceptance when no governing process requires a separate gate.

Artifact acceptance MUST NOT independently authorize implementation; authorization follows the shared guidance designated by Work Instructions → Governing Policy.

If acceptance or a material decision is missing, report that boundary without presenting the candidate as implementation authority.

## Completion

The SDD stage is complete when either the validated candidate has applicable acceptance or existing accepted specifications have been validated as sufficient. Output the accepted authority and its revision, comparison/delta disposition, and validation and acceptance evidence.

A specification-draft-only request MAY finish with a validated candidate and explicit pending acceptance. That completes the requested draft deliverable, not the SDD gate for affected implementation.
