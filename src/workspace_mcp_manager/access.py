from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from .domain import ACCESS_ALIAS_RE, AccessFolder, DesiredInstance
from .errors import ErrorCode, ManagerError, config_error
from .generation import OWNER_PREFIX, GeneratedBundle, GeneratedResource, ResourceKind
from .paths import ManagerPaths
from .registry import InstanceRegistry


APPLIED_SCHEMA_VERSION = 1
ACCESS_RESOURCE_PREFIXES = ("access-ro-", "access-rw-")
ACCESS_RESOURCE_IDS = frozenset({"access-root", "access-owner", "access-ignore"})


def _normalized_alias(alias: str) -> str:
    if not isinstance(alias, str) or not alias or "\x00" in alias or "\n" in alias or "\r" in alias:
        raise config_error("access alias must be a non-empty single-line string")
    if not ACCESS_ALIAS_RE.fullmatch(alias):
        raise config_error("access alias must use letters, digits, and single hyphens or underscores")
    return alias.lower().replace("-", "_")


def _is_access_resource_id(resource_id: str) -> bool:
    return resource_id in ACCESS_RESOURCE_IDS or resource_id.startswith(ACCESS_RESOURCE_PREFIXES)


class AccessManager:
    """Host-side desired-state mutation for external-folder access."""

    def __init__(self, paths: ManagerPaths, registry: InstanceRegistry) -> None:
        self.paths = paths
        self.registry = registry

    @contextmanager
    def _instance_lock(self, instance_id: str):
        lock_root = self.paths.state_root / "locks"
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = lock_root / f"{instance_id}.lock"
        with path.open("a+", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _payload(desired: DesiredInstance, *, action: str = "list", apply_required: bool = False) -> dict[str, Any]:
        access_root = Path(desired.workspace_path) / ".workspace-mcp-access"
        entries = [
            {
                "alias": folder.alias,
                "mode": mode,
                "path": folder.path,
                "mount_path": str(access_root / folder.alias),
            }
            for mode, folders in (
                ("ro", desired.access.read_only),
                ("rw", desired.access.read_write),
            )
            for folder in folders
        ]
        entries.sort(key=lambda item: (_normalized_alias(item["alias"]), item["mode"], item["path"]))
        return {
            "ok": True,
            "action": action,
            "instance_id": desired.instance_id.value,
            "desired_fingerprint": desired.fingerprint(),
            "access_root": str(access_root),
            "entries": entries,
            "apply_required": apply_required,
        }

    def list(self, instance_id: str) -> dict[str, Any]:
        return self._payload(self.registry.get(instance_id))

    def add(self, instance_id: str, *, mode: str, alias: str, path: str) -> dict[str, Any]:
        if mode not in {"ro", "rw"}:
            raise ManagerError(ErrorCode.CONFIG_INVALID, f"unsupported access mode: {mode}")
        with self._instance_lock(instance_id):
            desired = self.registry.get(instance_id)
            raw = desired.to_dict()
            key = "read_only" if mode == "ro" else "read_write"
            raw["access"][key].append({"alias": alias, "path": path})
            updated = DesiredInstance.from_dict(raw)
            self.registry.update(updated)
        return self._payload(updated, action=f"add-{mode}", apply_required=True)

    def remove(self, instance_id: str, *, alias: str) -> dict[str, Any]:
        normalized = _normalized_alias(alias)
        with self._instance_lock(instance_id):
            desired = self.registry.get(instance_id)
            raw = desired.to_dict()
            removed: dict[str, str] | None = None
            for key, mode in (("read_only", "ro"), ("read_write", "rw")):
                retained: list[dict[str, str]] = []
                for item in raw["access"][key]:
                    folder = AccessFolder.from_dict(item, f"access.{key}")
                    if folder.normalized_alias == normalized:
                        removed = {"alias": folder.alias, "mode": mode, "path": folder.path}
                        continue
                    retained.append(item)
                raw["access"][key] = retained
            if removed is None:
                raise config_error("access alias is not configured", alias=alias)
            updated = DesiredInstance.from_dict(raw)
            self.registry.update(updated)
        result = self._payload(updated, action="remove", apply_required=True)
        result["removed"] = removed
        return result


def _reconstruct_access_resource(
    desired: DesiredInstance,
    entry: Mapping[str, Any],
) -> GeneratedResource:
    resource_id = entry.get("resource_id")
    path_value = entry.get("path")
    fingerprint = entry.get("fingerprint")
    if not isinstance(resource_id, str) or not _is_access_resource_id(resource_id):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "invalid applied access resource identity")
    if not isinstance(path_value, str) or not isinstance(fingerprint, str):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "malformed applied access resource evidence")

    iid = desired.instance_id.value
    marker = f"{OWNER_PREFIX}{iid}"
    access_root = Path(desired.workspace_path) / ".workspace-mcp-access"
    owner = access_root / ".owner"

    if resource_id == "access-root":
        resource = GeneratedResource(
            resource_id,
            ResourceKind.DIRECTORY,
            str(access_root),
            0o700,
            ownership_token=marker,
            ownership_marker_path=str(owner),
        )
    elif resource_id == "access-owner":
        resource = GeneratedResource(
            resource_id,
            ResourceKind.FILE,
            str(owner),
            0o600,
            content=f"{OWNER_PREFIX}{iid}\n",
            ownership_token=marker,
        )
    elif resource_id == "access-ignore":
        resource = GeneratedResource(
            resource_id,
            ResourceKind.FILE,
            str(access_root / ".gitignore"),
            0o600,
            content="*\n",
            ownership_token=marker,
            ownership_marker_path=str(owner),
        )
    else:
        prefix = next((item for item in ACCESS_RESOURCE_PREFIXES if resource_id.startswith(item)), None)
        if prefix is None:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "unsupported applied access resource")
        alias = resource_id[len(prefix) :]
        _normalized_alias(alias)
        resource = GeneratedResource(
            resource_id,
            ResourceKind.DIRECTORY,
            str(access_root / alias),
            0o700,
            ownership_token=marker,
            ownership_marker_path=str(owner),
        )

    if resource.path != path_value:
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "applied access resource path is outside the current managed namespace",
            {"resource_id": resource_id, "path": path_value},
        )
    if resource.fingerprint() != fingerprint:
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "applied access resource fingerprint is invalid",
            {"resource_id": resource_id},
        )
    return resource


