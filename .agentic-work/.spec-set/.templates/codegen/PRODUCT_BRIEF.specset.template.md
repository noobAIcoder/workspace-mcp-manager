# Product Brief

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "PB",
  "purpose": "Capture concise non-normative product intent and source context for Product Spec authoring.",
  "authoritative_inputs": [
    "operator-provided product intent, constraints, and project evidence explicitly supplied for the brief"
  ],
  "non_goals": [
    "normative project requirements",
    "normative ancestry",
    "document-specific implementation design owned by downstream specifications"
  ],
  "readiness": [
    {
      "rule": "heading_profile",
      "h1": "# Product Brief",
      "required_h2_order": [
        "## Product Summary",
        "## Problem",
        "## Primary User and Context",
        "## Intended Outcome",
        "## MVP Scope",
        "## Boundaries"
      ],
      "optional_trailing_h2": [
        "## Assumptions"
      ],
      "nested_levels": [3, 4],
      "nested_under_h2": true,
      "nested_plain_text": true,
      "nested_forbidden_tokens": ["||"]
    },
    {"rule": "sections_populated", "scope": "required_h2_and_present_optional_h2"},
    {"rule": "forbid_specset_heading_syntax"},
    {"rule": "forbid_normative_rfc2119"},
    {"rule": "forbid_requirements_tables"},
    {"rule": "forbid_reserved_requirement_fields"},
    {"rule": "forbid_normative_document_links"},
    {"rule": "forbid_readiness_gates"}
  ],
  "applicability": [
    {"section": "## Assumptions", "mode": "optional_omittable"}
  ],
  "typed_fields": [],
  "change_areas": ["problem", "primary_user", "intended_outcome", "mvp_scope", "boundary", "assumption"],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "semantic_review": [
    "The brief is independently understandable as product intent without becoming a normative specification.",
    "The problem, primary user and operating context, intended outcome, MVP scope, and boundaries are concrete enough to author Product Spec without unsupported invention.",
    "The brief remains outcome-focused and does not introduce implementation, architecture, interface, or slice-planning decisions.",
    "Assumptions are included only when they materially affect interpretation or downstream specification work.",
    "Product Spec remains independently interpretable without requiring the Product Brief at implementation time."
  ]
}
specset-authoring:end -->

## Product Summary

{{product_summary}}

## Problem

{{problem}}

## Primary User and Context

{{primary_user_and_context}}

## Intended Outcome

{{intended_outcome}}

## MVP Scope

{{mvp_scope}}

## Boundaries

{{boundaries}}

## Assumptions

{{assumptions}}
