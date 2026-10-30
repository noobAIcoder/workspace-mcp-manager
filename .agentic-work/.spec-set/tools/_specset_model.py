#!/usr/bin/env python3
"""Shared Spec Set parser/model primitives."""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import shlex
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Sequence

RFC = re.compile(r"\b(?:MUST(?:\s+NOT)?|SHALL(?:\s+NOT)?|SHOULD(?:\s+NOT)?|REQUIRED|RECOMMENDED|MAY|OPTIONAL)\b")
HEADING = re.compile(r"^(#{1,5}) ([A-Z0-9-]+) \|\| ([^\n]+)$")
ITEM_CODE = re.compile(r"^[A-Z]{2,5}_[A-Z0-9_]{2,20}$")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"\{\{[a-z][a-z0-9_]*\}\}")
REQ_HEADERS = ["REQUIREMENT_ID", "REQUIREMENT_TEXT", "IMPLEMENTATION_NOTES", "DERIVED_FROM"]
RESERVED = set(REQ_HEADERS)
CONTRACT_START = "<!-- specset-authoring:start"
CONTRACT_END = "specset-authoring:end -->"
CONTRACT_RE = re.compile(r"<!-- specset-authoring:start\s*\n(.*?)\nspecset-authoring:end -->", re.DOTALL)
AUTHORING_COMMENT_RE = re.compile(r"<!-- specset-authoring:(?!start)[\s\S]*?-->")
CONTRACT_KEYS = {
    "document_key": str,
    "purpose": str,
    "authoritative_inputs": list,
    "non_goals": list,
    "readiness": list,
    "applicability": list,
    "typed_fields": list,
    "change_areas": list,
    "materialization": dict,
    "semantic_review": list,
}
LEGACY_NAMES = {
    "PRODUCT_SPEC.template.md", "DOMAIN_SPEC.template.md", "TECHNICAL_SPEC.template.md",
    "UI_SPEC.template.md", "CODEBASE_SPEC.template.md", "SLICES_MAP.template.md",
    "SLICE_XXX.template.md", "INDEX.md", "ID_RULESET.md", "meta-plan.template.md",
    "work-plan.template.md", "slice-preflight.template.md", "slice-work-plan.template.md",
    "general-meta-plan.template.md", "general-work-plan.template.md",
    "implementation-gap-report.template.md", "implementation-plan.template.md",
}
STALE_ACTIVE_LITERALS = (
    ".AG" + "ENTS/",
    "AGENTS." + "specset.md",
    "PRODUCT_BRIEF_" + "PROFILE.specset.md",
    ".templates/" + "ruleset/",
    "scripts/" + "validate_specs.py",
    "tools/" + "validate_specset.py",
)


@dataclasses.dataclass(frozen=True)
class Issue:
    code: str
    path: str
    line: int
    message: str


@dataclasses.dataclass
class HeadingNode:
    level: int
    sid: str
    title: str
    line: int
    parent: str | None
    has_child: bool = False


@dataclasses.dataclass
class MdTable:
    headers: list[str]
    rows: list[list[str]]
    line: int
    section: str | None


@dataclasses.dataclass
class RequirementRow:
    rid: str
    text: str
    notes: str
    derived: str
    line: int


@dataclasses.dataclass
class NormDoc:
    path: Path
    mode: str
    headings: list[HeadingNode]
    tables: list[MdTable]
    reqs: dict[str, RequirementRow]
    links: dict[str, str]
    issues: list[Issue]


@dataclasses.dataclass
class IndexModel:
    path: Path
    framework: dict[str, dict[str, str]]
    documents: dict[str, dict[str, str]]
    edges: list[dict[str, str]]
    namespaces: dict[str, dict[str, str]]
    global_fields: list[dict[str, str]]
    manifest_fields: dict[str, dict[str, str]]
    issues: list[Issue]