def stale_applied_access_resources(
    paths: ManagerPaths,
    desired: DesiredInstance,
    bundle: GeneratedBundle,
) -> tuple[GeneratedResource, ...]:
    """Return previously applied access resources no longer present in desired state."""

    applied_path = paths.state_root / "applied" / f"{desired.instance_id.value}.json"
    try:
        text = applied_path.read_text(encoding="utf-8", errors="strict")
    except FileNotFoundError:
        return ()
    except (OSError, UnicodeError) as exc:
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "cannot safely read applied ownership state for access cleanup",
            {"path": str(applied_path)},
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "applied ownership state is not valid JSON",
            {"path": str(applied_path)},
        ) from exc
    if not isinstance(payload, Mapping):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "applied ownership state must be an object")
    if payload.get("schema_version") != APPLIED_SCHEMA_VERSION:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "unsupported applied ownership schema")
    if payload.get("instance_id") != desired.instance_id.value:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "applied ownership instance identity mismatch")
    owned = payload.get("owned_resources")
    if not isinstance(owned, list):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "applied ownership resources are malformed")

    current_ids = {resource.resource_id for resource in bundle.resources}
    result: list[GeneratedResource] = []
    for entry in owned:
        if not isinstance(entry, Mapping):
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "applied ownership resource is malformed")
        resource_id = entry.get("resource_id")
        if not isinstance(resource_id, str) or not _is_access_resource_id(resource_id):
            continue
        if resource_id in current_ids:
            continue
        result.append(_reconstruct_access_resource(desired, entry))
    result.sort(key=lambda resource: resource.resource_id)
    return tuple(result)
