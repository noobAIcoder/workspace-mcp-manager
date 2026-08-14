from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any, Mapping

from .development_environment import github_cli_helper_path
from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError
from .generation import OWNER_PREFIX, GeneratedBundle, GeneratedResource, ResourceKind
from .paths import ManagerPaths


APPLIED_SCHEMA_VERSION = 1
GUARD_RESOURCE_IDS = frozenset({"admission-guard-unit", "admission-guard-timer"})
DEVELOPMENT_RESOURCE_IDS = frozenset({"github-cli-helper", "ssh-agent-unit"})


def _expected_guard_identity(paths: ManagerPaths, desired: DesiredInstance, resource_id: str) -> tuple[Path, str]:
    iid = desired.instance_id.value
    if resource_id == "admission-guard-unit":
        unit = f"workspace-mcp-admission-guard-{iid}.service"
    elif resource_id == "admission-guard-timer":
        unit = f"workspace-mcp-admission-guard-{iid}.timer"
    else:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "invalid applied admission-guard resource identity")
    return paths.user_unit_dir / unit, unit


def _load_applied(paths: ManagerPaths, desired: DesiredInstance) -> list[Mapping[str, Any]]:
    path = paths.state_root / "applied" / f"{desired.instance_id.value}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "cannot safely read applied ownership state for admission-guard cleanup",
            {"path": str(path)},
        ) from exc
    if not isinstance(payload, Mapping):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "applied ownership state must be an object")
    if payload.get("schema_version") != APPLIED_SCHEMA_VERSION:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "unsupported applied ownership schema")
    if payload.get("instance_id") != desired.instance_id.value:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "applied ownership instance identity mismatch")
    owned = payload.get("owned_resources")
    if not isinstance(owned, list) or not all(isinstance(item, Mapping) for item in owned):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "applied ownership resources are malformed")
    return list(owned)


def stale_applied_guard_resources(
    paths: ManagerPaths,
    desired: DesiredInstance,
    bundle: GeneratedBundle,
) -> tuple[GeneratedResource, ...]:
    """Reconstruct obsolete manager-owned guard units from applied evidence.

    Guard unit content can vary with the configured cadence. Applied state stores
    the resource fingerprint but not the old content, so reconstruction uses the
    currently installed unit text and accepts it only when that exact text/mode
    reproduces the applied fingerprint and carries the instance owner marker.
    """

    current_ids = {resource.resource_id for resource in bundle.resources}
    marker = f"{OWNER_PREFIX}{desired.instance_id.value}"
    result: list[GeneratedResource] = []
    for entry in _load_applied(paths, desired):
        resource_id = entry.get("resource_id")
        if resource_id not in GUARD_RESOURCE_IDS or resource_id in current_ids:
            continue
        expected_path, unit_name = _expected_guard_identity(paths, desired, str(resource_id))
        if entry.get("kind") != ResourceKind.SYSTEMD_UNIT.value:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied admission-guard resource kind is invalid",
                {"resource_id": resource_id},
            )
        if entry.get("path") != str(expected_path) or not isinstance(entry.get("fingerprint"), str):
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied admission-guard resource is outside the managed namespace",
                {"resource_id": resource_id},
            )
        try:
            metadata = expected_path.lstat()
        except FileNotFoundError:
            # Nothing remains on disk to remove. A successful apply will replace
            # applied ownership state with the current desired bundle.
            continue
        except OSError as exc:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "cannot inspect applied admission-guard resource",
                {"resource_id": resource_id, "path": str(expected_path)},
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied admission-guard resource has unexpected filesystem type",
                {"resource_id": resource_id, "path": str(expected_path)},
            )
        try:
            content = expected_path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "cannot read applied admission-guard resource",
                {"resource_id": resource_id, "path": str(expected_path)},
            ) from exc
        if marker not in content:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied admission-guard ownership marker is absent",
                {"resource_id": resource_id},
            )
        resource = GeneratedResource(
            str(resource_id),
            ResourceKind.SYSTEMD_UNIT,
            str(expected_path),
            stat.S_IMODE(metadata.st_mode),
            content=content,
            ownership_token=marker,
            unit_name=unit_name,
            enable_when_present=resource_id == "admission-guard-timer",
            active_when_running=resource_id == "admission-guard-timer",
        )
        if resource.mode != 0o600 or resource.fingerprint() != entry["fingerprint"]:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "installed admission-guard resource does not match applied ownership evidence",
                {"resource_id": resource_id},
            )
        result.append(resource)
    result.sort(key=lambda resource: (0 if resource.resource_id == "admission-guard-timer" else 1, resource.resource_id))
    return tuple(result)


def stale_applied_development_resources(
    paths: ManagerPaths,
    desired: DesiredInstance,
    bundle: GeneratedBundle,
) -> tuple[GeneratedResource, ...]:
    current_ids = {resource.resource_id for resource in bundle.resources}
    result: list[GeneratedResource] = []
    for entry in _load_applied(paths, desired):
        resource_id = entry.get("resource_id")
        if resource_id not in DEVELOPMENT_RESOURCE_IDS or resource_id in current_ids:
            continue
        helper_resource = resource_id == "github-cli-helper"
        if helper_resource:
            expected_path = github_cli_helper_path(paths, desired)
            kind = ResourceKind.FILE
            expected_mode = 0o755
            unit_name = None
        else:
            unit_name = f"workspace-mcp-ssh-agent-{desired.instance_id.value}.service"
            expected_path = paths.user_unit_dir / unit_name
            kind = ResourceKind.SYSTEMD_UNIT
            expected_mode = 0o600
        if entry.get("kind") != kind.value or entry.get("path") != str(expected_path):
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied development resource identity is invalid",
                {"resource_id": resource_id},
            )
        try:
            metadata = expected_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "cannot inspect applied development resource",
                {"resource_id": resource_id},
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied development resource has unexpected filesystem type",
                {"resource_id": resource_id},
            )
        try:
            content = expected_path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "cannot read applied development resource",
                {"resource_id": resource_id},
            ) from exc
        marker = f"{OWNER_PREFIX}{desired.instance_id.value}"
        if marker not in content:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied development ownership marker is absent",
                {"resource_id": resource_id},
            )
        resource = GeneratedResource(
            str(resource_id),
            kind,
            str(expected_path),
            stat.S_IMODE(metadata.st_mode),
            content=content,
            ownership_token=marker,
            unit_name=unit_name,
            enable_when_present=not helper_resource,
            active_when_running=not helper_resource,
        )
        fingerprint = entry.get("fingerprint")
        if resource.mode != expected_mode or not isinstance(fingerprint, str):
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "applied development resource metadata is invalid",
                {"resource_id": resource_id},
            )
        if resource.fingerprint() != fingerprint:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "installed development resource fingerprint differs from applied evidence",
                {"resource_id": resource_id},
            )
        result.append(resource)
    return tuple(result)
