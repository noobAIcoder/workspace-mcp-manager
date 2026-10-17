"""Read-only structural validation of the integration-owned repo-info schemas.

The owning YAML templates supply shape and version identifiers. This consumer
checks their declared mechanics; it does not verify observations or acceptance.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required; install tools/requirements.txt in the validation environment.") from exc

TEMPLATES = {
    "repo_info": ("REPO_INFO.yaml", "repo_info.template.yaml"),
    "runtime": ("runtime.yaml", "runtime.template.yaml"),
    "structure": ("structure.yaml", "structure.template.yaml"),
    "validation": ("validation.yaml", "validation.template.yaml"),
    "hazards": ("hazards.yaml", "hazards.template.yaml"),
}
Catalog = dict[str, tuple[Path, dict[str, Any]]]
PLACEHOLDER = re.compile(r"<[^>]+>")
BOOL_FIELDS = {"monorepo", "observed_required", "secret"}
REFERENCE_FIELDS = {"policy_refs", "requirement_refs", "handling_refs"}


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys rather than silently dropping values."""


def _unique_mapping(loader: UniqueLoader, node: Any) -> dict:
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            if key in result:
                raise ValueError(f"duplicate YAML mapping key: {key!r}")
            result[key] = loader.construct_object(value_node, deep=True)
        except TypeError as exc:
            raise ValueError("YAML mapping keys must be hashable scalars") from exc
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected one YAML mapping")
    return value


def load_catalog(integration_root: Path) -> Catalog:
    catalog = {}
    for key, (_, name) in TEMPLATES.items():
        path = integration_root / ".templates/repo-info" / name
        document = read_yaml(path)
        if not isinstance(document.get("schema"), str) or not isinstance(document.get("provenance"), dict):
            raise ValueError(f"{path}: incomplete owning template")
        catalog[key] = (path, document)
    return catalog