def cells(line: str) -> list[str]:
    """Parse one Markdown pipe row, decoding only the declared escaped-pipe cell syntax."""
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    result: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value) and value[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "|":
            result.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    result.append("".join(current).strip())
    return result


def separator(row: Sequence[str]) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in row)


def fence_state(lines: Sequence[str]) -> list[bool]:
    inside = False
    marker: str | None = None
    states: list[bool] = []
    for line in lines:
        states.append(inside)
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            current = stripped[:3]
            if not inside:
                inside, marker = True, current
            elif marker == current:
                inside, marker = False, None
    return states


def valid_sid(sid: str, level: int) -> bool:
    parts = sid.split("-")
    if len(parts) != level or not re.fullmatch(r"[A-Z]{2}", parts[0]):
        return False
    if level >= 2 and not re.fullmatch(r"[A-Z]{3}", parts[1]):
        return False
    return all(re.fullmatch(r"[A-Z0-9]{4,5}", part) for part in parts[2:])


def valid_rid(rid: str, group: str, mode: str) -> bool:
    del mode
    prefix = group + "-"
    return rid.startswith(prefix) and bool(ITEM_CODE.fullmatch(rid[len(prefix):]))


def split_list(value: str) -> list[str]:
    return [] if value == "(none)" else value.split(",")


def ref_values(raw: str) -> list[str]:
    return split_list(raw)


