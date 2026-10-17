try:
    from ._specset_refs import *
except ImportError:
    from _specset_refs import *


def _requirement_count(doc: NormDoc, section: str) -> int:
    return sum(len(table.rows) for table in _tables_in_section(doc, section) if table.headers == REQ_HEADERS)


def _document_not_applicable(docs: dict[str, list[NormDoc]], contracts: dict[str, dict[str, Any]], document_key: str) -> bool:
    target_docs = docs.get(document_key, [])
    contract = contracts.get(document_key, {})
    branch = next((rule for rule in contract.get("readiness", []) if isinstance(rule, dict) and rule.get("rule") == "applicability_branch"), None)
    if not branch:
        return False
    selector = branch.get("selector", {}).get("not_applicable_requirement_group")
    return bool(selector and any(doc.mode == "materialized" and selector in {heading.sid for heading in doc.headings} for doc in target_docs))


def _coverage_membership(doc: NormDoc, spec: dict[str, Any]) -> dict[str, set[str]]:
    context = spec.get("context")
    key_field = spec.get("key_field")
    slice_field = spec.get("slice_field")
    result: dict[str, set[str]] = defaultdict(set)
    if not all(isinstance(value, str) for value in (context, key_field, slice_field)):
        return result
    for table in _tables_in_section(doc, context):
        if key_field not in table.headers or slice_field not in table.headers:
            continue
        ki, si = table.headers.index(key_field), table.headers.index(slice_field)
        for row in table.rows:
            if max(ki, si) >= len(row):
                continue
            key = row[ki]
            if key == "(none)" or (doc.mode == "template" and PLACEHOLDER.search(key)):
                continue
            for slice_key in ref_values(row[si]):
                if doc.mode == "template" and PLACEHOLDER.search(slice_key):
                    continue
                result[key].add(slice_key)
    return result


