try:
    from ._specset_rules import *
except ImportError:
    from _specset_rules import *


def _framework_path(root: Path, codegen: Path, row: dict[str, str]) -> Path:
    return codegen / row["ACTIVE_PATH"]


def _active_reference_files(root: Path) -> Iterable[Path]:
    direct = [root / "AGENTS.md", root / "tools/specset_validate.py"]
    for path in direct:
        if path.is_file():
            yield path
    templates = root / ".templates"
    if templates.exists():
        for path in templates.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".yaml", ".yml", ".json"}:
                yield path
    positive = root / "tests/fixtures/specset/workspace"
    if positive.exists():
        for path in positive.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".yaml", ".yml", ".json", ".py"}:
                yield path


def _stale_reference_issues(root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for path in _active_reference_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(lines, 1):
            for literal in STALE_ACTIVE_LITERALS:
                if literal in line:
                    issues.append(Issue("STALE_ACTIVE_REFERENCE", str(path), line_no, f"active content contains obsolete reference {literal}"))
    return issues


def _validate_contract_against_index(path: Path, key: str, contract: dict[str, Any], index: IndexModel, issues: list[Issue]) -> None:
    if contract.get("document_key") != key:
        issues.append(Issue("CONTRACT_DOCUMENT_KEY", str(path), 0, f"contract document_key {contract.get('document_key')!r} does not match {key}"))
    for binding in contract.get("typed_fields", []):
        namespace = binding.get("namespace")
        if namespace not in index.namespaces:
            issues.append(Issue("CONTRACT_NAMESPACE", str(path), 0, f"typed field {binding.get('context')}.{binding.get('field')} uses unknown namespace {namespace}"))
    for spec in contract.get("typed_discriminators", []):
        if not isinstance(spec, dict) or not isinstance(spec.get("mapping"), dict):
            issues.append(Issue("CONTRACT_DISCRIMINATOR", str(path), 0, "typed_discriminators entries require a mapping object"))
            continue
        for namespace in spec["mapping"].values():
            if namespace not in index.namespaces:
                issues.append(Issue("CONTRACT_NAMESPACE", str(path), 0, f"typed discriminator uses unknown namespace {namespace}"))


def _validate_framework_authority_layout(root: Path, index: IndexModel) -> list[Issue]:
    del root
    issues: list[Issue] = []
    text = index.path.read_text(encoding="utf-8") if index.path.exists() else ""
    for forbidden in ("## IX-RDY ", "## IX-SLC ", "### IX-REF-FLDRT "):
        if forbidden in text:
            issues.append(Issue("INDEX_LOCAL_AUTHORITY", str(index.path), 0, f"index reintroduces document-local authority {forbidden.strip()}"))
    if "REQUIRED_REVIEW_KEYS" in text or "SOURCE_KEY | CHANGE_AREA" in text:
        issues.append(Issue("INDEX_PROPAGATION_DUP", str(index.path), 0, "index contains a duplicate per-document propagation registry"))
    if "product_brief_profile" in text or "| BP |" in text or ("PRODUCT_BRIEF_" + "PROFILE.specset.md") in text:
        issues.append(Issue("BP_PROFILE_IDENTITY", str(index.path), 0, "obsolete Product Brief profile identity remains in index"))
    if "FRAMEWORK_KEY" in text or "FRAMEWORK_SOURCE" in text:
        issues.append(Issue("INDEX_LEGACY_SCOPE", str(index.path), 0, "Spec Set index still uses ambiguous legacy framework registry terminology"))
    return issues


def validate_framework(root: Path) -> list[Issue]:
    issues: list[Issue] = []
    codegen = root / ".templates/codegen"
    service = root / ".templates/service"
    index_path = codegen / "SPEC_FILES_INDEX.specset.md"
    index = parse_index(index_path)
    issues.extend(index.issues)
    issues.extend(_validate_framework_authority_layout(root, index))
    agent = root / "AGENTS.md"
    if not agent.exists():
        issues.append(Issue("AGENT_POLICY_MISSING", str(agent), 0, "sole agent policy is missing"))
    obsolete_agent = root / ("AGENTS." + "specset.md")
    if obsolete_agent.exists():
        issues.append(Issue("OBSOLETE_AGENT_POLICY", str(obsolete_agent), 0, "duplicate agent policy remains active"))
    legacy_agents = root / ".agents"
    if legacy_agents.exists():
        issues.append(Issue("STALE_ACTIVE_LAYOUT", str(legacy_agents), 0, "legacy .agents directory remains active"))
    legacy_upper = next((entry for entry in root.iterdir() if entry.name == ".AGENTS"), None) if root.exists() else None
    if legacy_upper is not None:
        issues.append(Issue("STALE_ACTIVE_CASING", str(legacy_upper), 0, "uppercase legacy .AGENTS directory remains active"))
    profile = codegen / ("PRODUCT_BRIEF_" + "PROFILE.specset.md")
    if profile.exists():
        issues.append(Issue("BP_PROFILE_IDENTITY", str(profile), 0, "standalone Product Brief profile must be absent"))
    for row in index.framework.values():
        if row.get("STATUS") != "active":
            continue
        path = _framework_path(root, codegen, row)
        if not path.exists():
            issues.append(Issue("SPECSET_FILE_MISSING", str(path), 0, f"active Spec Set file {row['ACTIVE_PATH']} is missing"))
    if codegen.exists():
        for path in codegen.glob("*.md"):
            if path.name in LEGACY_NAMES:
                issues.append(Issue("LEGACY_FILENAME", str(path), 0, "legacy active Spec Set filename remains"))
            if ".specset." not in path.name and not path.name.endswith(".specset.md"):
                issues.append(Issue("SPECSET_FILENAME", str(path), 0, "active codegen Markdown file lacks .specset"))
    if service.exists():
        for path in service.glob("*.md"):
            if path.name in LEGACY_NAMES:
                issues.append(Issue("LEGACY_FILENAME", str(path), 0, "obsolete service-template filename remains"))
    issues.extend(_stale_reference_issues(root))
    template_rows = [row for row in index.framework.values() if row.get("CLASS") == "specset_template" and row.get("STATUS") == "active"]
    actual_template_keys = {row["SPECSET_KEY"] for row in template_rows}
    expected_template_keys = {key for key, row in index.documents.items() if row.get("SPECSET_SOURCE", "").endswith(".specset.template.md")}
    if actual_template_keys != expected_template_keys:
        issues.append(Issue("TEMPLATE_REGISTRY", str(index_path), 0, "active Spec Set template keys differ from document-registry template sources; " f"missing={sorted(expected_template_keys - actual_template_keys)}, extra={sorted(actual_template_keys - expected_template_keys)}"))
    contracts: dict[str, dict[str, Any]] = {}
    docs: dict[str, list[NormDoc]] = defaultdict(list)
    for row in template_rows:
        key = row["SPECSET_KEY"]
        path = codegen / row["ACTIVE_PATH"]
        contract, contract_issues = authoring_contract(path)
        issues.extend(contract_issues)
        if contract:
            contracts[key] = contract
            _validate_contract_against_index(path, key, contract, index, issues)
        if row.get("VALIDATION_PROFILE") == "product_brief":
            if contract:
                issues.extend(validate_brief(path, "template", contract=contract, framework_root=root))
            continue
        doc = parse_normative(path, "template")
        docs[key].append(doc)
        issues.extend(doc.issues)
        if contract:
            validate_readiness(key, doc, contract, docs, contracts, issues)
        for field in ("upstream_docs", "downstream_docs", "source_docs"):
            if field not in doc.links:
                issues.append(Issue("LINK_FIELD", str(path), 0, f"missing {field}"))
            else:
                issues.extend(path_list_issues(doc.links[field], path, allow_pattern=True))
        if key in index.documents:
            exp_up, exp_down, exp_src = expected_links(index, key)
            if split_list(doc.links.get("upstream_docs", "(none)")) != exp_up:
                issues.append(Issue("INDEX_MIRROR_UP", str(path), 0, f"template upstream_docs differs from {exp_up}"))
            if split_list(doc.links.get("downstream_docs", "(none)")) != exp_down:
                issues.append(Issue("INDEX_MIRROR_DOWN", str(path), 0, f"template downstream_docs differs from {exp_down}"))
            if split_list(doc.links.get("source_docs", "(none)")) != exp_src:
                issues.append(Issue("INDEX_MIRROR_SOURCE", str(path), 0, f"template source_docs differs from {exp_src}"))
    for namespace, meta in index.namespaces.items():
        declaration = meta.get("DECLARATION", "")
        if declaration.startswith("registry:"):
            parts = declaration.split(":", 3)
            if len(parts) != 4:
                continue
            _, owner_key, section, field = parts
            matching = [table for owner_doc in docs.get(owner_key, []) for table in _tables_in_section(owner_doc, section) if field in table.headers]
            if not matching:
                issues.append(Issue("NAMESPACE_OWNER", str(index_path), 0, f"namespace {namespace} declaration {declaration} is not present in its authoritative template"))
        elif declaration.startswith("requirements:") and declaration != "requirements:*":
            prefix = declaration.split(":", 1)[1]
            if not any(rid.startswith(prefix) for entries in docs.values() for doc in entries for rid in doc.reqs):
                issues.append(Issue("NAMESPACE_OWNER", str(index_path), 0, f"namespace {namespace} requirement prefix {prefix} has no declaration in active templates"))
    req_owner: dict[str, str] = {}
    req_graph: dict[str, list[str]] = {}
    for key, entries in docs.items():
        for doc in entries:
            for rid, req in doc.reqs.items():
                if rid in req_owner:
                    issues.append(Issue("REQ_GLOBAL_DUP", str(doc.path), req.line, f"requirement ID repeats across templates: {rid}"))
                req_owner[rid] = key
                req_graph[rid] = [] if req.derived == "(origin)" else req.derived.split(",")
    _validate_ancestry(index, docs, req_owner, req_graph, issues, template_mode=True)
    validate_global_refs(index, docs, req_owner, issues)
    validate_typed_refs(index, docs, contracts, req_owner, issues)
    ruleset = codegen / "ID_RULESET.specset.md"
    if ruleset.exists():
        rules_text = ruleset.read_text(encoding="utf-8")
        for forbidden in ("UI-CMP-CMPDT", "UI-SCR-SCRDT", "PRODUCT_BRIEF_" + "PROFILE"):
            if forbidden in rules_text:
                issues.append(Issue("RULESET_LOCAL_AUTHORITY", str(ruleset), 0, f"ruleset contains document-local authority {forbidden}"))
    if agent.exists():
        for name in re.findall(r"`([^`]+\.template\.md)`", agent.read_text(encoding="utf-8")):
            if "*" not in name and not (service / Path(name).name).exists():
                issues.append(Issue("SERVICE_ROUTE", str(agent), 0, f"routed service template does not exist: {name}"))
    return issues


def _validate_ancestry(index: IndexModel, docs: dict[str, list[NormDoc]], req_owner: dict[str, str], req_graph: dict[str, list[str]], issues: list[Issue], template_mode: bool) -> None:
    for key, entries in docs.items():
        upstream = set(graph_inputs(index, key, "normative"))
        for doc in entries:
            for req in doc.reqs.values():
                role = index.documents.get(key, {}).get("ROLE")
                if role == "normative_origin":
                    if req.derived != "(origin)":
                        issues.append(Issue("ORIGIN_DERIVATION", str(doc.path), req.line, f"{req.rid} must use (origin)"))
                    continue
                if req.derived == "(origin)":
                    issues.append(Issue("DERIVED_ORIGIN", str(doc.path), req.line, f"derived requirement {req.rid} uses (origin)"))
                    continue
                for ref in req.derived.split(","):
                    if template_mode and PLACEHOLDER.search(ref):
                        continue
                    owner = req_owner.get(ref)
                    if owner is None:
                        issues.append(Issue("DERIVED_DANGLING", str(doc.path), req.line, f"unknown requirement {ref}"))
                    elif owner == key:
                        issues.append(Issue("DERIVED_SAME_DOC", str(doc.path), req.line, f"same-document derivation {ref}"))
                    elif owner not in upstream:
                        issues.append(Issue("DERIVED_NOT_IMMEDIATE", str(doc.path), req.line, f"{ref} belongs to non-immediate upstream {owner}"))
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(rid: str) -> None:
        if rid in visiting:
            issues.append(Issue("DERIVED_CYCLE", str(index.path), 0, f"requirement cycle includes {rid}"))
            return
        if rid in visited:
            return
        visiting.add(rid)
        for parent in req_graph.get(rid, []):
            if parent in req_graph:
                visit(parent)
        visiting.remove(rid)
        visited.add(rid)
    for rid in req_graph:
        visit(rid)


def _manifest_required_fields(index: IndexModel | None, section: str, fallback: list[str]) -> list[str]:
    if index is None:
        return fallback
    row = index.manifest_fields.get(section)
    if not row:
        return fallback
    return row.get("REQUIRED_FIELDS", "").split(",")


def parse_manifest(path: Path, slug: str, index: IndexModel | None = None) -> tuple[list[dict[str, str]], list[Issue]]:
    issues: list[Issue] = []
    text = path.read_text(encoding="utf-8")
    if not text.startswith(f"# Spec Set Manifest: {slug}\n"):
        issues.append(Issue("MANIFEST_TITLE", str(path), 1, "manifest title does not match project slug"))
    if any(token in text for token in REQ_HEADERS) or RFC.search(text):
        issues.append(Issue("MANIFEST_NORMATIVE", str(path), 0, "project manifest contains normative content"))
    project_required = _manifest_required_fields(index, "MF-PRJ", ["project_slug", "status", "generated_at"])
    project_rows = table_rows(path, ["FIELD", "VALUE"])
    project_values = {row[0]: row[1] for row in project_rows if len(row) == 2}
    for field in project_required:
        if not project_values.get(field):
            issues.append(Issue("MANIFEST_FIELD", str(path), 0, f"MF-PRJ is missing required field {field}"))
    if project_values.get("project_slug") and project_values["project_slug"] != slug:
        issues.append(Issue("MANIFEST_SLUG", str(path), 0, f"manifest project_slug {project_values['project_slug']!r} does not match {slug!r}"))
    status = project_values.get("status")
    if status and not re.fullmatch(r"[a-z][a-z0-9_]*", status):
        issues.append(Issue("MANIFEST_STATUS", str(path), 0, f"invalid manifest status {status!r}"))
    generated_at = project_values.get("generated_at")
    if generated_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", generated_at):
        issues.append(Issue("MANIFEST_TIMESTAMP", str(path), 0, f"invalid generated_at {generated_at!r}"))
    file_required = _manifest_required_fields(index, "MF-FIL", ["document_key", "role", "path", "status", "source_docs", "upstream_docs", "downstream_docs"])
    headers = [field.upper() for field in file_required]
    rows = rows_as_dicts(path, headers)
    if not rows:
        issues.append(Issue("MANIFEST_TABLE", str(path), 0, "manifest registry table with required fields is missing"))
    for row in rows:
        for header in headers:
            if row.get(header, "") == "":
                issues.append(Issue("MANIFEST_FIELD", str(path), 0, f"MF-FIL row for {row.get('PATH', '(unknown)')} has empty {header}"))
        row_status = row.get("STATUS")
        if row_status and not re.fullmatch(r"[a-z][a-z0-9_]*", row_status):
            issues.append(Issue("MANIFEST_STATUS", str(path), 0, f"invalid MF-FIL status {row_status!r}"))
    return rows, issues


def _manifest_expected_rows(index: IndexModel, slug: str, expansions: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, row in index.documents.items():
        paths = expansions.get(key) or [materialize(row["MATERIALIZED_PATH"], slug)]
        for ordinal, path in enumerate(paths, 1):
            upstream, downstream, source = expected_links(index, key, slug, expansions)
            identity = key if len(paths) == 1 else f"{key}:{ordinal:03d}"
            result[identity] = {"document_key": identity, "role": row["ROLE"], "path": path, "source": source, "upstream": upstream, "downstream": downstream}
    return result


__all__ = [name for name in globals() if not name.startswith("__")]