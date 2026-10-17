# Authorized Dependencies

**Status:** Template v0.1

## Purpose

This document is the human-reviewed authority record for intentional direct dependency decisions.

Each consuming project SHOULD maintain its own project-local `docs/AUTHORIZED_DEPENDENCIES.md`. The copy in this repository defines the record shape; it does not authorize dependencies in other projects.

The package-manager lockfile remains the machine authority for the resolved dependency graph. This document records operator-reviewed dependency intent and trust boundaries.

## Policy owner

Dependency-change triggers, preparation, proposal state, approval scope, operator gating, re-entry, and integrity obligations are owned by Dependency Change Flow at `.agentic-work/.work-models/flows/DEPENDENCIES.md`. This template records decisions under that flow and MUST NOT redefine its workflow policy.

This locator is repository-root-relative in the consuming installation, including when this record is copied to `docs/AUTHORIZED_DEPENDENCIES.md`.

## Entries

No project dependencies are authorized by this template.

## Entry Template

### `<dependency-or-component>`

- **Status:** `Proposed` | `Approved` | `Rejected`
- **Ecosystem / type:**
- **Canonical package / source:**
- **Repository:**
- **Requested or approved version / change:**
- **Purpose / why required:**
- **Implementation notes:**
- **Material trust / provenance considerations:**
- **Approval reference / date:**