def _catalog_membership(doc: NormDoc, catalog: MdTable, slice_field: str, member_field: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    if slice_field not in catalog.headers or member_field not in catalog.headers:
        return result
    si, mi = catalog.headers.index(slice_field), catalog.headers.index(member_field)
    for row in catalog.rows:
        if max(si, mi) >= len(row):
            continue
        slice_key = row[si]
        if doc.mode == "template" and PLACEHOLDER.search(slice_key):
            continue
        for member in ref_values(row[mi]):
            if member == "(none)" or (doc.mode == "template" and PLACEHOLDER.search(member)):
                continue
            result[member].add(slice_key)
    return result


def _compare_coverage(doc: NormDoc, label: str, expected: dict[str, set[str]], actual: dict[str, set[str]], issues: list[Issue]) -> None:
    if expected != actual:
        pretty_expected = {key: sorted(value) for key, value in sorted(expected.items())}
        pretty_actual = {key: sorted(value) for key, value in sorted(actual.items())}
        issues.append(Issue("SLICE_COVERAGE_MIRROR", str(doc.path), 0, f"{label} coverage differs from slice catalog; expected={pretty_expected}, actual={pretty_actual}"))


def _validate_slice_catalog(doc: NormDoc, docs: dict[str, list[NormDoc]], contracts: dict[str, dict[str, Any]], rule: dict[str, Any], issues: list[Issue]) -> None:
    context = rule.get("catalog_context")
    tables = _tables_in_section(doc, context) if isinstance(context, str) else []
    if not tables:
        issues.append(Issue("READINESS_SM", str(doc.path), 0, f"missing slice catalog {context}"))
        return
    catalog = tables[0]
    fields = rule.get("catalog_fields", {})
    roles = rule.get("required_catalog_roles", [])
    if not isinstance(fields, dict) or not isinstance(roles, list) or not roles:
        issues.append(Issue("READINESS_SM", str(doc.path), catalog.line, "slice catalog readiness metadata is incomplete"))
        return
    if any(role not in fields or fields[role] not in catalog.headers for role in roles):
        issues.append(Issue("READINESS_SM", str(doc.path), catalog.line, "slice catalog lacks declared readiness fields"))
        return

    for forbidden in rule.get("forbidden_catalog_fields", []):
        if forbidden in catalog.headers:
            issues.append(Issue("SLICE_DEPENDENCY_DUPLICATE", str(doc.path), catalog.line, f"slice catalog must not contain deprecated mirror field {forbidden}"))

    slice_field = fields["slice_key"]
    order_field = fields["order_index"]
    slice_idx = catalog.headers.index(slice_field)
    order_idx = catalog.headers.index(order_field)
    slices: set[str] = set()
    orders: dict[str, int] = {}
    used_orders: dict[int, str] = {}
    for n, row in enumerate(catalog.rows, 2):
        if max(slice_idx, order_idx) >= len(row):
            continue
        slice_key = row[slice_idx]
        if doc.mode == "template" and PLACEHOLDER.search(slice_key):
            continue
        if slice_key in slices:
            issues.append(Issue("SLICE_KEY_DUP", str(doc.path), catalog.line + n, f"duplicate slice key {slice_key}"))
        slices.add(slice_key)
        try:
            order = int(row[order_idx])
            if order < 1:
                raise ValueError
        except ValueError:
            issues.append(Issue("SLICE_ORDER", str(doc.path), catalog.line + n, f"invalid ORDER_INDEX {row[order_idx]!r}"))
            continue
        if order in used_orders and used_orders[order] != slice_key:
            issues.append(Issue("SLICE_ORDER_DUP", str(doc.path), catalog.line + n, f"ORDER_INDEX {order} is shared by {used_orders[order]} and {slice_key}"))
        used_orders[order] = slice_key
        orders[slice_key] = order

    coverage = rule.get("coverage", {}) if isinstance(rule.get("coverage"), dict) else {}
    acceptance_expected = _catalog_membership(doc, catalog, slice_field, fields["acceptance_anchors"])
    guard_expected = _catalog_membership(doc, catalog, slice_field, fields["guardrails"])
    acceptance_actual = _coverage_membership(doc, coverage.get("acceptance", {}))
    guard_actual = _coverage_membership(doc, coverage.get("guardrail", {}))
    if rule.get("requires_acceptance_coverage"):
        _compare_coverage(doc, "acceptance", acceptance_expected, acceptance_actual, issues)
    if rule.get("requires_guardrail_coverage"):
        _compare_coverage(doc, "guardrail", guard_expected, guard_actual, issues)

    applicability_key = rule.get("ui_applicability_document_key")
    ui_na = bool(isinstance(applicability_key, str) and _document_not_applicable(docs, contracts, applicability_key))
    screen_spec = coverage.get("screen", {})
    component_spec = coverage.get("component", {})
    screen_tables = _tables_in_section(doc, str(screen_spec.get("context", "")))
    component_tables = _tables_in_section(doc, str(component_spec.get("context", "")))
    if rule.get("requires_ui_coverage_when_applicable"):
        if not isinstance(applicability_key, str):
            issues.append(Issue("READINESS_SM", str(doc.path), catalog.line, "UI coverage does not declare its applicability document"))
        elif ui_na:
            if screen_tables or component_tables:
                issues.append(Issue("SLICE_UI_COVERAGE_NA", str(doc.path), 0, "no-UI project must omit screen/component coverage registries"))
        else:
            screen_expected = _catalog_membership(doc, catalog, slice_field, fields["screens"])
            component_expected = _catalog_membership(doc, catalog, slice_field, fields["components"])
            _compare_coverage(doc, "screen", screen_expected, _coverage_membership(doc, screen_spec), issues)
            _compare_coverage(doc, "component", component_expected, _coverage_membership(doc, component_spec), issues)

    if rule.get("requires_migration_assignment_exactly_once"):
        declared = {value for domain_doc in docs.get("DS", []) for raw, _ in _field_values(domain_doc, "DS-MIG-MIGRT", "MIGRATION_KEY") for value in ref_values(raw) if value != "(none)"}
        migration_idx = catalog.headers.index(fields["migrations"])
        counts: dict[str, int] = defaultdict(int)
        for row in catalog.rows:
            if max(slice_idx, migration_idx) >= len(row):
                continue
            for migration in ref_values(row[migration_idx]):
                if migration == "(none)" or (doc.mode == "template" and PLACEHOLDER.search(migration)):
                    continue
                counts[migration] += 1
        for migration in sorted(declared):
            if counts.get(migration, 0) != 1:
                issues.append(Issue("SLICE_MIGRATION_ASSIGNMENT", str(doc.path), catalog.line, f"migration {migration} must be assigned to exactly one slice"))

    dependency_spec = rule.get("dependency_edges")
    if not isinstance(dependency_spec, dict):
        issues.append(Issue("READINESS_SM", str(doc.path), catalog.line, "slice catalog readiness lacks dependency_edges"))
        return
    dep_context = dependency_spec.get("context")
    dep_from = dependency_spec.get("from_field")
    dep_to = dependency_spec.get("to_field")
    if not all(isinstance(value, str) and value for value in (dep_context, dep_from, dep_to)):
        issues.append(Issue("READINESS_SM", str(doc.path), catalog.line, "dependency_edges is malformed"))
        return
    dep_table = next((table for table in _tables_in_section(doc, dep_context) if dep_from in table.headers and dep_to in table.headers), None)
    if dep_table is None:
        issues.append(Issue("READINESS_SM", str(doc.path), 0, f"missing dependency edge registry {dep_context}"))
        return

    fi, ti = dep_table.headers.index(dep_from), dep_table.headers.index(dep_to)
    edges: set[tuple[str, str]] = set()
    graph: dict[str, set[str]] = defaultdict(set)
    for n, row in enumerate(dep_table.rows, 2):
        if max(fi, ti) >= len(row):
            continue
        source, dependency = row[fi], row[ti]
        if doc.mode == "template" and (PLACEHOLDER.search(source) or PLACEHOLDER.search(dependency)):
            continue
        if source not in slices or dependency not in slices:
            issues.append(Issue("SLICE_DEPENDENCY_KEY", str(doc.path), dep_table.line + n, f"dependency edge {source}->{dependency} references an unknown slice"))
            continue
        if source == dependency:
            issues.append(Issue("SLICE_DEPENDENCY_SELF", str(doc.path), dep_table.line + n, f"slice {source} cannot depend on itself"))
            continue
        pair = (source, dependency)
        if pair in edges:
            issues.append(Issue("SLICE_DEPENDENCY_DUP", str(doc.path), dep_table.line + n, f"duplicate dependency edge {source}->{dependency}"))
            continue
        edges.add(pair)
        graph[source].add(dependency)
        if dependency_spec.get("require_dependency_lower_order") and source in orders and dependency in orders and orders[dependency] >= orders[source]:
            issues.append(Issue("SLICE_DEPENDENCY_ORDER", str(doc.path), dep_table.line + n, f"{source} depends on non-earlier slice {dependency}"))

    if dependency_spec.get("require_acyclic"):
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                issues.append(Issue("SLICE_DEPENDENCY_CYCLE", str(doc.path), dep_table.line, f"slice dependency cycle includes {node}"))
                return
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph.get(node, set()):
                visit(dependency)
            visiting.remove(node)
            visited.add(node)
        for slice_key in slices:
            visit(slice_key)


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
    if not all(isinstance(value, str) and value for value in (module_context, edge_context, module_key_field, edge_from_field, edge_to_field, edge_rule_field, allow_value, deny_value)):
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
        if source not in modules or target not in modules:
            issues.append(Issue("DEPENDENCY_MODULE", str(doc.path), edge_table.line + n, f"dependency edge {source}->{target} references unknown module"))
        if rule.get("self_edges") == "implicit" and source == target:
            issues.append(Issue("DEPENDENCY_SELF", str(doc.path), edge_table.line + n, f"self edge {source}->{target} is implicit and must not be registered"))
        if edge_rule not in {allow_value, deny_value}:
            issues.append(Issue("DEPENDENCY_EDGE_RULE", str(doc.path), edge_table.line + n, f"unsupported EDGE_RULE {edge_rule!r}"))
        pair = (source, target)
        if pair in seen:
            code = "DEPENDENCY_EDGE_CONFLICT" if seen[pair] != edge_rule else "DEPENDENCY_EDGE_DUP"
            issues.append(Issue(code, str(doc.path), edge_table.line + n, f"dependency pair {source}->{target} is declared more than once"))
        else:
            seen[pair] = edge_rule


def _validate_command_prerequisite_dag(doc: NormDoc, rule: dict[str, Any], issues: list[Issue]) -> None:
    context = rule.get("context")
    command_field = rule.get("command_field")
    prerequisites_field = rule.get("prerequisites_field")
    if not all(isinstance(value, str) and value for value in (context, command_field, prerequisites_field)):
        issues.append(Issue("READINESS_COMMAND_DAG", str(doc.path), 0, "command DAG readiness metadata is malformed"))
        return
    table = next((candidate for candidate in _tables_in_section(doc, context) if command_field in candidate.headers and prerequisites_field in candidate.headers), None)
    if table is None:
        issues.append(Issue("READINESS_COMMAND_DAG", str(doc.path), 0, f"missing command registry {context}"))
        return
    ci, pi = table.headers.index(command_field), table.headers.index(prerequisites_field)
    commands: set[str] = set()
    rows: list[tuple[str, str, int]] = []
    for n, row in enumerate(table.rows, 2):
        if max(ci, pi) >= len(row):
            continue
        command, prerequisites = row[ci], row[pi]
        if doc.mode == "template" and PLACEHOLDER.search(command):
            continue
        if command in commands:
            issues.append(Issue("COMMAND_DUP", str(doc.path), table.line + n, f"duplicate command key {command}"))
        commands.add(command)
        rows.append((command, prerequisites, table.line + n))
    graph: dict[str, set[str]] = defaultdict(set)
    for command, raw, line in rows:
        for prerequisite in ref_values(raw):
            if doc.mode == "template" and PLACEHOLDER.search(prerequisite):
                continue
            if prerequisite not in commands:
                issues.append(Issue("COMMAND_PREREQUISITE", str(doc.path), line, f"{command} references unknown prerequisite {prerequisite}"))
                continue
            if prerequisite == command:
                issues.append(Issue("COMMAND_PREREQUISITE_SELF", str(doc.path), line, f"{command} cannot depend on itself"))
                continue
            graph[command].add(prerequisite)
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(command: str) -> None:
        if command in visiting:
            issues.append(Issue("COMMAND_PREREQUISITE_CYCLE", str(doc.path), table.line, f"command prerequisite cycle includes {command}"))
            return
        if command in visited:
            return
        visiting.add(command)
        for prerequisite in graph.get(command, set()):
            visit(prerequisite)
        visiting.remove(command)
        visited.add(command)
    for command in commands:
        visit(command)


def _detail_registry_keys(doc: NormDoc, parent: str, label: str) -> tuple[set[str], set[str]]:
    raw_text = doc.path.read_text(encoding="utf-8")
    text = strip_authoring_metadata(raw_text) if doc.mode == "template" else raw_text
    lines = text.splitlines()
    children = [heading for heading in doc.headings if heading.parent == parent]
    keys: set[str] = set()
    duplicates: set[str] = set()
    pattern = re.compile(rf"\*\*{re.escape(label)}:\*\*\s*`([^`]+)`")
    for child in children:
        end_line = len(lines) + 1
        for heading in doc.headings:
            if heading.line > child.line and heading.level <= child.level:
                end_line = heading.line
                break
        body = "\n".join(lines[child.line:end_line - 1])
        match = pattern.search(body)
        if not match:
            continue
        key = match.group(1)
        if key in keys:
            duplicates.add(key)
        keys.add(key)
    return keys, duplicates


def _validate_acceptance_mapping(doc: NormDoc, rule: dict[str, Any], contract: dict[str, Any], issues: list[Issue]) -> None:
    input_spec = str(rule.get("input_field", ""))
    mapping_context = str(rule.get("mapping_context", ""))
    if "." not in input_spec or not mapping_context:
        issues.append(Issue("READINESS_SLICE_MAPPING", str(doc.path), 0, "acceptance mapping readiness is malformed"))
        return
    input_context, input_field = input_spec.rsplit(".", 1)
    anchors = {value for raw, _ in _field_values(doc, input_context, input_field) for value in ref_values(raw)}
    anchor_binding = next((entry for entry in contract.get("typed_fields", []) if entry.get("context") == mapping_context and entry.get("namespace") == "acceptance_requirement_id"), None)
    test_binding = next((entry for entry in contract.get("typed_fields", []) if entry.get("context") == mapping_context and entry.get("namespace") == "test_key"), None)
    if not anchor_binding or not test_binding:
        issues.append(Issue("READINESS_SLICE_MAPPING", str(doc.path), 0, "acceptance mapping typed bindings are missing"))
        return
    mapped_anchors = {value for raw, _ in _field_values(doc, mapping_context, anchor_binding["field"]) for value in ref_values(raw)}
    mapped_tests = {value for raw, _ in _field_values(doc, mapping_context, test_binding["field"]) for value in ref_values(raw)}
    for anchor in sorted(anchors - mapped_anchors):
        issues.append(Issue("SLICE_ANCHOR_UNMAPPED", str(doc.path), 0, f"acceptance anchor {anchor} has no test mapping"))
    registry_context = rule.get("test_registry_context")
    registry_id_field = rule.get("test_registry_id_field")
    if not isinstance(registry_context, str) or not isinstance(registry_id_field, str):
        issues.append(Issue("READINESS_SLICE_MAPPING", str(doc.path), 0, "acceptance mapping does not declare its test registry"))
        return
    registered_tests = {value for raw, _ in _field_values(doc, registry_context, registry_id_field) for value in ref_values(raw)}
    for test_id in sorted(mapped_tests - registered_tests):
        issues.append(Issue("SLICE_TEST_UNREGISTERED", str(doc.path), 0, f"mapped test {test_id} is absent from {registry_context}"))
    if rule.get("requires_test_details"):
        detail_parent = rule.get("test_detail_parent")
        detail_label = rule.get("test_detail_registry_label")
        if not isinstance(detail_parent, str) or not isinstance(detail_label, str):
            issues.append(Issue("READINESS_SLICE_MAPPING", str(doc.path), 0, "test-detail readiness metadata is malformed"))
            return
        detailed_tests, duplicate_details = _detail_registry_keys(doc, detail_parent, detail_label)
        for test_id in sorted(duplicate_details):
            issues.append(Issue("SLICE_TEST_DETAIL_DUP", str(doc.path), 0, f"test detail key {test_id} is duplicated"))
        for test_id in sorted(mapped_tests - detailed_tests):
            issues.append(Issue("SLICE_TEST_DETAIL_MISSING", str(doc.path), 0, f"mapped test {test_id} has no detail under {detail_parent}"))


def _validate_required_registry_headers(doc: NormDoc, rule: dict[str, Any], issues: list[Issue]) -> None:
    specs = rule.get("tables")
    if not isinstance(specs, list) or not specs:
        issues.append(Issue("READINESS_REGISTRY_SCHEMA", str(doc.path), 0, "required_registry_headers requires a non-empty tables list"))
        return
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get("context"), str) or not isinstance(spec.get("headers"), list) or not all(isinstance(value, str) and value for value in spec.get("headers", [])):
            issues.append(Issue("READINESS_REGISTRY_SCHEMA", str(doc.path), 0, "required registry header declaration is malformed"))
            continue
        context = spec["context"]
        headers = spec["headers"]
        if not any(table.headers == headers for table in _tables_in_section(doc, context)):
            issues.append(Issue("REGISTRY_SCHEMA", str(doc.path), 0, f"{context} lacks required headers {headers}"))


