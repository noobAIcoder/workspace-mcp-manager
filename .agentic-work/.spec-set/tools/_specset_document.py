try:
    from ._specset_model import *
except ImportError:
    from _specset_model import *


def _placeholder_line(line: str) -> bool:
    return bool(
        PLACEHOLDER.search(line)
        or re.search(r"\bTBD\b", line)
        or re.search(r"(?:^|[-_])TBD(?:[_-]|$)", line)
        or "<project_slug>" in line
        or "<NNN>" in line
        or "SLICE_###" in line
        or re.search(r"(?:DS|TS|UI|CS|SM|SX)-[^|, ]*\.\.\.", line)
    )


def _requirement_item_code(rid: str, group: HeadingNode | None) -> str:
    if group is None:
        return ""
    prefix = group.sid + "-"
    return rid[len(prefix):] if rid.startswith(prefix) else ""


def parse_normative(path: Path, mode: str) -> NormDoc:
    issues: list[Issue] = []
    if not path.exists():
        return NormDoc(path, mode, [], [], {}, {}, [Issue("DOCUMENT_MISSING", str(path), 0, "normative document is missing")])
    raw_text = path.read_text(encoding="utf-8")
    if mode == "materialized" and "specset-authoring" in raw_text:
        issues.append(Issue("AUTHORING_METADATA", str(path), 0, "materialized document contains framework-authoring metadata"))
    text = strip_authoring_metadata(raw_text) if mode == "template" else raw_text
    lines = text.splitlines()
    fenced = fence_state(lines)
    headings: list[HeadingNode] = []
    stack: list[HeadingNode] = []

    first_nonempty = next((i for i, line in enumerate(lines) if line.strip()), None)
    for i, line in enumerate(lines):
        if fenced[i] or not line.startswith("#"):
            continue
        match = HEADING.fullmatch(line)
        if not match:
            issues.append(Issue("HEADING_FORMAT", str(path), i + 1, "invalid normative heading"))
            continue
        level = len(match.group(1))
        sid, title = match.group(2), match.group(3)
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        if parent:
            parent.has_child = True
        node = HeadingNode(level, sid, title, i + 1, parent.sid if parent else None)
        headings.append(node)
        stack.append(node)
        if not valid_sid(sid, level):
            issues.append(Issue("SECTION_ID", str(path), i + 1, f"invalid H{level} section ID {sid}"))
        if parent and not sid.startswith(parent.sid + "-"):
            issues.append(Issue("SECTION_PARENT", str(path), i + 1, f"{sid} is not under parent {parent.sid}"))
        if not title or not title.isascii() or "||" in title or RFC.search(title):
            issues.append(Issue("SECTION_TITLE", str(path), i + 1, "invalid section title"))

    if first_nonempty is None or not headings or headings[0].line != first_nonempty + 1 or headings[0].level != 1:
        issues.append(Issue("FIRST_HEADING", str(path), (first_nonempty or 0) + 1, "first non-empty line is not a normative H1"))

    seen: set[str] = set()
    for heading in headings:
        if heading.sid in seen:
            issues.append(Issue("SECTION_DUP", str(path), heading.line, f"duplicate section ID {heading.sid}"))
        seen.add(heading.sid)

    heading_at = {h.line: h for h in headings}
    current: HeadingNode | None = None
    tables: list[MdTable] = []
    i = 0
    while i < len(lines):
        if i + 1 in heading_at:
            current = heading_at[i + 1]
        if fenced[i] or not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        start = i
        raw: list[str] = []
        while i < len(lines) and not fenced[i] and lines[i].lstrip().startswith("|"):
            raw.append(lines[i])
            i += 1
        if len(raw) < 2:
            continue
        parsed = [cells(row) for row in raw]
        if not separator(parsed[1]):
            continue
        width = len(parsed[0])
        if any(len(row) != width for row in parsed):
            issues.append(Issue("TABLE_WIDTH", str(path), start + 1, "inconsistent pipe-table row width"))
        tables.append(MdTable(parsed[0], parsed[2:], start + 1, current.sid if current else None))

    hmap = {h.sid: h for h in headings}
    requirement_groups = {
        table.section
        for table in tables
        if table.headers == REQ_HEADERS
        and table.section in hmap
        and hmap[table.section].level >= 3
        and not hmap[table.section].has_child
    }
    reqs: dict[str, RequirementRow] = {}
    for table in tables:
        if table.headers == REQ_HEADERS:
            group = hmap.get(table.section or "")
            if group is None or group.level < 3 or group.has_child:
                issues.append(Issue("REQ_GROUP", str(path), table.line, "requirements table is not directly scoped by a leaf H3-H5"))
            for n, row in enumerate(table.rows, 2):
                line = table.line + n
                if len(row) != 4:
                    continue
                rid, rtext, notes, derived = row
                if group and not valid_rid(rid, group.sid, mode):
                    issues.append(Issue("REQ_ID", str(path), line, f"invalid requirement ID {rid}"))
                item_code = _requirement_item_code(rid, group)
                has_placeholder = any(PLACEHOLDER.search(cell) for cell in row)
                if mode == "template" and has_placeholder and "TBD" not in item_code:
                    issues.append(Issue("PLACEHOLDER_REQ_ID", str(path), line, f"placeholder Requirement Item {rid} must use an item code containing TBD"))
                if rid in reqs:
                    issues.append(Issue("REQ_DUP", str(path), line, f"duplicate requirement ID {rid}"))
                if not rtext or not RFC.search(rtext):
                    issues.append(Issue("REQ_TEXT", str(path), line, f"{rid} lacks an RFC 2119 keyword"))
                if not derived:
                    issues.append(Issue("DERIVED_EMPTY", str(path), line, f"{rid} has empty DERIVED_FROM"))
                reqs[rid] = RequirementRow(rid, rtext, notes, derived, line)
        else:
            if table.section in requirement_groups:
                issues.append(Issue("REG_REQUIREMENT_GROUP", str(path), table.line, "requirement group directly scopes a Registry Table"))
            if RESERVED.intersection(table.headers):
                issues.append(Issue("REG_RESERVED", str(path), table.line, "registry header uses a reserved requirement column"))
            for n, row in enumerate(table.rows, 2):
                if any(RFC.search(cell) for cell in row):
                    issues.append(Issue("REG_NORMATIVE", str(path), table.line + n, "registry cell contains an RFC 2119 keyword"))

    links: dict[str, str] = {}
    for table in tables:
        if table.headers != ["FIELD", "VALUE"]:
            continue
        candidate = {row[0]: row[1] for row in table.rows if len(row) == 2}
        if {"upstream_docs", "downstream_docs", "source_docs"}.issubset(candidate):
            links = candidate
            if "materialized_path" in candidate:
                issues.append(Issue("SELF_PATH_MIRROR", str(path), table.line, "document link registry duplicates artifact identity with materialized_path"))
            break

    if mode == "materialized":
        for line_no, line in enumerate(lines, 1):
            if fenced[line_no - 1]:
                continue
            if _placeholder_line(line):
                issues.append(Issue("PLACEHOLDER", str(path), line_no, "materialized document contains a template placeholder"))

    return NormDoc(path, mode, headings, tables, reqs, links, issues)


