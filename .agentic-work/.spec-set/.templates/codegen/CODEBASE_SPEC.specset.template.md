# CS || Codebase specification

<!-- specset-authoring:start
{
  "schema_version": 1,
  "document_key": "CS",
  "purpose": "Map Technical Spec and applicable UI Spec contracts to concrete project-authored paths, modules, interfaces, dependency edges, trace targets, and test locations.",
  "authoritative_inputs": [
    "docs/TECHNICAL_SPEC.{{project_slug}}.md",
    "docs/UI_SPEC.{{project_slug}}.md"
  ],
  "non_goals": [
    "technical architecture or stack selection",
    "UI behavior or visual design ownership",
    "slice sequencing and composition",
    "assuming conventional src/tests/backend/frontend directory or process names"
  ],
  "readiness": [
    {"rule": "required_sections", "sections": ["CS-LAY", "CS-MOD", "CS-INT", "CS-DEP", "CS-TRC", "CS-TST"]},
    {
      "rule": "required_requirement_groups",
      "sections": ["CS-LAY-LAYRG", "CS-MOD-MODRG", "CS-INT-INTRG", "CS-DEP-DEPRG", "CS-TST-TSTRG"],
      "min_rows": 1
    },
    {
      "rule": "dependency_registry_mode",
      "mode": "allowlist",
      "module_context": "CS-MOD-MODRT",
      "module_key_field": "MODULE_KEY",
      "forbidden_module_fields": ["DEPENDS_ON_MODULES"],
      "edge_context": "CS-DEP-DEPRT",
      "edge_from_field": "FROM_MODULE",
      "edge_to_field": "TO_MODULE",
      "edge_rule_field": "EDGE_RULE",
      "allow_value": "ALLOW",
      "deny_value": "DENY",
      "unlisted_cross_module_edges": "denied",
      "self_edges": "implicit",
      "explicit_self_allow": "allowed_redundant",
      "explicit_self_deny": "forbidden"
    }
  ],
  "applicability": [
    {"section": "CS-GLY", "mode": "optional_omittable"}
  ],
  "typed_fields": [
    {"context": "CS-LAY-DIRRT", "field": "PATH_ROOT", "kind": "scalar", "namespace": "path_glob", "empty": "forbidden"},
    {"context": "CS-MOD-MODRT", "field": "OWNED_PATHS", "kind": "list", "namespace": "path_glob", "empty": "forbidden"},
    {"context": "CS-INT-IFRT", "field": "INTERFACE_KEY", "kind": "scalar", "namespace": "interface_key", "empty": "forbidden"},
    {"context": "CS-INT-IFRT", "field": "DEFINITION_PATHS", "kind": "list", "namespace": "path_glob", "empty": "forbidden"},
    {"context": "CS-INT-IFRT", "field": "OWNED_BY_MODULE", "kind": "scalar", "namespace": "module_key", "empty": "forbidden"},
    {"context": "CS-INT-IFRT", "field": "IMPLEMENTED_BY_MODULES", "kind": "list", "namespace": "module_key", "empty": "allowed"},
    {"context": "CS-DEP-DEPRT", "field": "FROM_MODULE", "kind": "scalar", "namespace": "module_key", "empty": "forbidden"},
    {"context": "CS-DEP-DEPRT", "field": "TO_MODULE", "kind": "scalar", "namespace": "module_key", "empty": "forbidden"},
    {"context": "CS-TRC-TRCRT", "field": "SOURCE_REQUIREMENT_ID", "kind": "scalar", "namespace": "requirement_id", "empty": "forbidden"},
    {"context": "CS-TST-TSTRT", "field": "PATH_GLOB", "kind": "scalar", "namespace": "path_glob", "empty": "forbidden"}
  ],
  "typed_discriminators": [
    {
      "context": "CS-TRC-TRCRT",
      "selector_field": "TARGET_KIND",
      "value_field": "TARGET_KEY",
      "mapping": {"module": "module_key", "interface": "interface_key", "dir_root": "directory_key"}
    }
  ],
  "change_areas": ["path", "module", "interface", "dependency", "trace", "test", "layout"],
  "materialization": {
    "strip_authoring_region": true,
    "strip_authoring_comments": true,
    "template_placeholders_allowed": true,
    "materialized_placeholders_allowed": false
  },
  "semantic_review": [
    "Every in-scope source path has one module owner, every Codebase Spec interface key reuses a Technical Spec interface, every interface has one module owner, and cross-module dependency edges are declared only in the dependency allowlist.",
    "Each applicable Technical Spec and UI Spec requirement maps to a concrete codebase target without inventing upstream behavior.",
    "Directory, module, interface, dependency, trace, and test detail is project-authored and concrete enough to constrain implementation and slicing without assuming conventional directory names.",
    "When UI is not applicable, Codebase Spec does not fabricate UI implementation targets."
  ]
}
specset-authoring:end -->