def table_rows(path: Path, headers: Sequence[str]) -> list[list[str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    fenced = fence_state(lines)
    i = 0
    while i < len(lines):
        if fenced[i] or not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        raw: list[str] = []
        while i < len(lines) and not fenced[i] and lines[i].lstrip().startswith("|"):
            raw.append(lines[i])
            i += 1
        if len(raw) < 2:
            continue
        parsed = [cells(row) for row in raw]
        if parsed[0] == list(headers) and separator(parsed[1]):
            return parsed[2:]
    return []


def rows_as_dicts(path: Path, headers: Sequence[str]) -> list[dict[str, str]]:
    return [dict(zip(headers, row)) for row in table_rows(path, headers) if len(row) == len(headers)]


def parse_index(path: Path) -> IndexModel:
    issues: list[Issue] = []
    if not path.exists():
        return IndexModel(path, {}, {}, [], {}, [], {}, [Issue("INDEX_MISSING", str(path), 0, "Spec Set index is missing")])

    specset_headers = ["SPECSET_KEY", "ACTIVE_PATH", "CLASS", "VALIDATION_PROFILE", "STATUS"]
    doc_headers = ["DOCUMENT_KEY", "SPECSET_SOURCE", "MATERIALIZED_PATH", "ROLE", "PROFILE", "GENERATION_ORDER", "APPLICABILITY"]
    edge_headers = ["EDGE_ID", "FROM_KEY", "TO_KEY", "EDGE_KIND", "CHANGE_EFFECT", "NOTES"]
    ns_headers = ["NAMESPACE", "VALUE_FORM", "OWNER_LOCATION", "DECLARATION", "NOTES"]
    field_headers = ["FIELD_CONTEXT", "FIELD_NAME", "VALUE_KIND", "TARGET_NAMESPACE", "EMPTY_SENTINEL", "NOTES"]
    manifest_headers = ["SECTION", "REQUIRED_FIELDS", "NOTES"]

    specset_rows = rows_as_dicts(path, specset_headers)
    doc_rows = rows_as_dicts(path, doc_headers)
    edges = rows_as_dicts(path, edge_headers)
    ns_rows = rows_as_dicts(path, ns_headers)
    fields = rows_as_dicts(path, field_headers)
    manifest_rows = rows_as_dicts(path, manifest_headers)

    if not specset_rows:
        issues.append(Issue("INDEX_SPECSET_REGISTRY", str(path), 0, "Spec Set file registry is missing"))
    if not doc_rows:
        issues.append(Issue("INDEX_DOCUMENT_REGISTRY", str(path), 0, "document registry is missing"))
    if not edges:
        issues.append(Issue("INDEX_EDGE_REGISTRY", str(path), 0, "document edge registry is missing"))
    if not ns_rows:
        issues.append(Issue("INDEX_NAMESPACE_REGISTRY", str(path), 0, "namespace registry is missing"))
    if not manifest_rows:
        issues.append(Issue("INDEX_MANIFEST_REGISTRY", str(path), 0, "manifest field registry is missing"))

    framework = {row["SPECSET_KEY"]: row for row in specset_rows}
    documents = {row["DOCUMENT_KEY"]: row for row in doc_rows}
    namespaces = {row["NAMESPACE"]: row for row in ns_rows}
    manifest_fields = {row["SECTION"]: row for row in manifest_rows}

    if len(framework) != len(specset_rows):
        issues.append(Issue("INDEX_SPECSET_DUP", str(path), 0, "Spec Set registry contains duplicate keys"))
    if len(documents) != len(doc_rows):
        issues.append(Issue("INDEX_DOCUMENT_DUP", str(path), 0, "document registry contains duplicate keys"))
    if len(namespaces) != len(ns_rows):
        issues.append(Issue("INDEX_NAMESPACE_DUP", str(path), 0, "namespace registry contains duplicate names"))
    if len(manifest_fields) != len(manifest_rows):
        issues.append(Issue("INDEX_MANIFEST_DUP", str(path), 0, "manifest registry contains duplicate sections"))

    edge_ids = [row["EDGE_ID"] for row in edges]
    if len(set(edge_ids)) != len(edge_ids):
        issues.append(Issue("INDEX_EDGE_DUP", str(path), 0, "document edge registry contains duplicate edge IDs"))
    for edge in edges:
        if edge["FROM_KEY"] not in documents and edge["FROM_KEY"] != "IX":
            issues.append(Issue("INDEX_EDGE_KEY", str(path), 0, f"unknown FROM_KEY {edge['FROM_KEY']}"))
        if edge["TO_KEY"] not in documents:
            issues.append(Issue("INDEX_EDGE_KEY", str(path), 0, f"unknown TO_KEY {edge['TO_KEY']}"))

    for namespace, meta in namespaces.items():
        declaration = meta.get("DECLARATION", "")
        valid = declaration in {"generated_paths", "requirements:*"} or declaration.startswith(("requirements:", "registry:", "union:", "syntax:"))
        if not valid:
            issues.append(Issue("INDEX_NAMESPACE_DECLARATION", str(path), 0, f"namespace {namespace} has invalid declaration {declaration!r}"))
        if declaration.startswith("registry:"):
            parts = declaration.split(":", 3)
            if len(parts) != 4 or parts[1] not in documents:
                issues.append(Issue("INDEX_NAMESPACE_DECLARATION", str(path), 0, f"namespace {namespace} has invalid registry declaration {declaration!r}"))
        if declaration.startswith("union:"):
            for member in declaration.split(":", 1)[1].split(","):
                if member not in namespaces:
                    issues.append(Issue("INDEX_NAMESPACE_DECLARATION", str(path), 0, f"namespace {namespace} union references unknown namespace {member}"))

    for field in fields:
        if field["TARGET_NAMESPACE"] not in namespaces:
            issues.append(Issue("INDEX_FIELD_NAMESPACE", str(path), 0, f"global field {field['FIELD_CONTEXT']}.{field['FIELD_NAME']} uses unknown namespace {field['TARGET_NAMESPACE']}"))
        if field["VALUE_KIND"] not in {"scalar", "list", "list_or_origin"}:
            issues.append(Issue("INDEX_FIELD_KIND", str(path), 0, f"global field {field['FIELD_CONTEXT']}.{field['FIELD_NAME']} has unsupported value kind {field['VALUE_KIND']}"))
        if field["EMPTY_SENTINEL"] not in {"allowed", "forbidden"}:
            issues.append(Issue("INDEX_FIELD_EMPTY", str(path), 0, f"global field {field['FIELD_CONTEXT']}.{field['FIELD_NAME']} has unsupported empty policy {field['EMPTY_SENTINEL']}"))

    for section, row in manifest_fields.items():
        raw = row.get("REQUIRED_FIELDS", "")
        values = raw.split(",") if raw else []
        if not values or any(not value or value.strip() != value for value in values):
            issues.append(Issue("INDEX_MANIFEST_FIELDS", str(path), 0, f"manifest section {section} has invalid REQUIRED_FIELDS {raw!r}"))

    return IndexModel(path, framework, documents, edges, namespaces, fields, manifest_fields, issues)


def document_registry(index: Path) -> dict[str, dict[str, str]]:
    return parse_index(index).documents


def authoring_contract(path: Path) -> tuple[dict[str, Any] | None, list[Issue]]:
    issues: list[Issue] = []
    if not path.exists():
        return None, [Issue("CONTRACT_FILE_MISSING", str(path), 0, "template is missing")]
    text = path.read_text(encoding="utf-8")
    if text.count(CONTRACT_START) != 1 or text.count(CONTRACT_END) != 1:
        return None, [Issue("CONTRACT_COUNT", str(path), 0, "template must contain exactly one specset-authoring region")]
    match = CONTRACT_RE.search(text)
    if not match:
        return None, [Issue("CONTRACT_MALFORMED", str(path), 0, "specset-authoring region is malformed")]
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return None, [Issue("CONTRACT_JSON", str(path), exc.lineno, f"invalid contract JSON: {exc.msg}")]
    if not isinstance(value, dict):
        return None, [Issue("CONTRACT_SCHEMA", str(path), 0, "authoring contract must be a JSON object")]
    if value.get("schema_version") != 1:
        issues.append(Issue("CONTRACT_VERSION", str(path), 0, "schema_version must equal 1"))
        return None, issues
    for key, expected_type in CONTRACT_KEYS.items():
        if key not in value:
            issues.append(Issue("CONTRACT_FIELD", str(path), 0, f"missing required contract field {key}"))
        elif not isinstance(value[key], expected_type):
            issues.append(Issue("CONTRACT_FIELD", str(path), 0, f"contract field {key} must be {expected_type.__name__}"))

    materialization = value.get("materialization")
    if isinstance(materialization, dict):
        for key in ("strip_authoring_region", "strip_authoring_comments", "template_placeholders_allowed", "materialized_placeholders_allowed"):
            if not isinstance(materialization.get(key), bool):
                issues.append(Issue("CONTRACT_MATERIALIZATION", str(path), 0, f"materialization.{key} must be boolean"))

    # Preserve all schema diagnostics while making v1 consumers safe to continue
    # independent checks. Unsupported schema versions return above and are never
    # interpreted as v1.
    normalized = dict(value)
    for key, expected_type in CONTRACT_KEYS.items():
        if expected_type is list and not isinstance(normalized.get(key), list):
            normalized[key] = []
        elif expected_type is dict and not isinstance(normalized.get(key), dict):
            normalized[key] = {}
        elif expected_type is str and not isinstance(normalized.get(key), str):
            normalized[key] = ""
    if "typed_discriminators" in normalized and not isinstance(normalized.get("typed_discriminators"), list):
        issues.append(Issue("CONTRACT_DISCRIMINATOR", str(path), 0, "typed_discriminators must be a list"))
        normalized["typed_discriminators"] = []
    value = normalized

    for rule in value.get("readiness", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("rule"), str) or not rule.get("rule"):
            issues.append(Issue("CONTRACT_READINESS", str(path), 0, "readiness entries must be objects with a non-empty rule"))
            continue
        enforcement = rule.get("enforcement")
        deterministic = isinstance(enforcement, str) and bool(re.fullmatch(r"deterministic(?:_[a-z][a-z0-9_]*)?", enforcement))
        if enforcement is not None and enforcement != "semantic" and not deterministic:
            issues.append(Issue("CONTRACT_READINESS", str(path), 0, f"readiness rule {rule.get('rule')} has invalid enforcement {enforcement!r}"))

    seen_applicability: set[str] = set()
    for entry in value.get("applicability", []):
        if not isinstance(entry, dict):
            issues.append(Issue("CONTRACT_APPLICABILITY", str(path), 0, "applicability entries must be objects"))
            continue
        section = entry.get("section")
        mode = entry.get("mode")
        if not isinstance(section, str) or not section:
            issues.append(Issue("CONTRACT_APPLICABILITY", str(path), 0, "applicability entry requires a non-empty section"))
        if mode not in {"required", "optional_omittable", "required_with_not_applicable_branch"}:
            issues.append(Issue("CONTRACT_APPLICABILITY", str(path), 0, f"unsupported applicability mode {mode!r}"))
        if mode == "required_with_not_applicable_branch" and not isinstance(entry.get("not_applicable_requirement_group"), str):
            issues.append(Issue("CONTRACT_APPLICABILITY", str(path), 0, f"{section} requires not_applicable_requirement_group"))
        if isinstance(section, str):
            if section in seen_applicability:
                issues.append(Issue("CONTRACT_APPLICABILITY_DUP", str(path), 0, f"duplicate applicability declaration for {section}"))
            seen_applicability.add(section)

    for spec in value.get("typed_discriminators", []):
        if (not isinstance(spec, dict) or not all(isinstance(spec.get(key), str) and spec.get(key) for key in ("context", "selector_field", "value_field")) or not isinstance(spec.get("mapping"), dict)):
            issues.append(Issue("CONTRACT_DISCRIMINATOR", str(path), 0, "typed_discriminators entries require context, selector_field, value_field, and mapping"))

    seen_fields: set[tuple[str, str]] = set()
    for entry in value.get("typed_fields", []):
        if not isinstance(entry, dict):
            issues.append(Issue("CONTRACT_TYPED_FIELD", str(path), 0, "typed_fields entries must be objects"))
            continue
        for key in ("context", "field", "kind", "namespace", "empty"):
            if not isinstance(entry.get(key), str) or not entry.get(key):
                issues.append(Issue("CONTRACT_TYPED_FIELD", str(path), 0, f"typed field is missing string {key}"))
        if entry.get("kind") not in {"scalar", "list"}:
            issues.append(Issue("CONTRACT_TYPED_FIELD", str(path), 0, f"unsupported typed-field kind {entry.get('kind')!r}"))
        if entry.get("empty") not in {"allowed", "forbidden"}:
            issues.append(Issue("CONTRACT_TYPED_FIELD", str(path), 0, f"unsupported empty policy {entry.get('empty')!r}"))
        identity = (str(entry.get("context", "")), str(entry.get("field", "")))
        if identity in seen_fields:
            issues.append(Issue("CONTRACT_TYPED_DUP", str(path), 0, f"duplicate typed-field binding {identity[0]}.{identity[1]}"))
        seen_fields.add(identity)

    value["typed_fields"] = [
        entry
        for entry in value.get("typed_fields", [])
        if isinstance(entry, dict)
        and all(isinstance(entry.get(key), str) and entry.get(key) for key in ("context", "field", "kind", "namespace", "empty"))
        and entry.get("kind") in {"scalar", "list"}
        and entry.get("empty") in {"allowed", "forbidden"}
    ]
    value["typed_discriminators"] = [
        spec
        for spec in value.get("typed_discriminators", [])
        if isinstance(spec, dict)
        and all(isinstance(spec.get(key), str) and spec.get(key) for key in ("context", "selector_field", "value_field"))
        and isinstance(spec.get("mapping"), dict)
    ]

    return value, issues


def strip_authoring_metadata(text: str) -> str:
    text = CONTRACT_RE.sub("", text)
    return AUTHORING_COMMENT_RE.sub("", text)


def materialize_template_text(text: str) -> str:
    return strip_authoring_metadata(text)


__all__ = [name for name in globals() if not name.startswith("__")]