def path_list_issues(value: str, path: Path, allow_pattern: bool = True) -> list[Issue]:
    result: list[Issue] = []
    if value == "(none)":
        return result
    if ", " in value or " ," in value:
        result.append(Issue("LIST_WHITESPACE", str(path), 0, "list uses whitespace around comma"))
    for item in value.split(","):
        if not valid_repo_path(item, allow_glob=False):
            result.append(Issue("PATH_NORMALIZATION", str(path), 0, f"invalid repo-relative path {item}"))
        elif not allow_pattern and "<NNN>" in item:
            result.append(Issue("DOCUMENT_PATTERN", str(path), 0, f"materialized document link contains class pattern {item}"))
    return result


def valid_repo_path(value: str, allow_glob: bool) -> bool:
    if not value or value == "(none)":
        return False
    if value.startswith(("/", "./", "../")) or re.match(r"^[A-Za-z]:", value):
        return False
    if "//" in value or "\\" in value or "/../" in f"/{value}/" or "/./" in f"/{value}/":
        return False
    if not allow_glob and any(ch in value for ch in "*?["):
        return False
    return True



def materialize(pattern: str, slug: str, ordinal: str = "001") -> str:
    return pattern.replace("{project_slug}", slug).replace("<project_slug>", slug).replace("<NNN>", ordinal)


