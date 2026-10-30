#!/usr/bin/env python3
"""Validate declared deterministic Spec Set framework and project contracts."""
try:
    from ._specset_framework import *
except ImportError:
    from _specset_framework import *

try:
    from ._specset_manifest import *
except ImportError:
    from _specset_manifest import *


DEFAULT_FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]


def validate_project(workspace: Path, framework_root: Path, slug: str) -> list[Issue]:
    issues: list[Issue] = []
    if len(slug) > 63 or not SLUG.fullmatch(slug):
        return [Issue("PROJECT_SLUG", str(workspace), 0, f"invalid project slug {slug}")]
    codegen = framework_root / ".templates/codegen"
    index = parse_index(codegen / "SPEC_FILES_INDEX.specset.md")
    issues.extend(index.issues)
    contracts: dict[str, dict[str, Any]] = {}
    for key, row in index.documents.items():
        source = row["SPECSET_SOURCE"]
        if source.endswith(".specset.template.md"):
            contract, contract_issues = authoring_contract(codegen / source)
            issues.extend(contract_issues)
            if contract:
                contracts[key] = contract
    fixed_paths: dict[str, Path] = {}
    expansions: dict[str, list[str]] = {}
    for key, row in index.documents.items():
        pattern = row["MATERIALIZED_PATH"]
        if "<NNN>" in pattern:
            glob_pattern = materialize(pattern.replace("<NNN>", "???"), slug)
            matches = sorted(workspace.glob(glob_pattern))
            expansions[key] = [str(path.relative_to(workspace)).replace("\\", "/") for path in matches]
            if row.get("APPLICABILITY") == "one_or_more" and not matches:
                issues.append(Issue("REPEATED_DOCUMENT_MISSING", str(workspace / Path(materialize(pattern, slug)).parent), 0, f"no materialized {key} document exists"))
        else:
            path = workspace / materialize(pattern, slug)
            fixed_paths[key] = path
            if row.get("APPLICABILITY") in {"required", "required_with_not_applicable_mode"} and not path.exists():
                issues.append(Issue("PROJECT_FILE_MISSING", str(path), 0, f"missing {key}"))
    docs: dict[str, list[NormDoc]] = defaultdict(list)
    manifest_path: Path | None = None
    generated: list[Path] = []
    for key, row in index.documents.items():
        profile = row["PROFILE"]
        paths = [workspace / item for item in expansions.get(key, [])] if key in expansions else ([fixed_paths[key]] if key in fixed_paths else [])
        generated.extend(path for path in paths if path.exists())
        if profile == "product_brief":
            brief_path = paths[0] if paths else None
            if brief_path and brief_path.exists():
                issues.extend(validate_brief(brief_path, "materialized", contract=contracts.get(key), framework_root=framework_root))
            continue
        if profile == "project_manifest":
            manifest_path = paths[0] if paths else None
            continue
        if profile == "normative":
            for path in paths:
                if not path.exists():
                    continue
                parsed = parse_normative(path, "materialized")
                docs[key].append(parsed)
                issues.extend(parsed.issues)
    if manifest_path is None or not manifest_path.exists():
        issues.append(Issue("MANIFEST_MISSING", str(manifest_path or workspace / "docs"), 0, "project manifest is missing"))
        manifest_rows: list[dict[str, str]] = []
    else:
        manifest_rows, manifest_issues = parse_manifest(manifest_path, slug, index=index)
        issues.extend(manifest_issues)
    project_paths = {str(path.relative_to(workspace)).replace("\\", "/") for path in generated}
    for path in generated:
        if ".specset" in path.name:
            issues.append(Issue("GENERATED_SPECSET", str(path), 0, "generated filename contains .specset"))
        if slug not in path.name:
            issues.append(Issue("GENERATED_SLUG", str(path), 0, "generated filename lacks exact project slug"))
    for key, entries in docs.items():
        exp_up, exp_down, exp_src = expected_links(index, key, slug, expansions)
        for doc in entries:
            if split_list(doc.links.get("upstream_docs", "(none)")) != exp_up:
                issues.append(Issue("INDEX_MIRROR_UP", str(doc.path), 0, f"upstream_docs differs from {exp_up}"))
            if split_list(doc.links.get("downstream_docs", "(none)")) != exp_down:
                issues.append(Issue("INDEX_MIRROR_DOWN", str(doc.path), 0, f"downstream_docs differs from {exp_down}"))
            if split_list(doc.links.get("source_docs", "(none)")) != exp_src:
                issues.append(Issue("INDEX_MIRROR_SOURCE", str(doc.path), 0, f"source_docs differs from {exp_src}"))
    req_owner: dict[str, str] = {}
    req_graph: dict[str, list[str]] = {}
    for key, entries in docs.items():
        for doc in entries:
            for rid, req in doc.reqs.items():
                if rid in req_owner:
                    issues.append(Issue("REQ_GLOBAL_DUP", str(doc.path), req.line, f"requirement ID is not globally unique: {rid}"))
                req_owner[rid] = key
                req_graph[rid] = [] if req.derived == "(origin)" else req.derived.split(",")
    _validate_ancestry(index, docs, req_owner, req_graph, issues, template_mode=False)
    validate_global_refs(index, docs, req_owner, issues, project_paths)
    validate_typed_refs(index, docs, contracts, req_owner, issues, project_paths)
    for key, entries in docs.items():
        contract = contracts.get(key)
        if not contract:
            continue
        for doc in entries:
            validate_readiness(key, doc, contract, docs, contracts, issues)
    if manifest_path and manifest_path.exists() and manifest_rows:
        expected = _manifest_expected_rows(index, slug, expansions)
        actual_paths = {row.get("PATH", "") for row in manifest_rows}
        expected_paths = {data["path"] for data in expected.values()}
        if actual_paths != expected_paths:
            issues.append(Issue("MANIFEST_MIRROR", str(manifest_path), 0, f"manifest paths differ; missing={sorted(expected_paths-actual_paths)}, extra={sorted(actual_paths-expected_paths)}"))
        for row in manifest_rows:
            path = row.get("PATH", "")
            match = next((item for item in expected.values() if item["path"] == path), None)
            if not match:
                continue
            if row.get("ROLE") != match["role"]:
                issues.append(Issue("MANIFEST_ROLE", str(manifest_path), 0, f"manifest ROLE for {path} differs from registry"))
            if split_list(row.get("SOURCE_DOCS", "(none)")) != match["source"]:
                issues.append(Issue("MANIFEST_SOURCE", str(manifest_path), 0, f"manifest SOURCE_DOCS for {path} differs from graph"))
            if split_list(row.get("UPSTREAM_DOCS", "(none)")) != match["upstream"]:
                issues.append(Issue("MANIFEST_UPSTREAM", str(manifest_path), 0, f"manifest UPSTREAM_DOCS for {path} differs from graph"))
            if split_list(row.get("DOWNSTREAM_DOCS", "(none)")) != match["downstream"]:
                issues.append(Issue("MANIFEST_DOWNSTREAM", str(manifest_path), 0, f"manifest DOWNSTREAM_DOCS for {path} differs from graph"))
    docs_root = workspace / "docs"
    other_slugs: list[str] = []
    for other_manifest in docs_root.glob("SPEC_FILES_INDEX.*.md") if docs_root.exists() else []:
        match = re.fullmatch(r"SPEC_FILES_INDEX\.(.+)\.md", other_manifest.name)
        if match and match.group(1) != slug:
            other_slugs.append(match.group(1))
    for path in generated:
        content = path.read_text(encoding="utf-8")
        for other in other_slugs:
            if other in content:
                issues.append(Issue("CROSS_PROJECT_LEAK", str(path), 0, f"artifact references other project slug {other}"))
    return issues