def _known(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not PLACEHOLDER.search(value)


def _walk(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield child_path, child
            yield from _walk(child, child_path)


def _shape(value: Any, exemplar: Any, path: str, issues: list[Issue]) -> None:
    def add(code: str, message: str) -> None:
        issues.append(Issue(code, path, message))

    if isinstance(exemplar, dict):
        if not isinstance(value, dict):
            add("FIELD_TYPE", "expected mapping")
            return
        for key in exemplar.keys() - value.keys():
            add("MISSING_FIELD", f"required field {key!r} is missing")
        for key in value.keys() - exemplar.keys():
            add("UNKNOWN_FIELD", f"undeclared field {key!r}; no implicit schema extension")
        for key in exemplar.keys() & value.keys():
            _shape(value[key], exemplar[key], f"{path}.{key}", issues)
    elif isinstance(exemplar, list):
        if not isinstance(value, list):
            add("FIELD_TYPE", "expected list")
            return
        seen = set()
        for index, row in enumerate(value):
            _shape(row, exemplar[0] if exemplar else "", f"{path}[{index}]", issues)
            if isinstance(row, dict) and _known(row.get("id")):
                if row["id"] in seen:
                    add("DUPLICATE_ID", f"duplicate registry ID {row['id']!r}")
                seen.add(row["id"])
    elif exemplar is None:
        field = path.rsplit(".", 1)[-1]
        expected = bool if field in BOOL_FIELDS else int if field == "port" else str
        if value is not None and type(value) is not expected:
            add("FIELD_TYPE", f"expected null or {expected.__name__}")
    elif type(value) is not type(exemplar):
        add("FIELD_TYPE", f"expected {type(exemplar).__name__}")


def _repository_relative(value: str) -> bool:
    """Check lexical containment on both path dialects, without resolving files."""
    if not value.strip() or "\0" in value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized):
        return False  # Includes drive-qualified paths and external URI locators.
    depth = 0
    for part in normalized.split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return False
        elif part not in {"", "."}:
            depth += 1
    return True


def _validate_repository_paths(data: dict[str, Any], key: str) -> list[Issue]:
    # Only schema-designated path fields: descriptions and commands are opaque.
    fields = {
        "runtime": {"commands[].cwd", "env.files[].path"},
        "structure": {"root.workspace_files[]", "paths[].path", "entrypoints[].path"},
        "validation": {"commands[].cwd"},
    }.get(key, set())
    values = [(path, value) for path, value in _walk(data)
              if re.sub(r"\[\d+\]", "[]", path) in fields]
    values.extend((f"authority_refs[{index}].source", row["source"])
                  for index, row in enumerate(data["authority_refs"])
                  if row["kind"] == "repository")
    return [Issue("REPOSITORY_PATH", f"{key}.{path}",
                  "expected a repository-root-relative path with no absolute prefix, URI scheme, or traversal above root")
            for path, value in values
            if value is not None and not PLACEHOLDER.search(value) and not _repository_relative(value)]


def validate_document(data: dict[str, Any], key: str, catalog: Catalog) -> list[Issue]:
    expected = catalog[key][1]
    if data.get("schema") != expected["schema"]:
        return [Issue("UNSUPPORTED_SCHEMA", key, f"found {data.get('schema')!r}; supported {expected['schema']!r}; preserve original, no migration performed")]
    issues: list[Issue] = []
    _shape(data, expected, key, issues)
    if issues:
        return issues  # Do not inspect malformed nested payloads as valid data.
    issues.extend(_validate_repository_paths(data, key))
    for field in ("role", "audience"):
        if data[field] != expected[field]:
            issues.append(Issue("FIELD_VALUE", f"{key}.{field}", f"expected {expected[field]!r}"))
    enums = {
        "provenance.status": {"unverified", "verified"},
        "evidence_kind": {"deterministic", "semantic", "integration", "runtime_qualification", "manual_inspection"},
        "matcher.type": {"exact", "prefix", "glob", "regex", "signature", "condition"},
        "class": {"source", "tests", "config", "scripts", "docs", "data", "generated", "legacy", "vendor"},
        "result": {"pass", "fail", "not_run", "unknown"},
    }
    for path, value in _walk(data):
        for suffix, allowed in enums.items():
            if (path == suffix or path.endswith("." + suffix)) and value is not None and not PLACEHOLDER.search(str(value)) and value not in allowed:
                issues.append(Issue("FIELD_VALUE", f"{key}.{path}", f"expected one of {sorted(allowed)}"))
    authority_ids = {row["id"] for row in data["authority_refs"] if _known(row["id"])}
    for row in data["authority_refs"]:
        if not PLACEHOLDER.search(row["kind"]) and row["kind"] not in {"repository", "external"}:
            issues.append(Issue("FIELD_VALUE", key + ".authority_refs", "kind must be repository or external"))
    for path, value in _walk(data):
        field = path.rsplit(".", 1)[-1]
        refs = value if field in REFERENCE_FIELDS else [value] if field == "severity_scale_ref" else []
        for ref in refs:
            if _known(ref) and ref not in authority_ids:
                issues.append(Issue("AUTHORITY_REFERENCE", f"{key}.{path}", f"unknown authority_refs ID {ref!r}"))
    provenance = data["provenance"]
    if provenance["status"] == "verified":
        for field in ("repository_commit", "verified_at", "environment", "evidence_source"):
            if not _known(provenance[field]):
                issues.append(Issue("PROVENANCE_INCOMPLETE", f"{key}.provenance.{field}", "verified provenance requires a concrete value"))
        commit = provenance["repository_commit"]
        if _known(commit) and not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            issues.append(Issue("PROVENANCE_FORMAT", key, "repository_commit must be a full 40-character commit SHA"))
        stamp = provenance["verified_at"]
        if _known(stamp):
            try:
                if not stamp.endswith("Z"):
                    raise ValueError("UTC Z suffix required")
                datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                issues.append(Issue("PROVENANCE_FORMAT", key, "verified_at must be a valid YYYY-MM-DDTHH:MM:SSZ timestamp"))
        for path, value in _walk(data):
            if isinstance(value, str) and PLACEHOLDER.search(value):
                issues.append(Issue("VERIFIED_PLACEHOLDER", f"{key}.{path}", "placeholder cannot be recorded as verified"))
    if key == "repo_info":
        for shard, row in data["shards"].items():
            if row["file"] != TEMPLATES[shard][0]:
                issues.append(Issue("SHARD_PATH", shard, f"expected installation-relative {TEMPLATES[shard][0]!r}"))
            if row["schema"] != catalog[shard][1]["schema"]:
                issues.append(Issue("SHARD_SCHEMA", shard, f"unsupported shard schema {row['schema']!r}; supported {catalog[shard][1]['schema']!r}"))
        for row in data["summaries"]:
            if _known(row["shard"]) and row["shard"] not in data["shards"]:
                issues.append(Issue("SHARD_REFERENCE", key, f"unknown summary shard {row['shard']!r}"))
    if key == "validation":
        command_ids = {row["id"] for row in data["commands"] if _known(row["id"])}
        for row in data["known_baselines"]:
            for command in row["command_ids"]:
                if _known(command) and command not in command_ids:
                    issues.append(Issue("COMMAND_REFERENCE", key, f"unknown validation command {command!r}"))
    return issues


def validate_installed(root: Path, catalog: Catalog, required_shards: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    loaded: dict[str, dict] = {}
    invalid_registrations: set[str] = set()
    queue = ["repo_info", *dict.fromkeys(required_shards)]
    for key in queue:
        if key in invalid_registrations:
            continue  # Its index diagnostic already identifies the unavailable context.
        if key not in catalog:
            issues.append(Issue("SHARD_REFERENCE", key, "unknown requested shard"))
            continue
        path = root / TEMPLATES[key][0]
        if not path.is_file():
            issues.append(Issue("CONTEXT_MISSING", str(path), "required context missing; dependent scope only"))
            if key == "repo_info":
                return issues
            continue
        try:
            data = read_yaml(path)
        except ValueError as exc:
            issues.append(Issue("CONTEXT_PARSE", str(path), str(exc)))
            if key == "repo_info":
                return issues
            continue
        found = validate_document(data, key, catalog)
        issues.extend(found)
        if key == "repo_info" and found:
            registration_codes = {"SHARD_PATH", "SHARD_SCHEMA"}
            invalid_registrations.update(issue.path for issue in found if issue.code in registration_codes)
            if any(issue.code not in registration_codes for issue in found):
                return issues
            # A bad shard registration does not hide checks of independent shards.
        if not found:
            loaded[key] = data
            if key == "structure" and any(_known(row["regenerate_command_id"]) for row in data["paths"]):
                if "runtime" not in queue:
                    queue.append("runtime")  # This explicit cross-reference requires its owning context.
    if "structure" in loaded and "runtime" in loaded:
        command_ids = {row["id"] for row in loaded["runtime"]["commands"] if _known(row["id"])}
        for row in loaded["structure"]["paths"]:
            ref = row["regenerate_command_id"]
            if _known(ref) and ref not in command_ids:
                issues.append(Issue("COMMAND_REFERENCE", "structure", f"unknown runtime command {ref!r}"))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("templates", "installed"))
    parser.add_argument("--integration-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--facts-root", type=Path, help="Installation containing facts; defaults to integration root")
    parser.add_argument("--shard", action="append", default=[], choices=tuple(k for k in TEMPLATES if k != "repo_info"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.integration_root)
        issues = [issue for key, (_, doc) in catalog.items() for issue in validate_document(doc, key, catalog)]
        if not issues and args.mode == "installed":
            issues = validate_installed(args.facts_root or args.integration_root, catalog, args.shard)
    except ValueError as exc:
        issues = [Issue("TEMPLATE_PARSE", str(args.integration_root), str(exc))]
    if args.json:
        print(json.dumps({"valid": not issues, "validation_scope": "structural_only", "facts_verified": False, "issues": [asdict(i) for i in issues]}, indent=2))
    else:
        for issue in issues:
            print(f"{issue.code}: {issue.path}: {issue.message}")
        print("STRUCTURE VALID; facts not verified" if not issues else "STRUCTURE INVALID; originals unchanged")
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
