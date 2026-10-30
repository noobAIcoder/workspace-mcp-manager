try:
    from ._specset_readiness import *
except ImportError:
    from _specset_readiness import *


_base_validate_readiness = validate_readiness


def _validate_slice_dependency_inputs(
    doc: NormDoc,
    docs: dict[str, list[NormDoc]],
    rule: dict[str, Any],
    issues: list[Issue],
) -> None:
    input_context = rule.get("input_context")
    slice_key_field = rule.get("slice_key_field")
    dependencies_field = rule.get("dependencies_field")
    map_document_key = rule.get("map_document_key")
    edge_context = rule.get("edge_context")
    edge_from_field = rule.get("edge_from_field")
    edge_to_field = rule.get("edge_to_field")
    values = (
        input_context,
        slice_key_field,
        dependencies_field,
        map_document_key,
        edge_context,
        edge_from_field,
        edge_to_field,
    )
    if not all(isinstance(value, str) and value for value in values):
        issues.append(
            Issue(
                "READINESS_SLICE_DEPENDENCY",
                str(doc.path),
                0,
                "slice_dependency_inputs readiness metadata is malformed",
            )
        )
        return

    slice_values = _field_values(doc, input_context, slice_key_field)
    dependency_values = _field_values(doc, input_context, dependencies_field)
    if len(slice_values) != 1 or len(dependency_values) != 1:
        issues.append(
            Issue(
                "SLICE_DEPENDENCY_INPUT",
                str(doc.path),
                0,
                "slice input registry must contain exactly one slice_key and dependencies field",
            )
        )
        return

    slice_key, slice_line = slice_values[0]
    raw_dependencies, dependency_line = dependency_values[0]
    if doc.mode == "template" and (
        PLACEHOLDER.search(slice_key) or PLACEHOLDER.search(raw_dependencies)
    ):
        return

    expected: list[str] = []
    for map_doc in docs.get(map_document_key, []):
        for table in _tables_in_section(map_doc, edge_context):
            if edge_from_field not in table.headers or edge_to_field not in table.headers:
                continue
            from_idx = table.headers.index(edge_from_field)
            to_idx = table.headers.index(edge_to_field)
            for row in table.rows:
                if max(from_idx, to_idx) >= len(row):
                    continue
                if row[from_idx] == slice_key:
                    expected.append(row[to_idx])

    actual = ref_values(raw_dependencies)
    if actual != expected:
        issues.append(
            Issue(
                "SLICE_DEPENDENCY_MIRROR",
                str(doc.path),
                dependency_line or slice_line,
                f"dependencies differs from Slices Map edge registry; expected={expected}, actual={actual}",
            )
        )


def _validate_dependency_allowlist(doc: NormDoc, rule: dict[str, Any], issues: list[Issue]) -> None:
    if rule.get("mode") != "allowlist":
        return
    module_context = rule.get("module_context")
    edge_context = rule.get("edge_context")
    module_key_field = rule.get("module_key_field")
    edge_from_field = rule.get("edge_from_field")
    edge_to_field = rule.get("edge_to_field")
    edge_rule_field = rule.get("edge_rule_field")
    allow_value = rule.get("allow_value")
    deny_value = rule.get("deny_value")
    values = (
        module_context,
        edge_context,
        module_key_field,
        edge_from_field,
        edge_to_field,
        edge_rule_field,
        allow_value,
        deny_value,
    )
    if not all(isinstance(value, str) and value for value in values):
        issues.append(Issue("READINESS_DEPENDENCY", str(doc.path), 0, "dependency allowlist readiness metadata is malformed"))
        return
    module_table = next((table for table in _tables_in_section(doc, module_context) if module_key_field in table.headers), None)
    edge_table = next((table for table in _tables_in_section(doc, edge_context) if {edge_from_field, edge_to_field, edge_rule_field}.issubset(table.headers)), None)
    if not module_table:
        issues.append(Issue("READINESS_DEPENDENCY", str(doc.path), 0, f"missing module registry {module_context}"))
        return
    if not edge_table:
        issues.append(Issue("READINESS_DEPENDENCY", str(doc.path), 0, f"missing dependency registry {edge_context}"))
        return
    for forbidden in rule.get("forbidden_module_fields", []):
        if forbidden in module_table.headers:
            issues.append(Issue("DEPENDENCY_DUPLICATE_AUTHORITY", str(doc.path), module_table.line, f"module registry must not contain dependency mirror field {forbidden}"))
    mi = module_table.headers.index(module_key_field)
    modules = {row[mi] for row in module_table.rows if mi < len(row)}
    fi, ti, ri = edge_table.headers.index(edge_from_field), edge_table.headers.index(edge_to_field), edge_table.headers.index(edge_rule_field)
    seen: dict[tuple[str, str], str] = {}
    for n, row in enumerate(edge_table.rows, 2):
        if max(fi, ti, ri) >= len(row):
            continue
        source, target, edge_rule = row[fi], row[ti], row[ri]
        if doc.mode == "template" and any(PLACEHOLDER.search(value) for value in (source, target, edge_rule)):
            continue
        known_modules = source in modules and target in modules
        if not known_modules:
            issues.append(Issue("DEPENDENCY_MODULE", str(doc.path), edge_table.line + n, f"dependency edge {source}->{target} references unknown module"))
        if edge_rule not in {allow_value, deny_value}:
            issues.append(Issue("DEPENDENCY_EDGE_RULE", str(doc.path), edge_table.line + n, f"unsupported EDGE_RULE {edge_rule!r}"))
        if known_modules and rule.get("self_edges") == "implicit" and source == target and edge_rule == deny_value:
            issues.append(Issue("DEPENDENCY_SELF_DENY", str(doc.path), edge_table.line + n, f"self edge {source}->{target} cannot be denied because self-dependency is implicit"))
        pair = (source, target)
        if pair in seen:
            code = "DEPENDENCY_EDGE_CONFLICT" if seen[pair] != edge_rule else "DEPENDENCY_EDGE_DUP"
            issues.append(Issue(code, str(doc.path), edge_table.line + n, f"dependency pair {source}->{target} is declared more than once"))
        else:
            seen[pair] = edge_rule


