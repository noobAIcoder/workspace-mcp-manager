from __future__ import annotations

import datetime
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from ._specset_framework import *
except ImportError:
    from _specset_framework import *


def _manifest_required_fields(index: IndexModel | None, section: str, fallback: list[str]) -> list[str]:
    if index is None:
        return fallback
    row = index.manifest_fields.get(section)
    if not row:
        return fallback
    return row.get("REQUIRED_FIELDS", "").split(",")


def _manifest_slice_ordinal(index: IndexModel, key: str, slug: str, path: str) -> str | None:
    meta = index.documents.get(key)
    if not meta:
        return None
    pattern = meta.get("MATERIALIZED_PATH", "")
    if "<NNN>" not in pattern:
        return None
    rendered = re.escape(pattern)
    rendered = rendered.replace(re.escape("{project_slug}"), re.escape(slug))
    rendered = rendered.replace(re.escape("<project_slug>"), re.escape(slug))
    rendered = rendered.replace(re.escape("<NNN>"), r"(?P<ordinal>\d{3})")
    match = re.fullmatch(rendered, path)
    return match.group("ordinal") if match else None


def _manifest_key_expected_path(index: IndexModel, document_key: str, slug: str) -> str | None:
    if document_key in index.documents:
        meta = index.documents[document_key]
        if "<NNN>" in meta.get("MATERIALIZED_PATH", ""):
            return None
        return materialize(meta["MATERIALIZED_PATH"], slug)
    if ":" not in document_key:
        return None
    base, ordinal = document_key.rsplit(":", 1)
    meta = index.documents.get(base)
    if not meta or "<NNN>" not in meta.get("MATERIALIZED_PATH", "") or not re.fullmatch(r"\d{3}", ordinal):
        return None
    return materialize(meta["MATERIALIZED_PATH"], slug, ordinal)


def parse_manifest(path: Path, slug: str, index: IndexModel | None = None) -> tuple[list[dict[str, str]], list[Issue]]:
    issues: list[Issue] = []
    if not path.exists():
        return [], [Issue("MANIFEST_MISSING", str(path), 0, "project manifest is missing")]
    text = path.read_text(encoding="utf-8")
    if not text.startswith(f"# Spec Set Manifest: {slug}\n"):
        issues.append(Issue("MANIFEST_TITLE", str(path), 1, "manifest title does not match project slug"))
    if any(token in text for token in REQ_HEADERS) or RFC.search(text):
        issues.append(Issue("MANIFEST_NORMATIVE", str(path), 0, "project manifest contains normative content"))

    project_required = _manifest_required_fields(index, "MF-PRJ", ["project_slug", "status", "generated_at"])
    project_rows = [row for row in table_rows(path, ["FIELD", "VALUE"]) if len(row) == 2]
    occurrences: dict[str, list[str]] = defaultdict(list)
    for field, value in project_rows:
        occurrences[field].append(value)
    for field in project_required:
        values = occurrences.get(field, [])
        if not values or not values[0]:
            issues.append(Issue("MANIFEST_FIELD", str(path), 0, f"MF-PRJ is missing required field {field}"))
        if len(values) > 1:
            issues.append(Issue("MANIFEST_FIELD_DUP", str(path), 0, f"MF-PRJ field {field} occurs {len(values)} times"))
    project_values = {field: values[0] for field, values in occurrences.items() if values}
    if project_values.get("project_slug") and project_values["project_slug"] != slug:
        issues.append(Issue("MANIFEST_SLUG", str(path), 0, f"manifest project_slug {project_values['project_slug']!r} does not match {slug!r}"))
    status = project_values.get("status")
    if status and not re.fullmatch(r"[a-z][a-z0-9_]*", status):
        issues.append(Issue("MANIFEST_STATUS", str(path), 0, f"invalid manifest status {status!r}"))
    generated_at = project_values.get("generated_at")
    if generated_at:
        timestamp_valid = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", generated_at))
        if timestamp_valid:
            try:
                datetime.datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                timestamp_valid = False
        if not timestamp_valid:
            issues.append(Issue("MANIFEST_TIMESTAMP", str(path), 0, f"invalid generated_at {generated_at!r}"))

    file_required = _manifest_required_fields(index, "MF-FIL", ["document_key", "role", "path", "status", "source_docs", "upstream_docs", "downstream_docs"])
    headers = [field.upper() for field in file_required]
    raw_rows = [row for row in table_rows(path, headers) if len(row) == len(headers)]
    rows = [dict(zip(headers, row)) for row in raw_rows]
    if not rows:
        issues.append(Issue("MANIFEST_TABLE", str(path), 0, "manifest registry table with required fields is missing"))

    key_counts: dict[str, int] = defaultdict(int)
    path_counts: dict[str, int] = defaultdict(int)
    row_counts: dict[tuple[str, ...], int] = defaultdict(int)
    for raw, row in zip(raw_rows, rows):
        row_counts[tuple(raw)] += 1
        key_counts[row.get("DOCUMENT_KEY", "")] += 1
        path_counts[row.get("PATH", "")] += 1
        for header in headers:
            if row.get(header, "") == "":
                issues.append(Issue("MANIFEST_FIELD", str(path), 0, f"MF-FIL row for {row.get('PATH', '(unknown)')} has empty {header}"))
        row_status = row.get("STATUS")
        if row_status and not re.fullmatch(r"[a-z][a-z0-9_]*", row_status):
            issues.append(Issue("MANIFEST_STATUS", str(path), 0, f"invalid MF-FIL status {row_status!r}"))
        if index is not None:
            key = row.get("DOCUMENT_KEY", "")
            expected_path = _manifest_key_expected_path(index, key, slug)
            if expected_path is None:
                issues.append(Issue("MANIFEST_DOCUMENT_KEY", str(path), 0, f"unregistered or malformed manifest document key {key!r}"))
            elif row.get("PATH") != expected_path:
                issues.append(Issue("MANIFEST_KEY_PATH", str(path), 0, f"manifest key {key} must identify {expected_path}, not {row.get('PATH')!r}"))
    for key, count in key_counts.items():
        if key and count > 1:
            issues.append(Issue("MANIFEST_KEY_DUP", str(path), 0, f"manifest DOCUMENT_KEY {key} occurs {count} times"))
    for artifact_path, count in path_counts.items():
        if artifact_path and count > 1:
            issues.append(Issue("MANIFEST_PATH_DUP", str(path), 0, f"manifest PATH {artifact_path} occurs {count} times"))
    for raw, count in row_counts.items():
        if count > 1:
            issues.append(Issue("MANIFEST_ROW_DUP", str(path), 0, f"manifest artifact row is duplicated {count} times: {raw[0] if raw else '(empty)'}"))
    return rows, issues


def _manifest_expected_rows(index: IndexModel, slug: str, expansions: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, row in index.documents.items():
        paths = expansions.get(key) or [materialize(row["MATERIALIZED_PATH"], slug)]
        repeated = "<NNN>" in row.get("MATERIALIZED_PATH", "")
        for path in paths:
            upstream, downstream, source = expected_links(index, key, slug, expansions)
            if repeated:
                ordinal = _manifest_slice_ordinal(index, key, slug, path)
                identity = f"{key}:{ordinal}" if ordinal is not None else f"{key}:???"
            else:
                identity = key
            result[identity] = {"document_key": identity, "role": row["ROLE"], "path": path, "source": source, "upstream": upstream, "downstream": downstream}
    return result


__all__ = [name for name in globals() if not name.startswith("__")]
