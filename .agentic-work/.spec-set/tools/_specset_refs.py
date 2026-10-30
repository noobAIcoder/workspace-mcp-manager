try:
    from ._specset_document import *
except ImportError:
    from _specset_document import *

try:
    from ._specset_glob import *
except ImportError:
    from _specset_glob import *


def _tables_in_section(doc: NormDoc, section: str) -> list[MdTable]:
    return [table for table in doc.tables if table.section == section]


def _field_values(doc: NormDoc, context: str, field: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for table in _tables_in_section(doc, context):
        if table.headers == ["FIELD", "VALUE"]:
            for n, row in enumerate(table.rows, 2):
                if len(row) >= 2 and row[0] == field:
                    result.append((row[1], table.line + n))
        elif field in table.headers:
            idx = table.headers.index(field)
            for n, row in enumerate(table.rows, 2):
                if idx < len(row):
                    result.append((row[idx], table.line + n))
    return result


def _document_field_values(doc: NormDoc, field: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for table in doc.tables:
        if table.headers != ["FIELD", "VALUE"]:
            continue
        for n, row in enumerate(table.rows, 2):
            if len(row) >= 2 and row[0] == field:
                result.append((row[1], table.line + n))
    return result


def _namespace_declarations(index: IndexModel, docs: dict[str, list[NormDoc]], req_owner: dict[str, str], project_paths: set[str] | None) -> dict[str, set[str] | None]:
    cache: dict[str, set[str] | None] = {}
    resolving: set[str] = set()

    def resolve(namespace: str) -> set[str] | None:
        if namespace in cache:
            return cache[namespace]
        if namespace in resolving:
            return set()
        meta = index.namespaces.get(namespace)
        if meta is None:
            return set()
        resolving.add(namespace)
        declaration = meta.get("DECLARATION", "")
        values: set[str] | None
        if declaration == "generated_paths":
            values = project_paths if project_paths is not None else None
        elif declaration == "requirements:*":
            values = set(req_owner)
        elif declaration.startswith("requirements:"):
            prefix = declaration.split(":", 1)[1]
            values = {rid for rid in req_owner if rid.startswith(prefix)}
        elif declaration.startswith("registry:"):
            parts = declaration.split(":", 3)
            values = set()
            if len(parts) == 4:
                _, owner_key, context, field = parts
                for doc in docs.get(owner_key, []):
                    for raw, _ in _field_values(doc, context, field):
                        values.update(ref_values(raw))
                values.discard("(none)")
        elif declaration.startswith("union:"):
            values = set()
            for member in declaration.split(":", 1)[1].split(","):
                member_values = resolve(member)
                if member_values:
                    values.update(member_values)
        elif declaration.startswith("syntax:"):
            values = None
        else:
            values = set()
        resolving.remove(namespace)
        cache[namespace] = values
        return values

    for namespace in index.namespaces:
        resolve(namespace)
    return cache


def _is_template_value(value: str, mode: str) -> bool:
    return mode == "template" and bool(PLACEHOLDER.search(value))


def _validate_reference_value(value: str, binding: dict[str, Any], declarations: dict[str, set[str] | None], path: Path, line: int, mode: str, issues: list[Issue]) -> None:
    empty = binding.get("empty")
    kind = binding.get("kind")
    if value == "(none)":
        if empty == "forbidden":
            issues.append(Issue("TYPED_REF_EMPTY", str(path), line, f"{binding['context']}.{binding['field']} forbids (none)"))
        return
    if kind == "list_or_origin" and value == "(origin)":
        return
    if _is_template_value(value, mode):
        return

    values = [value] if kind == "scalar" else value.split(",")
    if kind in {"list", "list_or_origin"} and (", " in value or " ," in value):
        issues.append(Issue("LIST_WHITESPACE", str(path), line, f"{binding['context']}.{binding['field']} uses whitespace around comma"))

    namespace = binding.get("namespace")
    if namespace == "path_glob":
        for item in values:
            if not valid_repo_path(item, allow_glob=True):
                issues.append(Issue("TYPED_PATH", str(path), line, f"invalid path/glob {item}"))
                continue
            status, reason = path_glob_status(item)
            if status == "invalid":
                issues.append(Issue("TYPED_PATH_GLOB_INVALID", str(path), line, f"invalid path/glob {item}: {reason}"))
            elif status == "unsupported":
                issues.append(Issue("TYPED_PATH_GLOB_UNSUPPORTED", str(path), line, f"unsupported path/glob {item}: {reason}"))
        return

    if namespace == "document_path":
        for item in values:
            if _is_template_value(item, mode) or (mode == "template" and "<NNN>" in item):
                continue
            if "<NNN>" in item:
                issues.append(Issue("DOCUMENT_PATTERN", str(path), line, f"materialized document path contains class pattern {item}"))
                continue
            if not valid_repo_path(item, allow_glob=False):
                issues.append(Issue("TYPED_PATH", str(path), line, f"invalid document path {item}"))
            elif declarations.get(namespace) is not None and item not in (declarations.get(namespace) or set()):
                issues.append(Issue("TYPED_REF", str(path), line, f"{binding['field']} references unknown document_path {item}"))
        return

    allowed = declarations.get(str(namespace))
    if allowed is None:
        return
    for item in values:
        if mode == "template" and (PLACEHOLDER.search(item) or ("TBD" in item and item in allowed)):
            continue
        if item not in allowed:
            issues.append(Issue("TYPED_REF", str(path), line, f"{binding['field']} references unknown {namespace} {item}"))


def _global_binding(row: dict[str, str]) -> dict[str, str]:
    return {"context": row["FIELD_CONTEXT"], "field": row["FIELD_NAME"], "kind": row["VALUE_KIND"], "namespace": row["TARGET_NAMESPACE"], "empty": row["EMPTY_SENTINEL"]}


def validate_global_refs(index: IndexModel, docs: dict[str, list[NormDoc]], req_owner: dict[str, str], issues: list[Issue], project_paths: set[str] | None = None) -> None:
    declarations = _namespace_declarations(index, docs, req_owner, project_paths)
    for row in index.global_fields:
        binding = _global_binding(row)
        context = binding["context"]
        field = binding["field"]
        if context == "document_link":
            for entries in docs.values():
                for doc in entries:
                    values = _document_field_values(doc, field)
                    if not values:
                        issues.append(Issue("GLOBAL_FIELD_MISSING", str(doc.path), 0, f"document link is missing global field {field}"))
                        continue
                    if len(values) != 1:
                        issues.append(Issue("GLOBAL_FIELD_COUNT", str(doc.path), values[0][1], f"document link global field {field} occurs {len(values)} times"))
                        continue
                    value, line = values[0]
                    _validate_reference_value(value, binding, declarations, doc.path, line, doc.mode, issues)
        elif context == "requirement_row" and field == "DERIVED_FROM":
            for entries in docs.values():
                for doc in entries:
                    for req in doc.reqs.values():
                        _validate_reference_value(req.derived, binding, declarations, doc.path, req.line, doc.mode, issues)
        else:
            issues.append(Issue("GLOBAL_FIELD_CONTEXT", str(index.path), 0, f"unsupported global field context {context}.{field}"))


def validate_typed_refs(index: IndexModel, docs: dict[str, list[NormDoc]], contracts: dict[str, dict[str, Any]], req_owner: dict[str, str], issues: list[Issue], project_paths: set[str] | None = None) -> None:
    declarations = _namespace_declarations(index, docs, req_owner, project_paths)
    for key, entries in docs.items():
        contract = contracts.get(key)
        if not contract:
            continue
        for doc in entries:
            for binding in contract.get("typed_fields", []):
                for value, line in _field_values(doc, binding["context"], binding["field"]):
                    _validate_reference_value(value, binding, declarations, doc.path, line, doc.mode, issues)
            for spec in contract.get("typed_discriminators", []):
                if not isinstance(spec, dict):
                    continue
                context = spec.get("context")
                selector = spec.get("selector_field")
                value_field = spec.get("value_field")
                mapping = spec.get("mapping", {})
                if not all(isinstance(value, str) for value in (context, selector, value_field)) or not isinstance(mapping, dict):
                    continue
                for table in _tables_in_section(doc, context):
                    if selector not in table.headers or value_field not in table.headers:
                        continue
                    si, vi = table.headers.index(selector), table.headers.index(value_field)
                    for n, row in enumerate(table.rows, 2):
                        if max(si, vi) >= len(row):
                            continue
                        selector_value = row[si]
                        namespace = mapping.get(selector_value)
                        if not namespace:
                            if doc.mode == "template" and PLACEHOLDER.search(selector_value):
                                continue
                            issues.append(Issue("TYPED_DISCRIMINATOR", str(doc.path), table.line + n, f"unknown {selector} value {selector_value!r} in {context}"))
                            continue
                        binding = {"context": context, "field": value_field, "kind": "scalar", "namespace": namespace, "empty": "forbidden"}
                        _validate_reference_value(row[vi], binding, declarations, doc.path, table.line + n, doc.mode, issues)


__all__ = [name for name in globals() if not name.startswith("__")]
