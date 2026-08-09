from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .errors import ErrorCode, ManagerError, config_error


CONFIG_VERSION = 1
INSTANCE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ACCESS_ALIAS_RE = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$")
TUNNEL_ID_RE = re.compile(r"^tunnel_[A-Za-z0-9]+$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PermissionMode(StrEnum):
    SAFE = "safe"
    TRUSTED = "trusted"
    DANGEROUS = "dangerous"


class ShellEnvInherit(StrEnum):
    CORE = "core"
    ALL = "all"
    NONE = "none"


class DeploymentTarget(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"


class RuntimeTarget(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


def _strict_object(
    value: Any,
    *,
    field: str,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise config_error(f"{field} must be an object")
    optional = optional or set()
    keys = set(value)
    unknown = sorted(keys - required - optional)
    missing = sorted(required - keys)
    if unknown:
        raise config_error(f"{field} contains unknown fields", fields=unknown)
    if missing:
        raise config_error(f"{field} is missing required fields", fields=missing)
    return value


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise config_error(f"{field} must be a non-empty string")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise config_error(f"{field} contains an unsupported control character")
    return value


def _absolute_path(value: Any, field: str) -> str:
    raw = _nonempty_string(value, field)
    if not PurePosixPath(raw).is_absolute():
        raise config_error(f"{field} must be an absolute POSIX path")
    return os.path.normpath(raw)


def _port(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise config_error(f"{field} must be an integer between 1 and 65535")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise config_error(f"{field} must be a positive integer")
    return value


def _enum(enum_type: type[StrEnum], value: Any, field: str) -> StrEnum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = [item.value for item in enum_type]
        raise config_error(f"{field} must be one of {allowed}") from exc


@dataclass(frozen=True, slots=True)
class InstanceId:
    value: str

    def __post_init__(self) -> None:
        if not INSTANCE_ID_RE.fullmatch(self.value) or len(self.value) > 63:
            raise config_error(
                "instance_id must use lowercase letters, digits, and single hyphens",
                value=self.value,
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class LifecycleIntent:
    deployment: DeploymentTarget
    runtime: RuntimeTarget

    @classmethod
    def from_dict(cls, value: Any) -> "LifecycleIntent":
        data = _strict_object(
            value,
            field="lifecycle",
            required={"deployment", "runtime"},
        )
        deployment = _enum(DeploymentTarget, data["deployment"], "lifecycle.deployment")
        runtime = _enum(RuntimeTarget, data["runtime"], "lifecycle.runtime")
        if deployment is DeploymentTarget.ABSENT and runtime is RuntimeTarget.RUNNING:
            raise config_error("lifecycle.runtime cannot be running when deployment is absent")
        return cls(deployment=deployment, runtime=runtime)

    def to_dict(self) -> dict[str, str]:
        return {"deployment": self.deployment.value, "runtime": self.runtime.value}


@dataclass(frozen=True, slots=True)
class McpConfig:
    binary: str
    host: str
    port: int
    permission_mode: PermissionMode
    shell_env_inherit: ShellEnvInherit
    exec_path: str
    external_roots: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "McpConfig":
        data = _strict_object(
            value,
            field="mcp",
            required={
                "binary",
                "host",
                "port",
                "permission_mode",
                "shell_env_inherit",
                "exec_path",
                "external_roots",
            },
        )
        exec_path = _nonempty_string(data["exec_path"], "mcp.exec_path")
        for index, item in enumerate(exec_path.split(os.pathsep)):
            if not item or not PurePosixPath(item).is_absolute():
                raise config_error(
                    "mcp.exec_path must contain only non-empty absolute path entries",
                    index=index,
                )
        roots_raw = data["external_roots"]
        if not isinstance(roots_raw, list):
            raise config_error("mcp.external_roots must be an array")
        roots = tuple(_absolute_path(item, f"mcp.external_roots[{index}]") for index, item in enumerate(roots_raw))
        if len(set(roots)) != len(roots):
            raise config_error("mcp.external_roots contains duplicate paths")
        return cls(
            binary=_nonempty_string(data["binary"], "mcp.binary"),
            host=_nonempty_string(data["host"], "mcp.host"),
            port=_port(data["port"], "mcp.port"),
            permission_mode=_enum(PermissionMode, data["permission_mode"], "mcp.permission_mode"),
            shell_env_inherit=_enum(ShellEnvInherit, data["shell_env_inherit"], "mcp.shell_env_inherit"),
            exec_path=exec_path,
            external_roots=roots,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary": self.binary,
            "host": self.host,
            "port": self.port,
            "permission_mode": self.permission_mode.value,
            "shell_env_inherit": self.shell_env_inherit.value,
            "exec_path": self.exec_path,
            "external_roots": list(self.external_roots),
        }


@dataclass(frozen=True, slots=True)
class TunnelConfig:
    binary: str
    id: str
    profile: str
    health_host: str
    health_port: int
    env_file: str

    @classmethod
    def from_dict(cls, value: Any) -> "TunnelConfig":
        data = _strict_object(
            value,
            field="tunnel",
            required={"binary", "id", "profile", "health_host", "health_port", "env_file"},
        )
        tunnel_id = _nonempty_string(data["id"], "tunnel.id")
        if not TUNNEL_ID_RE.fullmatch(tunnel_id):
            raise config_error("tunnel.id must start with tunnel_ and contain only letters/digits after the prefix")
        profile = _nonempty_string(data["profile"], "tunnel.profile")
        if not PROFILE_RE.fullmatch(profile):
            raise config_error("tunnel.profile contains unsupported characters")
        return cls(
            binary=_nonempty_string(data["binary"], "tunnel.binary"),
            id=tunnel_id,
            profile=profile,
            health_host=_nonempty_string(data["health_host"], "tunnel.health_host"),
            health_port=_port(data["health_port"], "tunnel.health_port"),
            env_file=_absolute_path(data["env_file"], "tunnel.env_file"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary": self.binary,
            "id": self.id,
            "profile": self.profile,
            "health_host": self.health_host,
            "health_port": self.health_port,
            "env_file": self.env_file,
        }


@dataclass(frozen=True, slots=True)
class AccessFolder:
    alias: str
    path: str

    @classmethod
    def from_dict(cls, value: Any, field: str) -> "AccessFolder":
        data = _strict_object(value, field=field, required={"alias", "path"})
        alias = _nonempty_string(data["alias"], f"{field}.alias")
        if not ACCESS_ALIAS_RE.fullmatch(alias):
            raise config_error(
                f"{field}.alias must use letters, digits, and single hyphens or underscores"
            )
        return cls(alias=alias, path=_absolute_path(data["path"], f"{field}.path"))

    @property
    def normalized_alias(self) -> str:
        return self.alias.lower().replace("-", "_")

    def to_dict(self) -> dict[str, str]:
        return {"alias": self.alias, "path": self.path}


@dataclass(frozen=True, slots=True)
class AccessConfig:
    read_only: tuple[AccessFolder, ...]
    read_write: tuple[AccessFolder, ...]

    @classmethod
    def from_dict(cls, value: Any, *, workspace_path: str) -> "AccessConfig":
        data = _strict_object(value, field="access", required={"read_only", "read_write"})
        groups: dict[str, tuple[AccessFolder, ...]] = {}
        for mode in ("read_only", "read_write"):
            raw = data[mode]
            if not isinstance(raw, list):
                raise config_error(f"access.{mode} must be an array")
            groups[mode] = tuple(
                AccessFolder.from_dict(item, f"access.{mode}[{index}]")
                for index, item in enumerate(raw)
            )
        all_folders = groups["read_only"] + groups["read_write"]
        aliases = [folder.normalized_alias for folder in all_folders]
        if len(set(aliases)) != len(aliases):
            raise config_error("access folder aliases collide after normalization")
        paths = [folder.path for folder in all_folders]
        if len(set(paths)) != len(paths):
            raise config_error("access folder source paths must be unique")
        workspace = PurePosixPath(workspace_path)
        for folder in all_folders:
            source = PurePosixPath(folder.path)
            if source == workspace or workspace in source.parents:
                raise config_error(
                    "access folder source is already inside workspace_path",
                    alias=folder.alias,
                    path=folder.path,
                )
        return cls(read_only=groups["read_only"], read_write=groups["read_write"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_only": [folder.to_dict() for folder in self.read_only],
            "read_write": [folder.to_dict() for folder in self.read_write],
        }


@dataclass(frozen=True, slots=True)
class GithubConfig:
    config_dir: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "GithubConfig":
        data = _strict_object(value, field="github", required={"config_dir"})
        if data["config_dir"] is None:
            return cls(config_dir=None)
        return cls(config_dir=_absolute_path(data["config_dir"], "github.config_dir"))

    def to_dict(self) -> dict[str, str | None]:
        return {"config_dir": self.config_dir}


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    admission_guard_enabled: bool
    guard_interval_seconds: int
    recovery_cooldown_seconds: int

    @classmethod
    def from_dict(cls, value: Any) -> "RecoveryPolicy":
        data = _strict_object(
            value,
            field="recovery",
            required={"admission_guard_enabled", "guard_interval_seconds", "recovery_cooldown_seconds"},
        )
        enabled = data["admission_guard_enabled"]
        if not isinstance(enabled, bool):
            raise config_error("recovery.admission_guard_enabled must be boolean")
        return cls(
            admission_guard_enabled=enabled,
            guard_interval_seconds=_positive_int(data["guard_interval_seconds"], "recovery.guard_interval_seconds"),
            recovery_cooldown_seconds=_positive_int(
                data["recovery_cooldown_seconds"], "recovery.recovery_cooldown_seconds"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission_guard_enabled": self.admission_guard_enabled,
            "guard_interval_seconds": self.guard_interval_seconds,
            "recovery_cooldown_seconds": self.recovery_cooldown_seconds,
        }


@dataclass(frozen=True, slots=True)
class DesiredInstance:
    config_version: int
    instance_id: InstanceId
    workspace_path: str
    lifecycle: LifecycleIntent
    mcp: McpConfig
    tunnel: TunnelConfig
    access: AccessConfig
    github: GithubConfig
    recovery: RecoveryPolicy

    @classmethod
    def from_dict(cls, value: Any) -> "DesiredInstance":
        data = _strict_object(
            value,
            field="instance",
            required={
                "config_version",
                "instance_id",
                "workspace_path",
                "lifecycle",
                "mcp",
                "tunnel",
                "access",
                "github",
                "recovery",
            },
        )
        version = data["config_version"]
        if version != CONFIG_VERSION:
            raise ManagerError(
                ErrorCode.CONFIG_VERSION_UNSUPPORTED,
                f"unsupported config_version: {version!r}",
                {"supported": [CONFIG_VERSION]},
            )
        workspace_path = _absolute_path(data["workspace_path"], "workspace_path")
        mcp = McpConfig.from_dict(data["mcp"])
        tunnel = TunnelConfig.from_dict(data["tunnel"])
        if mcp.host == tunnel.health_host and mcp.port == tunnel.health_port:
            raise config_error("mcp.port and tunnel.health_port collide on the same host")
        return cls(
            config_version=CONFIG_VERSION,
            instance_id=InstanceId(_nonempty_string(data["instance_id"], "instance_id")),
            workspace_path=workspace_path,
            lifecycle=LifecycleIntent.from_dict(data["lifecycle"]),
            mcp=mcp,
            tunnel=tunnel,
            access=AccessConfig.from_dict(data["access"], workspace_path=workspace_path),
            github=GithubConfig.from_dict(data["github"]),
            recovery=RecoveryPolicy.from_dict(data["recovery"]),
        )

    @classmethod
    def from_json(cls, text: str) -> "DesiredInstance":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise config_error("instance declaration is not valid JSON", line=exc.lineno, column=exc.colno) from exc
        return cls.from_dict(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "instance_id": self.instance_id.value,
            "workspace_path": self.workspace_path,
            "lifecycle": self.lifecycle.to_dict(),
            "mcp": self.mcp.to_dict(),
            "tunnel": self.tunnel.to_dict(),
            "access": self.access.to_dict(),
            "github": self.github.to_dict(),
            "recovery": self.recovery.to_dict(),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ObservedResource:
    resource_type: str
    identity: str
    exists: bool
    attributes: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ObservedInstanceState:
    instance_id: str
    captured_at: str
    resources: tuple[ObservedResource, ...]


@dataclass(frozen=True, slots=True)
class OwnedResource:
    resource_type: str
    identity: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class AppliedOwnershipState:
    schema_version: int
    instance_id: str
    desired_fingerprint: str
    owned_resources: tuple[OwnedResource, ...]
    last_successful_apply_at: str | None