def template_pattern(pattern: str, key: str | None = None, self_path: bool = False) -> str:
    value = pattern.replace("{project_slug}", "{{project_slug}}")
    if self_path and key == "SX":
        value = value.replace("<NNN>", "{{slice_ordinal}}")
    return value


def graph_inputs(index: IndexModel, key: str, kind: str) -> list[str]:
    return [edge["FROM_KEY"] for edge in index.edges if edge["TO_KEY"] == key and edge["EDGE_KIND"] == kind]


def graph_outputs(index: IndexModel, key: str, kind: str) -> list[str]:
    return [edge["TO_KEY"] for edge in index.edges if edge["FROM_KEY"] == key and edge["EDGE_KIND"] == kind]


def downstream_review_keys(index_or_path: IndexModel | Path, source_key: str) -> list[str]:
    index = parse_index(index_or_path) if isinstance(index_or_path, Path) else index_or_path
    source_targets = graph_outputs(index, source_key, "source_review")
    if source_targets:
        return source_targets
    seen: set[str] = set()
    queue: deque[str] = deque(graph_outputs(index, source_key, "normative"))
    while queue:
        key = queue.popleft()
        if key in seen:
            continue
        seen.add(key)
        queue.extend(graph_outputs(index, key, "normative"))
    return sorted(seen, key=lambda key: int(index.documents.get(key, {}).get("GENERATION_ORDER", "999")))


def expected_links(index: IndexModel, key: str, slug: str | None = None, expansions: dict[str, list[str]] | None = None) -> tuple[list[str], list[str], list[str]]:
    expansions = expansions or {}

    def resolve(target: str) -> list[str]:
        document = index.documents.get(target)
        if not document:
            return []
        pattern = document["MATERIALIZED_PATH"]
        if slug is not None and target in expansions:
            return list(expansions[target])
        if slug is None:
            return [template_pattern(pattern, target)]
        return [materialize(pattern, slug)]

    upstream: list[str] = []
    for source in graph_inputs(index, key, "normative"):
        upstream.extend(resolve(source))
    downstream: list[str] = []
    for target in graph_outputs(index, key, "normative"):
        downstream.extend(resolve(target))
    sources: list[str] = []
    for source in graph_inputs(index, key, "source_review"):
        sources.extend(resolve(source))
    return upstream, downstream, sources


def _brief_contract(path: Path, mode: str, framework_root: Path | None, contract: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[Issue]]:
    if contract is not None:
        return contract, []
    if mode == "template":
        return authoring_contract(path)
    root = framework_root or Path(__file__).resolve().parents[1]
    return authoring_contract(root / ".templates/codegen/PRODUCT_BRIEF.specset.template.md")