def prefix_before_glob(pattern: str) -> str:
    cut = len(pattern)
    for token in ("*", "?", "["):
        pos = pattern.find(token)
        if pos >= 0:
            cut = min(cut, pos)
    return pattern[:cut]


def _owned_path_contains(owned: str, touched: str) -> bool:
    owner_prefix = prefix_before_glob(owned).rstrip("/")
    touched_prefix = prefix_before_glob(touched).rstrip("/")
    if not owner_prefix or not touched_prefix:
        return False
    return touched_prefix == owner_prefix or touched_prefix.startswith(owner_prefix + "/")


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
    if not all(isinstance(value, str) for value in (scope_context, scope_field, path_field, owner_key, owner_context, owner_key_field, owner_path_field)):
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
        if not any(_owned_path_contains(owned, touched) for owned in allowed):
            issues.append(Issue("SLICE_FILE_OWNER", str(doc.path), 0, f"touched file/glob {touched} is outside touched module ownership"))


def validate_readiness(key: str, doc: NormDoc, contract: dict[str, Any], docs: dict[str, list[NormDoc]], contracts: dict[str, dict[str, Any]], issues: list[Issue]) -> None:
    sections = {heading.sid for heading in doc.headings}
    for entry in contract.get("applicability", []):
        if isinstance(entry, dict) and entry.get("mode") == "required":
            section = entry.get("section")
            if isinstance(section, str) and section not in sections:
                issues.append(Issue("APPLICABILITY_REQUIRED", str(doc.path), 0, f"{key} is missing required applicability section {section}"))
    for rule in contract.get("readiness", []):
        if not isinstance(rule, dict):
            continue
        kind = rule.get("rule")
        if rule.get("enforcement") == "semantic":
            continue
        if kind == "required_sections":
            for sid in rule.get("sections", []):
                if sid not in sections:
                    issues.append(Issue("READINESS_SECTION", str(doc.path), 0, f"{key} is missing required section {sid}"))
        elif kind == "required_requirement_groups":
            minimum = int(rule.get("min_rows", 1))
            for sid in rule.get("sections", []):
                if _requirement_count(doc, sid) < minimum:
                    issues.append(Issue("READINESS_REQUIREMENT_GROUP", str(doc.path), 0, f"{key} requirement group {sid} has fewer than {minimum} rows"))
        elif kind == "required_registry_headers":
            _validate_required_registry_headers(doc, rule, issues)
        elif kind == "applicability_branch":
            selector = rule.get("selector", {}).get("not_applicable_requirement_group")
            applicable = rule.get("applicable", {})
            na = rule.get("not_applicable", {})
            if doc.mode == "template" and rule.get("template_mode") == "both_branches_scaffold":
                if selector and _requirement_count(doc, selector) < int(na.get("min_rows", 1)):
                    issues.append(Issue("READINESS_UI_NA", str(doc.path), 0, f"template scaffold {selector} is missing or empty"))
                for sid in applicable.get("required_sections", []):
                    if sid not in sections:
                        issues.append(Issue("READINESS_UI", str(doc.path), 0, f"template UI scaffold is missing {sid}"))
                for sid in applicable.get("required_requirement_groups", []):
                    if _requirement_count(doc, sid) < 1:
                        issues.append(Issue("READINESS_UI", str(doc.path), 0, f"template UI requirement group {sid} is empty"))
                continue
            if selector and selector in sections:
                minimum = int(na.get("min_rows", 1))
                if _requirement_count(doc, selector) < minimum:
                    issues.append(Issue("READINESS_UI_NA", str(doc.path), 0, f"{selector} has fewer than {minimum} rows"))
                for sid in na.get("omit_sections", []):
                    if sid in sections:
                        issues.append(Issue("READINESS_UI_NA", str(doc.path), 0, f"not-applicable UI must omit {sid}"))
            else:
                for sid in applicable.get("required_sections", []):
                    if sid not in sections:
                        issues.append(Issue("READINESS_UI", str(doc.path), 0, f"applicable UI is missing {sid}"))
                for sid in applicable.get("required_requirement_groups", []):
                    if _requirement_count(doc, sid) < 1:
                        issues.append(Issue("READINESS_UI", str(doc.path), 0, f"applicable UI requirement group {sid} is empty"))
        elif kind == "ancestry_mode":
            if rule.get("value") == "origin":
                for req in doc.reqs.values():
                    if req.derived != "(origin)":
                        issues.append(Issue("READINESS_ANCESTRY", str(doc.path), req.line, f"{req.rid} must use (origin)"))
        elif kind == "detail_id_sequence":
            parent = rule.get("parent")
            pattern = re.compile(str(rule.get("terminal_pattern", r".*")))
            first = str(rule.get("first", ""))
            children = [heading for heading in doc.headings if heading.parent == parent]
            terminals = [heading.sid.rsplit("-", 1)[-1] for heading in children]
            for heading, terminal in zip(children, terminals):
                if not pattern.fullmatch(terminal):
                    issues.append(Issue("DETAIL_ID", str(doc.path), heading.line, f"detail ID {terminal} violates {rule.get('terminal_pattern')}"))
            if terminals and first and terminals[0] != first:
                issues.append(Issue("DETAIL_ID", str(doc.path), children[0].line, f"first detail ID must be {first}"))
            if terminals and re.fullmatch(r"[A-Z]\d{4}", first) and all(re.fullmatch(r"[A-Z]\d{4}", terminal) for terminal in terminals):
                prefix = first[0]
                if any(terminal[0] != prefix for terminal in terminals):
                    issues.append(Issue("DETAIL_ID", str(doc.path), children[0].line, f"detail IDs must use prefix {prefix}"))
                    continue
                numbers = [int(terminal[1:]) for terminal in terminals]
                if rule.get("ordering", "strictly_increasing") == "strictly_increasing" and any(left >= right for left, right in zip(numbers, numbers[1:])):
                    issues.append(Issue("DETAIL_ID", str(doc.path), children[0].line, "detail IDs must be strictly increasing in document order"))
                if rule.get("gaps") == "forbidden":
                    expected = list(range(int(first[1:]), int(first[1:]) + len(numbers)))
                    if numbers != expected:
                        issues.append(Issue("DETAIL_ID", str(doc.path), children[0].line, f"detail IDs must be contiguous from {first}"))
        elif kind == "dependency_registry_mode":
            _validate_dependency_allowlist(doc, rule, issues)
        elif kind == "command_prerequisite_dag":
            _validate_command_prerequisite_dag(doc, rule, issues)
        elif kind == "slice_catalog_complete":
            _validate_slice_catalog(doc, docs, contracts, rule, issues)
        elif kind == "acceptance_mapping_complete":
            _validate_acceptance_mapping(doc, rule, contract, issues)
        elif kind == "field_ownership":
            _validate_field_ownership(doc, docs, rule, issues)
        else:
            issues.append(Issue("READINESS_RULE_UNSUPPORTED", str(doc.path), 0, f"unsupported deterministic readiness rule {kind}"))


__all__ = [name for name in globals() if not name.startswith("__")]
