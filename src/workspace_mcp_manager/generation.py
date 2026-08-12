from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .domain import DesiredInstance
from .errors import config_error
from .paths import ManagerPaths


RESOURCE_SCHEMA_VERSION = 1
OWNER_PREFIX = "workspace-mcp-manager-instance="


class ResourceKind(StrEnum):
    DIRECTORY = "directory"
    FILE = "file"
    SYSTEMD_UNIT = "systemd-unit"
    TUNNEL_PROFILE = "tunnel-profile"


@dataclass(frozen=True, slots=True)
class GeneratedResource:
    resource_id: str
    kind: ResourceKind
    path: str
    mode: int
    content: str | None = None
    ownership_token: str | None = None
    ownership_marker_path: str | None = None
    unit_name: str | None = None
    enable_when_present: bool = False
    active_when_running: bool = False
    remove_when_absent: bool = True

    def fingerprint(self) -> str:
        payload = {
            "schema_version": RESOURCE_SCHEMA_VERSION,
            "resource_id": self.resource_id,
            "kind": self.kind.value,
            "path": self.path,
            "mode": self.mode,
            "content": self.content,
            "ownership_token": self.ownership_token,
            "ownership_marker_path": self.ownership_marker_path,
            "unit_name": self.unit_name,
            "enable_when_present": self.enable_when_present,
            "active_when_running": self.active_when_running,
            "remove_when_absent": self.remove_when_absent,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "resource_id": self.resource_id,
            "kind": self.kind.value,
            "path": self.path,
            "mode": oct(self.mode),
            "fingerprint": self.fingerprint(),
            "unit_name": self.unit_name,
            "enable_when_present": self.enable_when_present,
            "active_when_running": self.active_when_running,
            "remove_when_absent": self.remove_when_absent,
            "ownership_token": self.ownership_token,
            "ownership_marker_path": self.ownership_marker_path,
        }
        if include_content:
            result["content"] = self.content
        return result


@dataclass(frozen=True, slots=True)
class GeneratedBundle:
    instance_id: str
    desired_fingerprint: str
    resources: tuple[GeneratedResource, ...]

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        return {
            "schema_version": RESOURCE_SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "desired_fingerprint": self.desired_fingerprint,
            "resources": [item.to_dict(include_content=include_content) for item in self.resources],
        }


def systemd_quote(value: str) -> str:
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def systemd_path(value: str) -> str:
    if not value.startswith("/"):
        raise config_error(f"systemd path must be absolute: {value}")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise config_error(f"systemd path contains whitespace or control characters: {value}")
    if any(character in {'"', "'", "\\"} for character in value):
        raise config_error(f"systemd path contains unsupported quoting characters: {value}")
    return value.replace("%", "%%")


def _owner_marker(instance_id: str) -> str:
    return f"# {OWNER_PREFIX}{instance_id}\n"