def validate_brief(path: Path, mode: str, contract: dict[str, Any] | None = None, framework_root: Path | None = None) -> list[Issue]:
    if not path.exists():
        return [Issue("BRIEF_MISSING", str(path), 0, "Product Brief is missing")]
    contract, c_issues = _brief_contract(path, mode, framework_root, contract)
    issues = list(c_issues)
    if contract is None:
        return issues

    supported_rules = {"heading_profile", "sections_populated", "forbid_specset_heading_syntax", "forbid_normative_rfc2119", "forbid_requirements_tables", "forbid_reserved_requirement_fields", "forbid_normative_document_links", "forbid_readiness_gates"}
    for rule in contract.get("readiness", []):
        if not isinstance(rule, dict) or rule.get("enforcement") == "semantic":
            continue
        kind = rule.get("rule")
        if kind not in supported_rules:
            issues.append(Issue("BRIEF_READINESS_UNSUPPORTED", str(path), 0, f"unsupported deterministic Product Brief readiness rule {kind}"))

    raw_text = path.read_text(encoding="utf-8")
    if mode == "materialized" and "specset-authoring" in raw_text:
        issues.append(Issue("AUTHORING_METADATA", str(path), 0, "materialized Product Brief contains framework-authoring metadata"))
    text = strip_authoring_metadata(raw_text) if mode == "template" else raw_text
    lines = text.splitlines()
    fenced = fence_state(lines)
    heading_rows = [(i + 1, line) for i, line in enumerate(lines) if not fenced[i] and line.startswith("#")]

    profile = next((rule for rule in contract.get("readiness", []) if isinstance(rule, dict) and rule.get("rule") == "heading_profile"), {})
    expected_h1 = profile.get("h1", "# Product Brief")
    h1 = [line for _, line in heading_rows if line.startswith("# ")]
    if h1 != [expected_h1]:
        issues.append(Issue("BRIEF_HEADINGS", str(path), 1, "Product Brief must contain the declared H1 exactly once"))
    h2 = [line for _, line in heading_rows if line.startswith("## ")]
    required_h2 = profile.get("required_h2_order", [])
    optional_h2 = profile.get("optional_trailing_h2", [])
    if h2[: len(required_h2)] != required_h2:
        issues.append(Issue("BRIEF_HEADINGS", str(path), 1, "required Product Brief H2 sequence is missing or reordered"))
    if h2[len(required_h2):] not in ([], optional_h2):
        issues.append(Issue("BRIEF_EXTRA_HEADING", str(path), 0, "unexpected Product Brief H2 section"))

    allowed_nested = set(profile.get("nested_levels", []))
    forbidden_nested = [token for token in profile.get("nested_forbidden_tokens", []) if isinstance(token, str)]
    seen_h2 = False
    for line_no, line in heading_rows:
        level = len(line) - len(line.lstrip("#"))
        if level == 2:
            seen_h2 = True
        if level >= 3:
            if level not in allowed_nested or any(token in line for token in forbidden_nested):
                issues.append(Issue("BRIEF_NESTED_HEADING", str(path), line_no, "nested Product Brief heading violates the declared profile"))
            if profile.get("nested_under_h2") and not seen_h2:
                issues.append(Issue("BRIEF_NESTED_HEADING", str(path), line_no, "nested Product Brief heading is not under an H2 section"))

    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        if line.startswith("#") and "||" in line:
            issues.append(Issue("BRIEF_SPECSET_HEADING", str(path), i + 1, "Product Brief uses Spec Set heading syntax"))
        if RFC.search(line):
            issues.append(Issue("BRIEF_NORMATIVE_KEYWORD", str(path), i + 1, "Product Brief contains an all-caps RFC 2119 keyword"))
        if any(token in line for token in REQ_HEADERS):
            issues.append(Issue("BRIEF_NORMATIVE_STRUCTURE", str(path), i + 1, "Product Brief contains reserved requirement structure"))
        if re.match(r"\|\s*(?:materialized_path|source_docs|upstream_docs|downstream_docs)\s*\|", line):
            issues.append(Issue("BRIEF_NORMATIVE_LINK", str(path), i + 1, "Product Brief contains normative document-link metadata"))
        if "REQUIRED_STATE" in line and "BLOCKS" in line:
            issues.append(Issue("BRIEF_READINESS_GATE", str(path), i + 1, "Product Brief contains a readiness gate"))

    if mode == "materialized":
        positions = [(i, line) for i, line in enumerate(lines) if not fenced[i] and line.startswith("## ")]
        for pos, (start, heading) in enumerate(positions):
            end = positions[pos + 1][0] if pos + 1 < len(positions) else len(lines)
            body = "\n".join(lines[start + 1:end]).strip()
            if not body or body in {"TBD", "TBD."}:
                issues.append(Issue("BRIEF_EMPTY_SECTION", str(path), start + 1, f"{heading} is not materialized"))
        for line_no, line in enumerate(lines, 1):
            if not fenced[line_no - 1] and _placeholder_line(line):
                issues.append(Issue("PLACEHOLDER", str(path), line_no, "materialized Product Brief contains a template placeholder"))

    return issues


__all__ = [name for name in globals() if not name.startswith("__")]