## CS-LNK || Record document mirrors

| FIELD | VALUE |
| --- | --- |
| project_slug | {{project_slug}} |
| document_key | CS |
| document_role | normative_derived |
| source_docs | (none) |
| upstream_docs | docs/TECHNICAL_SPEC.{{project_slug}}.md,docs/UI_SPEC.{{project_slug}}.md |
| downstream_docs | docs/SLICES_MAP.{{project_slug}}.md,docs/slices/SLICE_<NNN>.{{project_slug}}.md |

## CS-OVR || Define codebase purpose

{{codebase_context}}

## CS-LAY || Define directory roots

### CS-LAY-DIRRT || Directory registry

| DIRECTORY_KEY | PATH_ROOT | ROLE |
| --- | --- | --- |
| DIR_TBD | {{path_root}} | {{role}} |

### CS-LAY-LAYRG || Layout requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-LAY-LAYRG-LAYO_TBD | The codebase MUST place {{path_owned_concern}} under the declared directory root. | directory_key:DIR_TBD | TS-ARC-ARCRG-ARCH_TBD |

## CS-MOD || Define modules

### CS-MOD-MODRT || Module registry

| MODULE_KEY | OWNED_PATHS | PUBLIC_ENTRYPOINTS | LAYER | RESPONSIBILITY |
| --- | --- | --- | --- | --- |
| MOD_TBD | {{owned_globs}} | {{entrypoints_or_none}} | {{layer}} | {{single_responsibility}} |

### CS-MOD-MODRG || Module requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-MOD-MODRG-MODL_TBD | The codebase MUST isolate {{module_responsibility}} in the declared module. | module_key:MOD_TBD | TS-ARC-ARCRG-ARCH_TBD |

## CS-INT || Define interfaces

### CS-INT-IFRT || Interface registry

| INTERFACE_KEY | INTERFACE_KIND | DEFINITION_PATHS | OWNED_BY_MODULE | IMPLEMENTED_BY_MODULES | NOTES |
| --- | --- | --- | --- | --- | --- |
| IFACE_TBD | {{interface_kind}} | {{definition_paths}} | MOD_TBD | MOD_TBD | {{notes}} |

### CS-INT-INTRG || Interface requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-INT-INTRG-IFCE_TBD | The codebase MUST define {{interface_boundary}} at the declared definition path. | interface_key:IFACE_TBD | TS-IFC-IFCRG-IFCE_TBD |

## CS-DEP || Define dependency policy

### CS-DEP-DEPRT || Dependency allowlist registry

| FROM_MODULE | TO_MODULE | EDGE_RULE | RATIONALE |
| --- | --- | --- | --- |
| {{from_module}} | {{to_module}} | {{allow_or_deny}} | {{dependency_rationale}} |

Unlisted cross-module edges are denied. Self-dependency inside one module is implicit. A self-edge MAY be omitted or recorded as `ALLOW`; explicit self-`ALLOW` is redundant but valid. A self-`DENY` contradicts implicit self-dependency and is invalid. For any ordered module pair, duplicate declarations are invalid and `DENY` MUST NOT coexist with `ALLOW`.

### CS-DEP-DEPRG || Dependency requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-DEP-DEPRG-DEPS_TBD | The codebase MUST permit only declared cross-module dependency edges. | enforcement:dependency_allowlist | TS-ARC-ARCRG-ARCH_TBD |

## CS-TRC || Map upstream requirements

### CS-TRC-TRCRT || Trace registry

| SOURCE_REQUIREMENT_ID | TARGET_KIND | TARGET_KEY | COVERAGE_NOTE |
| --- | --- | --- | --- |
| TS-ARC-ARCRG-ARCH_TBD | module | MOD_TBD | {{coverage_note}} |
| TS-IFC-IFCRG-IFCE_TBD | interface | IFACE_TBD | {{coverage_note}} |

## CS-TST || Define test locations

### CS-TST-TSTRT || Test location registry

| TEST_DIRECTORY_KEY | PATH_GLOB | PURPOSE |
| --- | --- | --- |
| TST_TBD | {{test_path_glob}} | {{test_purpose}} |

### CS-TST-TSTRG || Test layout requirements

| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |
| --- | --- | --- | --- |
| CS-TST-TSTRG-TEST_TBD | The codebase MUST organize {{test_layout_contract}} at the declared test location. | test_directory_key:TST_TBD | TS-TST-TSTRG-TEST_TBD |

## CS-GLY || Maintain glossary

| TERM | DEFINITION |
| --- | --- |
| {{term}} | {{definition}} |