class ResourceGenerator:
    def __init__(self, paths: ManagerPaths, *, curl_binary: str = "/usr/bin/curl") -> None:
        self.paths = paths
        self.curl_binary = curl_binary

    def _state_dir(self, desired: DesiredInstance) -> Path:
        return self.paths.state_root / "instances" / desired.instance_id.value

    def _profile_path(self, desired: DesiredInstance) -> Path:
        return self.paths.tunnel_profile_dir / f"{desired.tunnel.profile}.yaml"

    def _unit_path(self, name: str) -> Path:
        return self.paths.user_unit_dir / name

    def _runtime_command(self, desired: DesiredInstance, action: str) -> str:
        values = (
            str(self.paths.manager_executable),
            "--registry-dir",
            str(self.paths.registry_dir),
            "_runtime",
            action,
            desired.instance_id.value,
        )
        return " ".join(systemd_quote(value) for value in values)

    def _render_profile(self, desired: DesiredInstance) -> str:
        marker = _owner_marker(desired.instance_id.value)
        mcp_url = f"http://{desired.mcp.host}:{desired.mcp.port}/mcp"
        return (
            marker
            + "config_version: 1\n"
            + "control_plane:\n"
            + '  base_url: "https://api.openai.com"\n'
            + f"  tunnel_id: {json.dumps(desired.tunnel.id)}\n"
            + '  api_key: "env:CONTROL_PLANE_API_KEY"\n'
            + "health:\n"
            + f"  listen_addr: {json.dumps(f'{desired.tunnel.health_host}:{desired.tunnel.health_port}')}\n"
            + "admin_ui:\n"
            + "  open_browser: false\n"
            + "log:\n"
            + "  level: info\n"
            + "  format: json\n"
            + "mcp:\n"
            + "  server_urls:\n"
            + "    - channel: main\n"
            + f"      url: {json.dumps(mcp_url)}\n"
        )

    def _effective_external_roots(self, desired: DesiredInstance) -> tuple[str, ...]:
        roots = list(desired.mcp.external_roots)
        if desired.github.config_dir and desired.github.config_dir not in roots:
            roots.append(desired.github.config_dir)
        return tuple(roots)

    def _render_mcp_unit(self, desired: DesiredInstance) -> str:
        iid = desired.instance_id.value
        state_dir = self._state_dir(desired)
        exec_start = " ".join(
            systemd_quote(value)
            for value in (
                desired.mcp.binary,
                "--workspace",
                desired.workspace_path,
                "--host",
                desired.mcp.host,
                "--port",
                str(desired.mcp.port),
                "--permission-mode",
                desired.mcp.permission_mode.value,
                "--shell-env-inherit",
                desired.mcp.shell_env_inherit.value,
            )
        )
        environment_lines = [
            'Environment="PYTHONUNBUFFERED=1"',
            f"Environment={systemd_quote('PATH=' + desired.mcp.exec_path)}",
        ]
        roots = self._effective_external_roots(desired)
        if roots:
            environment_lines.append(
                f"Environment={systemd_quote('CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=' + os.pathsep.join(roots))}"
            )
        if desired.github.config_dir:
            environment_lines.append(
                f"Environment={systemd_quote('GH_CONFIG_DIR=' + desired.github.config_dir)}"
            )
        access_lines: list[str] = []
        access_root = Path(desired.workspace_path) / ".workspace-mcp-access"
        folders = [(folder, True) for folder in desired.access.read_only] + [
            (folder, False) for folder in desired.access.read_write
        ]
        for folder, read_only in folders:
            directive = "BindReadOnlyPaths" if read_only else "BindPaths"
            definition = f"{folder.path}:{access_root / folder.alias}:rbind"
            access_lines.append(f"{directive}={systemd_quote(definition)}")
        if access_lines:
            environment_lines.append("PrivateUsers=true")
            environment_lines.extend(access_lines)
        return "\n".join(
            [
                _owner_marker(iid).rstrip("\n"),
                f"# workspace-mcp-manager-port={desired.mcp.port}",
                "[Unit]",
                f"Description=Coding Tools MCP - {iid}",
                "After=network.target",
                "",
                "[Service]",
                "Type=simple",
                f"WorkingDirectory={systemd_path(desired.workspace_path)}",
                *environment_lines,
                f"ExecStart={exec_start}",
                "Restart=on-failure",
                "RestartSec=2",
                "TimeoutStopSec=10",
                "KillMode=mixed",
                f"StandardOutput=append:{systemd_path(str(state_dir / 'mcp.log'))}",
                f"StandardError=append:{systemd_path(str(state_dir / 'mcp.log'))}",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )

    def _render_tunnel_unit(self, desired: DesiredInstance) -> str:
        iid = desired.instance_id.value
        mcp_unit = f"coding-tools-mcp-{iid}.service"
        state_dir = self._state_dir(desired)
        discovery_url = f"http://{desired.mcp.host}:{desired.mcp.port}/.well-known/mcp.json"
        exec_pre = " ".join(
            systemd_quote(value)
            for value in (
                self.curl_binary,
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "30",
                "--retry-delay",
                "1",
                "--retry-connrefused",
                "--retry-max-time",
                "30",
                "--connect-timeout",
                "1",
                discovery_url,
            )
        )
        return "\n".join(
            [
                _owner_marker(iid).rstrip("\n"),
                f"# workspace-mcp-manager-profile={desired.tunnel.profile}",
                f"# workspace-mcp-manager-health-port={desired.tunnel.health_port}",
                f"# workspace-mcp-manager-tunnel-id={desired.tunnel.id}",
                "[Unit]",
                f"Description=OpenAI MCP Tunnel - {iid}",
                f"Requires={mcp_unit}",
                f"After={mcp_unit}",
                "",
                "[Service]",
                "Type=simple",
                f"WorkingDirectory={systemd_path(str(self.paths.account_home))}",
                "UMask=0077",
                f"ExecStartPre={exec_pre}",
                f"ExecStart={self._runtime_command(desired, 'tunnel')}",
                "Restart=always",
                "RestartSec=5",
                "TimeoutStopSec=15",
                "KillMode=mixed",
                f"StandardOutput=append:{systemd_path(str(state_dir / 'tunnel-supervisor.log'))}",
                f"StandardError=append:{systemd_path(str(state_dir / 'tunnel-supervisor.log'))}",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )

    def _render_guard_unit(self, desired: DesiredInstance) -> str:
        iid = desired.instance_id.value
        state_dir = self._state_dir(desired)
        return "\n".join(
            [
                _owner_marker(iid).rstrip("\n"),
                "[Unit]",
                f"Description=Workspace MCP admission recovery guard - {iid}",
                f"After=coding-tools-mcp-{iid}.service",
                "",
                "[Service]",
                "Type=oneshot",
                f"ExecStart={self._runtime_command(desired, 'admission-guard')}",
                f"StandardOutput=append:{systemd_path(str(state_dir / 'admission-guard.log'))}",
                f"StandardError=append:{systemd_path(str(state_dir / 'admission-guard.log'))}",
                "",
            ]
        )

    def _render_guard_timer(self, desired: DesiredInstance) -> str:
        iid = desired.instance_id.value
        interval = desired.recovery.guard_interval_seconds
        return "\n".join(
            [
                _owner_marker(iid).rstrip("\n"),
                "[Unit]",
                f"Description=Workspace MCP admission recovery timer - {iid}",
                "",
                "[Timer]",
                f"OnBootSec={interval}s",
                f"OnUnitActiveSec={interval}s",
                "AccuracySec=1s",
                f"Unit=workspace-mcp-admission-guard-{iid}.service",
                "",
                "[Install]",
                "WantedBy=timers.target",
                "",
            ]
        )

    def generate(self, desired: DesiredInstance) -> GeneratedBundle:
        iid = desired.instance_id.value
        marker = f"{OWNER_PREFIX}{iid}"
        state_dir = self._state_dir(desired)
        state_owner = state_dir / ".owner"
        resources: list[GeneratedResource] = [
            GeneratedResource(
                "state-dir",
                ResourceKind.DIRECTORY,
                str(state_dir),
                0o700,
                ownership_token=marker,
                ownership_marker_path=str(state_owner),
            ),
            GeneratedResource(
                "state-owner",
                ResourceKind.FILE,
                str(state_owner),
                0o600,
                content=f"{OWNER_PREFIX}{iid}\n",
                ownership_token=marker,
            ),
            GeneratedResource(
                "tunnel-profile-dir",
                ResourceKind.DIRECTORY,
                str(self.paths.tunnel_profile_dir),
                0o700,
                remove_when_absent=False,
            ),
            GeneratedResource(
                "tunnel-profile",
                ResourceKind.TUNNEL_PROFILE,
                str(self._profile_path(desired)),
                0o600,
                content=self._render_profile(desired),
                ownership_token=marker,
            ),
        ]
        mcp_unit = f"coding-tools-mcp-{iid}.service"
        tunnel_unit = f"tunnel-client-{iid}.service"
        resources.extend(
            [
                GeneratedResource(
                    "mcp-unit",
                    ResourceKind.SYSTEMD_UNIT,
                    str(self._unit_path(mcp_unit)),
                    0o600,
                    content=self._render_mcp_unit(desired),
                    ownership_token=marker,
                    unit_name=mcp_unit,
                    enable_when_present=True,
                    active_when_running=True,
                ),
                GeneratedResource(
                    "tunnel-unit",
                    ResourceKind.SYSTEMD_UNIT,
                    str(self._unit_path(tunnel_unit)),
                    0o600,
                    content=self._render_tunnel_unit(desired),
                    ownership_token=marker,
                    unit_name=tunnel_unit,
                    enable_when_present=True,
                    active_when_running=True,
                ),
            ]
        )
        folders = [(folder, "ro") for folder in desired.access.read_only] + [
            (folder, "rw") for folder in desired.access.read_write
        ]
        if folders:
            access_root = Path(desired.workspace_path) / ".workspace-mcp-access"
            access_owner = access_root / ".owner"
            resources.extend(
                [
                    GeneratedResource(
                        "access-root",
                        ResourceKind.DIRECTORY,
                        str(access_root),
                        0o700,
                        ownership_token=marker,
                        ownership_marker_path=str(access_owner),
                    ),
                    GeneratedResource(
                        "access-owner",
                        ResourceKind.FILE,
                        str(access_owner),
                        0o600,
                        content=f"{OWNER_PREFIX}{iid}\n",
                        ownership_token=marker,
                    ),
                    GeneratedResource(
                        "access-ignore",
                        ResourceKind.FILE,
                        str(access_root / ".gitignore"),
                        0o600,
                        content="*\n",
                        ownership_token=marker,
                        ownership_marker_path=str(access_owner),
                    ),
                ]
            )
            for folder, mode in folders:
                resources.append(
                    GeneratedResource(
                        f"access-{mode}-{folder.alias}",
                        ResourceKind.DIRECTORY,
                        str(access_root / folder.alias),
                        0o700,
                        ownership_token=marker,
                        ownership_marker_path=str(access_owner),
                    )
                )
        if desired.recovery.admission_guard_enabled:
            guard_unit = f"workspace-mcp-admission-guard-{iid}.service"
            guard_timer = f"workspace-mcp-admission-guard-{iid}.timer"
            resources.extend(
                [
                    GeneratedResource(
                        "admission-guard-unit",
                        ResourceKind.SYSTEMD_UNIT,
                        str(self._unit_path(guard_unit)),
                        0o600,
                        content=self._render_guard_unit(desired),
                        ownership_token=marker,
                        unit_name=guard_unit,
                    ),
                    GeneratedResource(
                        "admission-guard-timer",
                        ResourceKind.SYSTEMD_UNIT,
                        str(self._unit_path(guard_timer)),
                        0o600,
                        content=self._render_guard_timer(desired),
                        ownership_token=marker,
                        unit_name=guard_timer,
                        enable_when_present=True,
                        active_when_running=True,
                    ),
                ]
            )
        return GeneratedBundle(
            instance_id=iid,
            desired_fingerprint=desired.fingerprint(),
            resources=tuple(resources),
        )