def _validate_field_ownership(doc: NormDoc, docs: dict[str, list[NormDoc]], rule: dict[str, Any], issues: list[Issue]) -> None:
    if doc.mode == "template":
        return
    scope_context = rule.get("scope_context")
    scope_field = rule.get("scope_field")
    path_field = rule.get("path_field")
    owner_key = rule.get("owner_document_key")
    owner_context = rule.get("owner_context")
    owner_key_field = rule.get("owner_key_field")
    owner_path_field = rule.get("owner_path_field")
    values = (scope_context, scope_field, path_field, owner_key, owner_context, owner_key_field, owner_path_field)
    if not all(isinstance(value, str) and value for value in values):
        issues.append(Issue("READINESS_FIELD_OWNERSHIP", str(doc.path), 0, "field_ownership readiness metadata is malformed"))
        return
    owner_paths: dict[str, list[str]] = defaultdict(list)
    for owner_doc in docs.get(owner_key, []):
        for table in _tables_in_section(owner_doc, owner_context):
            if owner_key_field not in table.headers or owner_path_field not in table.headers:
                continue
            ki, pi = table.headers.index(owner_key_field), table.headers.index(owner_path_field)
            for row in table.rows:
                if max(ki, pi) < len(row):
                    owner_paths[row[ki]].extend(ref_values(row[pi]))
    scopes = [value for raw, _ in _field_values(doc, scope_context, scope_field) for value in ref_values(raw)]
    paths = [value for raw, _ in _field_values(doc, scope_context, path_field) for value in ref_values(raw)]
    allowed = [path for scope in scopes for path in owner_paths.get(scope, [])]
    for touched in paths:
        status, detail = glob_subset(touched, allowed)
        if status == "contained":
            continue
        code = {
            "invalid": "SLICE_FILE_OWNER_GLOB_INVALID",
            "unsupported": "SLICE_FILE_OWNER_GLOB_UNSUPPORTED",
            "unestablished": "SLICE_FILE_OWNER_UNESTABLISHED",
            "not_contained": "SLICE_FILE_OWNER",
        }.get(status, "SLICE_FILE_OWNER_UNESTABLISHED")
        issues.append(Issue(code, str(doc.path), 0, f"touched file/glob {touched} is not established within touched module ownership: {detail}"))


def validate_readiness(
    key: str,
    doc: NormDoc,
    contract: dict[str, Any],
    docs: dict[str, list[NormDoc]],
    contracts: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> None:
    special_kinds = {"slice_dependency_inputs", "dependency_registry_mode", "field_ownership"}
    special_rules = [
        rule
        for rule in contract.get("readiness", [])
        if isinstance(rule, dict) and rule.get("rule") in special_kinds
    ]
    if not special_rules:
        _base_validate_readiness(key, doc, contract, docs, contracts, issues)
        return

    delegated = dict(contract)
    delegated["readiness"] = [
        rule
        for rule in contract.get("readiness", [])
        if not (isinstance(rule, dict) and rule.get("rule") in special_kinds)
    ]
    _base_validate_readiness(key, doc, delegated, docs, contracts, issues)
    for rule in special_rules:
        kind = rule.get("rule")
        if kind == "slice_dependency_inputs":
            _validate_slice_dependency_inputs(doc, docs, rule, issues)
        elif kind == "dependency_registry_mode":
            _validate_dependency_allowlist(doc, rule, issues)
        elif kind == "field_ownership":
            _validate_field_ownership(doc, docs, rule, issues)


__all__ = [name for name in globals() if not name.startswith("__")]
