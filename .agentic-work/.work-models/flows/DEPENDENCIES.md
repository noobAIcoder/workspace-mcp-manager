# Dependency Change Flow

**Status:** Draft v0.1

## Purpose

Provide a deterministic operator gate for material dependency changes while allowing normal use of the existing approved dependency graph.

## Trigger

Invoke this flow whenever intended work requires or proposes a material dependency change, including:

- adding, removing, upgrading, downgrading, or replacing a direct dependency;
- changing a dependency source, registry, repository, or equivalent provenance boundary;
- introducing a new third-party installer, executable, runtime component, or other code-execution dependency;
- materially changing an existing dependency's trust assumptions.

## Non-Triggers

This flow is not required merely to:

- restore, install, or use the project's existing authoritative dependency graph;
- perform package-manager metadata normalization that does not change dependency intent or material resolution;
- inspect dependency metadata, documentation, advisories, or provenance without mutation or execution.

Unexpected integrity, provenance, or source changes discovered during those operations are blockers under this flow's integrity rules.

## Required Preparation

Before changing dependency manifests, lockfiles, configured sources, or executing newly proposed third-party code:

1. Inspect the existing dependency manifests, lockfiles, configured sources, and project-local `docs/AUTHORIZED_DEPENDENCIES.md` when present.
2. Plan the complete set of direct dependency changes required by the current task where reasonably knowable, to avoid repeated approval gates.
3. For each proposed change, establish the dependency or component, canonical source or repository, intended version or change, why it is required, material implementation notes, and material trust or provenance considerations.
4. Create or update the target project's `docs/AUTHORIZED_DEPENDENCIES.md` with each unapproved change recorded as `Proposed` while preserving any still-applicable approved decision separately.
5. Complete other safe read-only analysis needed to make the proposal concrete and reviewable.

`Proposed` denotes pending, unapproved dependency intent. Creating a proposal to alter an already approved dependency MUST NOT reset, replace, or weaken the currently applicable `Approved` scope before the proposal is decided.

Transitive dependencies SHOULD remain represented by the package-manager lockfile and SHOULD NOT be duplicated manually unless a transitive component itself creates a material exception requiring operator review.

## Operator Gate

After required preparation, affected work MUST halt for operator review.

Before approval, the proposed dependency change MUST NOT be applied to manifests, lockfiles, configured sources, or newly executed third-party code.

## Approval Scope

Approval authorizes only the dependency changes and trust assumptions documented in the reviewed proposal.

After approval, the newly approved scope SHOULD be recorded with its decision or supersession reference and the originating operation MUST resume without repeating completed preparation.

Rejecting a proposed change MUST NOT revoke a previously applicable approval unless revocation is itself explicitly decided.

A material deviation from the approved dependency set, source, version intent, purpose, or trust assumptions MUST re-enter this flow.

## Integrity Rules

Approved and existing dependencies MUST be consumed through the project's authoritative manifests, lockfiles, configured sources, and normal verification controls.

Integrity, signature, provenance, source, or equivalent trust controls MUST NOT be bypassed merely to make work succeed.

Credible evidence of compromise, unexpected provenance or source substitution, or integrity verification failure MUST block affected execution and MUST be reported for operator review unless a safe already-authorized alternative exists.

Hashes, lockfiles, dependency sources, or verification metadata MUST NOT be rewritten solely to suppress a verification failure.

## Exit Conditions

- **Approved:** record approval as appropriate and resume the originating operation.
- **Rejected:** do not apply the dependency change; resume only work that remains valid without it.
- **Blocked:** preserve completed preparation and report the unresolved dependency risk or missing decision precisely.