def report(issues: Sequence[Issue], json_mode: bool) -> int:
    if json_mode:
        print(json.dumps({"valid": not issues, "validation_scope": "deterministic_only", "semantic_review": "not_performed", "issue_count": len(issues), "issues": [dataclasses.asdict(issue) for issue in issues]}, indent=2))
    elif issues:
        print(f"DETERMINISTIC INVALID: {len(issues)} issue(s); semantic review not performed")
        for issue in issues:
            print(f"{issue.path}:{issue.line}: {issue.code}: {issue.message}")
    else:
        print("DETERMINISTIC VALID: 0 issues; semantic review not performed")
    return 1 if issues else 0


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    framework_parser = sub.add_parser("framework")
    framework_parser.add_argument("--root", type=Path, default=DEFAULT_FRAMEWORK_ROOT)
    framework_parser.add_argument("--json", action="store_true")
    project = sub.add_parser("project")
    project.add_argument("--workspace", type=Path, required=True)
    project.add_argument("--framework-root", type=Path, default=DEFAULT_FRAMEWORK_ROOT)
    project.add_argument("--project-slug", required=True)
    project.add_argument("--json", action="store_true")
    document_parser = sub.add_parser("document")
    document_parser.add_argument("path", type=Path)
    document_parser.add_argument("--mode", choices=["template", "materialized"], default="materialized")
    document_parser.add_argument("--json", action="store_true")
    brief = sub.add_parser("product-brief")
    brief.add_argument("path", type=Path)
    brief.add_argument("--mode", choices=["template", "materialized"], default="materialized")
    brief.add_argument("--framework-root", type=Path, default=DEFAULT_FRAMEWORK_ROOT)
    brief.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "framework":
        return report(validate_framework(args.root), args.json)
    if args.command == "project":
        return report(validate_project(args.workspace, args.framework_root, args.project_slug), args.json)
    if args.command == "document":
        return report(parse_normative(args.path, args.mode).issues, args.json)
    if args.command == "product-brief":
        return report(validate_brief(args.path, args.mode, framework_root=args.framework_root), args.json)
    raise AssertionError(args.command)


if __name__ == "__main__":
    sys.exit(main())
