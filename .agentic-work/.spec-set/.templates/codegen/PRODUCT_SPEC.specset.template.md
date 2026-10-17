# PS || Product specification

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "PS",
  "purpose": "Define normative product outcomes and project constraints that downstream specifications implement.",
  "authoritative_inputs": [
    "docs/PRODUCT_BRIEF.{{project_slug}}.md as non-normative source provenance"
  ],
  "non_goals": [
    "domain data and persistence design",
    "technical architecture and stack selection",
    "UI composition",
    "codebase layout and slice composition"
  ],
  "readiness": [
    {"rule": "required_sections", "sections": ["PS-OVR", "PS-USR", "PS-JRN", "PS-RUL", "PS-CON", "PS-ACS"]},
    {
      "rule": "required_requirement_groups",
      "sections": ["PS-JRN-JRNRG", "PS-RUL-RULRG", "PS-CON-CONRG", "PS-ACS-ACSRG"],
      "min_rows": 1
    },
    {"rule": "ancestry_mode", "value": "origin"}
  ],
  "applicability": [
    {"section": "PS-GLY", "mode": "optional_omittable"}
  ],
  "typed_fields": [],
  "change_areas": ["journey", "rule", "constraint", "acceptance"],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "semantic_review": [
    "The Product Spec is independently interpretable without requiring the Product Brief at implementation time.",
    "Target users, operating context, user journeys, rules, constraints, and acceptance outcomes are concrete and mutually coherent.",
    "Product Brief content is used only as source evidence and is not represented as normative ancestry.",
    "The document does not invent downstream domain, technical, UI, codebase, or slicing detail that is not needed to constrain the product."
  ]
}
specset-authoring:end -->

## PS-LNK || Record document mirrors

| FIELD | VALUE |
| --- | --- |
| project_slug | {{project_slug}} |
| document_key | PS |
| document_role | normative_origin |
| source_docs | docs/PRODUCT_BRIEF.{{project_slug}}.md |
| upstream_docs | (none) |
| downstream_docs | docs/DOMAIN_SPEC.{{project_slug}}.md |

## PS-OVR || Define product requirements context

{{product_requirements_context}}

## PS-USR || Identify target users

<!-- specset-authoring:guidance Describe the primary user, operating context, current workaround, and needed outcome. -->
{{target_users}}

## PS-JRN || Define user journeys

<!-- specset-authoring:guidance State each journey's trigger, initial state, ordered steps, completed state, and principal recovery path. -->
{{journey_definitions}}

### PS-JRN-JRNRG || Journey requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| PS-JRN-JRNRG-TBD_JOURNEY | The product MUST support {{journey_outcome}}. | status:template_placeholder | (origin) |

## PS-RUL || Define product rules

<!-- specset-authoring:guidance State testable validation, business-logic, and process constraints with boundary behavior. -->
{{product_rules}}

### PS-RUL-RULRG || Rule requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| PS-RUL-RULRG-TBD_RULE | The product MUST enforce {{product_rule}}. | status:template_placeholder | (origin) |

## PS-CON || Define project constraints

<!-- specset-authoring:guidance Record platform, deployment, data, time, integration, legal, and operating constraints that materially bound implementation. -->
{{project_constraints}}

### PS-CON-CONRG || Constraint requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| PS-CON-CONRG-TBD_CONST | The solution MUST satisfy {{project_constraint}}. | status:template_placeholder | (origin) |

## PS-ACS || Define acceptance outcomes

<!-- specset-authoring:guidance Define observable outcomes using concrete preconditions, actions, and results. Cover every in-scope journey and material failure path. -->
{{acceptance_outcomes}}

### PS-ACS-ACSRG || Acceptance requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| PS-ACS-ACSRG-TBD_ACCEPT | The product MUST demonstrate {{acceptance_outcome}}. | status:template_placeholder | (origin) |

## PS-GLY || Maintain glossary

| TERM | DEFINITION |
| --- | --- |
| {{term}} | {{definition}} |
