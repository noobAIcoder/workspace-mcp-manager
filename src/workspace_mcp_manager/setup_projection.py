from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .development_environment import (
    CORE_SSH_COMMAND_KEY,
    GIT_CONFIG_OWNERSHIP_VERSION,
    HTTPS_HELPER_KEY,
    IDENTITY_OWNER_KEY,
    IDENTITY_VERSION_KEY,
    REMOTE_OWNER_KEY,
    REMOTE_VERSION_KEY,
    managed_github_profile_path_for_instance,
)
from .development_toolchain import (
    project_development_toolchain,
    recommended_toolchain_values,
)
from .domain import DesiredInstance
from .endpoint_projection import (
    PORT_PROJECTION_VERSION,
    Endpoint,
    ListenerObservation,
    ListenerState,
    endpoint_conflicts,
    endpoint_overlap,
    normalized_host,
    observe_tcp_listeners,
)
from .errors import ErrorCode, ManagerError, config_error
from .operator_contracts import operator_template
from .paths import ManagerPaths
from .redaction import sanitized_subprocess_env
from .registry import InstanceRegistry


DISCOVERY_VERSION = 2
CANDIDATE_REQUEST_VERSION = 1
CANDIDATE_VERSION = 1
AUTHENTICATION_STATUS_VERSION = 1

MCP_PORT_RANGE = range(7654, 8000)
TUNNEL_HEALTH_PORT_RANGE = range(7070, 7500)

_INSTANCE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_ACCESS_ALIAS_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

_EDITABLE_POINTERS = frozenset(
    {
        "/instance_id",
        "/lifecycle/runtime",
        "/mcp/binary",
        "/mcp/host",
        "/mcp/port",
        "/mcp/permission_mode",
        "/mcp/shell_env_inherit",
        "/mcp/exec_path",
        "/mcp/external_roots",
        "/tunnel/binary",
        "/tunnel/id",
        "/tunnel/health_host",
        "/tunnel/health_port",
        "/tunnel/env_file",
        "/github/mode",
        "/github/config_dir",
        "/github/binary",
        "/git/identity",
        "/git/remote",
        "/agent/mode",
        "/agent/ssh_auth_sock",
        "/recovery/admission_guard_enabled",
        "/recovery/guard_interval_seconds",
        "/recovery/recovery_cooldown_seconds",
    }
)
_CLEARABLE_POINTERS = frozenset(
    {
        "/github/config_dir",
        "/github/binary",
        "/git/identity",
        "/git/remote",
        "/agent/ssh_auth_sock",
    }
)

_COPY_POINTERS = (
    "/mcp/binary",
    "/mcp/host",
    "/mcp/permission_mode",
    "/mcp/shell_env_inherit",
    "/mcp/exec_path",
    "/mcp/external_roots",
    "/tunnel/binary",
    "/tunnel/health_host",
    "/tunnel/env_file",
    "/recovery/admission_guard_enabled",
    "/recovery/guard_interval_seconds",
    "/recovery/recovery_cooldown_seconds",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_instance_id(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = decomposed.encode("ascii", errors="ignore").decode("ascii").lower()
    normalized = _INSTANCE_NORMALIZE_RE.sub("-", ascii_value).strip("-") or "workspace"
    return normalized[:63].rstrip("-") or "workspace"


def suggest_instance_id(base: str, used: set[str]) -> str:
    normalized = normalize_instance_id(base)
    if normalized not in used:
        return normalized
    suffix = 2
    while True:
        suffix_text = f"-{suffix}"
        stem = normalized[: 63 - len(suffix_text)].rstrip("-") or "workspace"
        candidate = f"{stem}{suffix_text}"
        if candidate not in used:
            return candidate
        suffix += 1


def canonical_workspace_identity(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _run_local(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 3.0,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            cwd=None if cwd is None else str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            env=None if env is None else dict(env),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_value(git: str, workspace: Path, args: list[str], env: Mapping[str, str]) -> str | None:
    result = _run_local([git, "-C", str(workspace), *args], env=env)
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.rstrip("\n")
    return value if value else None


def _git_values(git: str, workspace: Path, args: list[str], env: Mapping[str, str]) -> list[str]:
    result = _run_local([git, "-C", str(workspace), *args], env=env)
    if result is None or result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _remote_identity(raw_url: str) -> dict[str, Any]:
    value = raw_url.strip()
    result: dict[str, Any] = {
        "transport": None,
        "host": None,
        "repository": None,
        "supported": False,
        "desired_protocol": None,
        "userinfo_present": False,
    }
    path: str | None = None
    if "://" in value:
        parsed = urlparse(value)
        transport = parsed.scheme.lower() if parsed.scheme else None
        host = parsed.hostname.lower() if parsed.hostname else None
        path = unquote(parsed.path or "")
        result.update(
            {
                "transport": transport,
                "host": host,
                "userinfo_present": parsed.username is not None or parsed.password is not None,
            }
        )
    elif ":" in value and not value.startswith(("/", "./", "../")):
        left, path = value.split(":", 1)
        host = left.rsplit("@", 1)[-1].lower()
        result.update({"transport": "ssh", "host": host})
    else:
        result["transport"] = "path"

    if path:
        repository = path.strip("/")
        if repository.endswith(".git"):
            repository = repository[:-4]
        if repository:
            result["repository"] = repository

    host = result.get("host")
    transport = result.get("transport")
    if host == "github.com" and result.get("repository"):
        if transport in {"ssh", "git+ssh"}:
            result.update({"supported": True, "desired_protocol": "ssh"})
        elif transport in {"http", "https"}:
            result.update({"supported": True, "desired_protocol": "https-gh"})
    return result


def _credential_name_present(path: Path, name: str) -> bool | None:
    try:
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            for line in handle:
                key, separator, value = line.partition("=")
                if separator and key.strip() == name:
                    return bool(value.strip())
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError):
        return None
    return False


def _socket_available(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return stat.S_ISSOCK(Path(raw).stat().st_mode)
    except OSError:
        return False


def authentication_status(
    paths: ManagerPaths,
    *,
    gh_binary: str | None,
    ssh_auth_sock: str | None,
    tunnel_env_file: str | None = None,
) -> dict[str, Any]:
    providers: list[dict[str, Any]] = []

    if gh_binary:
        env = sanitized_subprocess_env(os.environ)
        env["HOME"] = str(paths.account_home)
        probe = _run_local([gh_binary, "auth", "status", "--hostname", "github.com"], env=env, timeout=5.0)
        gh_status = "unknown" if probe is None else ("available" if probe.returncode == 0 else "unavailable")
        gh_reason = (
            "GitHub CLI authentication status could not be determined"
            if probe is None
            else ("GitHub CLI reports an authenticated account" if probe.returncode == 0 else "GitHub CLI reports no usable authentication")
        )
    else:
        gh_status = "unavailable"
        gh_reason = "GitHub CLI is not available"
    providers.append(
        {
            "provider": "github-cli",
            "status": gh_status,
            "verification_scope": "provider_status",
            "capabilities": {"binary_present": bool(gh_binary)},
            "setup_action": {
                "label": "Open GitHub authentication setup",
                "url": "https://github.com/settings/tokens",
            }
            if gh_status != "available"
            else None,
            "reason": gh_reason,
        }
    )

    ssh_available = _socket_available(ssh_auth_sock)
    providers.append(
        {
            "provider": "ssh-agent",
            "status": "available" if ssh_available else "unavailable",
            "verification_scope": "local_capability",
            "capabilities": {"socket_available": ssh_available, "socket_path_present": bool(ssh_auth_sock)},
            "setup_action": {
                "label": "Open GitHub SSH key setup",
                "url": "https://github.com/settings/keys",
            }
            if not ssh_available
            else None,
            "reason": "SSH agent socket is available" if ssh_available else "SSH agent socket is unavailable",
        }
    )

    env_file = Path(tunnel_env_file) if tunnel_env_file else paths.account_home / ".config/tunnel-client/runtime.env"
    tunnel_present = _credential_name_present(env_file, "CONTROL_PLANE_API_KEY")
    tunnel_status = "unknown" if tunnel_present is None else ("available" if tunnel_present else "unavailable")
    providers.append(
        {
            "provider": "tunnel-control-plane",
            "status": tunnel_status,
            "verification_scope": "credential_presence",
            "capabilities": {"credential_name_present": tunnel_present is True},
            "setup_action": None,
            "reason": (
                "Tunnel credential presence could not be determined"
                if tunnel_present is None
                else ("Tunnel credential name is configured" if tunnel_present else "Tunnel credential name is not configured")
            ),
        }
    )
    return {"authentication_status_version": AUTHENTICATION_STATUS_VERSION, "providers": providers}


def _provider_status(status: Mapping[str, Any], provider: str) -> Mapping[str, Any] | None:
    values = status.get("providers")
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, Mapping) and item.get("provider") == provider:
            return item
    return None


def discover_local_git(paths: ManagerPaths, selected: Path, repository_root: Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
    exec_path = f"{paths.account_home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
    git = shutil.which("git", path=exec_path)
    gh = shutil.which("gh", path=exec_path)
    ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK")
    auth = authentication_status(paths, gh_binary=gh, ssh_auth_sock=ssh_auth_sock)

    payload: dict[str, Any] = {
        "git_executable": {"present": bool(git), "path": git},
        "repository": {"present": repository_root is not None, "root": None if repository_root is None else str(repository_root)},
        "working_tree": {"clean": None, "entry_count": None},
        "identity": {
            "local": {"name": None, "email": None},
            "effective": {"name": None, "email": None, "scope": None, "origin": None},
            "classification": "absent",
            "owner": None,
        },
        "remote": {
            "names": [],
            "selected": None,
            "transport": None,
            "host": None,
            "repository": None,
            "desired_protocol": None,
            "classification": "absent",
            "owner": None,
        },
        "github_cli": {"present": bool(gh), "path": gh},
        "ssh_agent": {"socket_path_present": bool(ssh_auth_sock), "available": _socket_available(ssh_auth_sock), "socket": ssh_auth_sock},
    }
    if not git or repository_root is None:
        return payload, auth

    env = sanitized_subprocess_env(os.environ)
    env.update({"HOME": str(paths.account_home), "PATH": exec_path, "GIT_TERMINAL_PROMPT": "0"})
    workspace = repository_root

    status = _run_local([git, "-C", str(workspace), "status", "--porcelain=v1", "--untracked-files=all"], env=env, timeout=5.0)
    if status is not None and status.returncode == 0:
        entries = [line for line in status.stdout.splitlines() if line]
        payload["working_tree"] = {"clean": not entries, "entry_count": len(entries)}

    local_name = _git_value(git, workspace, ["config", "--local", "--get", "user.name"], env)
    local_email = _git_value(git, workspace, ["config", "--local", "--get", "user.email"], env)
    effective_name = _git_value(git, workspace, ["config", "--get", "user.name"], env)
    effective_email = _git_value(git, workspace, ["config", "--get", "user.email"], env)
    origin_name = _git_value(git, workspace, ["config", "--show-origin", "--get", "user.name"], env)
    origin = origin_name.split("\t", 1)[0] if origin_name and "\t" in origin_name else origin_name
    scope = "repository-local" if local_name is not None or local_email is not None else ("inherited" if effective_name or effective_email else None)
    identity_owner = _git_value(git, workspace, ["config", "--local", "--get", IDENTITY_OWNER_KEY], env)
    identity_version = _git_value(git, workspace, ["config", "--local", "--get", IDENTITY_VERSION_KEY], env)
    if identity_owner or identity_version:
        identity_classification = "already_managed" if identity_owner and identity_version == GIT_CONFIG_OWNERSHIP_VERSION else "foreign"
    elif local_name and local_email:
        identity_classification = "adoptable"
    elif local_name or local_email:
        identity_classification = "foreign"
    elif effective_name or effective_email:
        identity_classification = "observed_only"
    else:
        identity_classification = "absent"
    payload["identity"] = {
        "local": {"name": local_name, "email": local_email},
        "effective": {"name": effective_name, "email": effective_email, "scope": scope, "origin": origin},
        "classification": identity_classification,
        "owner": identity_owner,
    }

    remotes = sorted(_git_values(git, workspace, ["remote"], env))
    selected_remote = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
    remote_payload = dict(payload["remote"])
    remote_payload.update({"names": remotes, "selected": selected_remote})
    if selected_remote:
        remote_url = _git_value(git, workspace, ["remote", "get-url", selected_remote], env)
        metadata = _remote_identity(remote_url or "")
        remote_owner = _git_value(git, workspace, ["config", "--local", "--get", REMOTE_OWNER_KEY], env)
        remote_version = _git_value(git, workspace, ["config", "--local", "--get", REMOTE_VERSION_KEY], env)
        helpers = _git_values(git, workspace, ["config", "--local", "--get-all", HTTPS_HELPER_KEY], env)
        ssh_commands = _git_values(git, workspace, ["config", "--local", "--get-all", CORE_SSH_COMMAND_KEY], env)
        if remote_owner or remote_version:
            classification = "already_managed" if remote_owner and remote_version == GIT_CONFIG_OWNERSHIP_VERSION else "foreign"
        elif metadata.get("userinfo_present"):
            classification = "foreign"
        elif not metadata.get("supported"):
            classification = "unsupported"
        elif helpers or ssh_commands:
            classification = "foreign"
        else:
            classification = "adoptable"
        remote_payload.update(
            {
                "transport": metadata.get("transport"),
                "host": metadata.get("host"),
                "repository": metadata.get("repository"),
                "desired_protocol": metadata.get("desired_protocol"),
                "userinfo_present": bool(metadata.get("userinfo_present")),
                "classification": classification,
                "owner": remote_owner,
            }
        )
    payload["remote"] = remote_payload
    return payload, auth


def _repository_root(paths: ManagerPaths, selected: Path) -> Path | None:
    exec_path = f"{paths.account_home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
    git = shutil.which("git", path=exec_path)
    if not git:
        return None
    env = sanitized_subprocess_env(os.environ)
    env.update({"HOME": str(paths.account_home), "PATH": exec_path, "GIT_TERMINAL_PROMPT": "0"})
    probe = _run_local([git, "-C", str(selected), "rev-parse", "--show-toplevel"], env=env)
    if probe is None or probe.returncode != 0 or not probe.stdout.strip():
        return None
    try:
        root = Path(probe.stdout.strip()).resolve(strict=True)
    except OSError:
        return None
    return root if root.is_dir() else None


def _match_workspace(registry: InstanceRegistry, identity: str) -> tuple[str, list[DesiredInstance]]:
    matches = [
        desired
        for desired in registry.list()
        if canonical_workspace_identity(desired.workspace_path) == identity
    ]
    return ("none" if not matches else ("single" if len(matches) == 1 else "conflict"), matches)


def discover_workspace(paths: ManagerPaths, registry: InstanceRegistry, raw_path: str) -> dict[str, Any]:
    input_path = str(raw_path)
    candidate = Path(raw_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise config_error("workspace discovery path does not exist", path=input_path) from exc
    if not resolved.is_dir():
        raise config_error("workspace discovery path must be a directory", path=input_path)

    repository_root = _repository_root(paths, resolved)
    workspace = repository_root or resolved
    identity = canonical_workspace_identity(workspace)
    match_state, matches = _match_workspace(registry, identity)
    local_git, auth = discover_local_git(paths, resolved, repository_root)
    matched_desired = matches[0] if match_state == "single" and len(matches) == 1 else None
    development_toolchain = project_development_toolchain(
        paths,
        workspace,
        desired=matched_desired,
    )

    matched_ids = {item.instance_id.value for item in matches}
    identity_owner = local_git.get("identity", {}).get("owner") if isinstance(local_git.get("identity"), Mapping) else None
    remote_owner = local_git.get("remote", {}).get("owner") if isinstance(local_git.get("remote"), Mapping) else None
    if identity_owner and identity_owner not in matched_ids and isinstance(local_git.get("identity"), dict):
        local_git["identity"]["classification"] = "foreign"
    if remote_owner and remote_owner not in matched_ids and isinstance(local_git.get("remote"), dict):
        local_git["remote"]["classification"] = "foreign"

    used = {item.instance_id.value for item in registry.list()}
    suggestion_base = workspace.name
    suggestion = matches[0].instance_id.value if match_state == "single" else suggest_instance_id(suggestion_base, used)
    warnings: list[str] = []
    if match_state == "conflict":
        warnings.append("Multiple declarations resolve to the same canonical workspace identity")
    toolchain_warnings = development_toolchain.get("warnings")
    if isinstance(toolchain_warnings, list):
        warnings.extend(str(item) for item in toolchain_warnings)
    evidence = {
        "workspace_identity": identity,
        "repository_root": None if repository_root is None else str(repository_root),
        "instance_match": {"status": match_state, "instance_ids": sorted(item.instance_id.value for item in matches)},
        "instance_id_suggestion": suggestion,
        "local_git": local_git,
        "authentication_status": auth,
        "development_toolchain": development_toolchain,
    }
    return {
        "ok": True,
        "discovery_version": DISCOVERY_VERSION,
        "input_path": input_path,
        "resolved_path": str(resolved),
        "workspace_path": str(workspace),
        "workspace_identity": identity,
        "repository_detected": repository_root is not None,
        "repository_root": None if repository_root is None else str(repository_root),
        "repository_basename": workspace.name if repository_root is not None else None,
        "instance_match": evidence["instance_match"],
        "instance_id_suggestion": suggestion,
        "local_git": local_git,
        "authentication_status": auth,
        "development_toolchain": development_toolchain,
        "warnings": warnings,
        "discovery_fingerprint": _fingerprint(
            {"discovery_version": DISCOVERY_VERSION, **evidence}
        ),
    }


class PortProjectionService:
    def __init__(
        self,
        registry: InstanceRegistry,
        *,
        listener_observation: ListenerObservation | None = None,
    ) -> None:
        self.registry = registry
        self._fixed_observation = listener_observation

    def observe(self) -> ListenerObservation:
        return self._fixed_observation or observe_tcp_listeners()

    def declared(self) -> list[Endpoint]:
        result: list[Endpoint] = []
        for desired in self.registry.list():
            result.extend(
                (
                    Endpoint(desired.mcp.host, desired.mcp.port, "MCP", "declared", desired.instance_id.value),
                    Endpoint(
                        desired.tunnel.health_host,
                        desired.tunnel.health_port,
                        "Tunnel health",
                        "declared",
                        desired.instance_id.value,
                    ),
                )
            )
        return result

    @staticmethod
    def _attribute_observed(observed: Endpoint, declared: list[Endpoint]) -> Endpoint:
        exact = [
            item
            for item in declared
            if item.port == observed.port and normalized_host(item.host) == normalized_host(observed.host)
        ]
        if len(exact) == 1:
            item = exact[0]
            return Endpoint(
                observed.host,
                observed.port,
                item.purpose,
                "observed",
                item.instance_id,
                attribution="manager declaration",
                dual_stack_unknown=observed.dual_stack_unknown,
            )
        return observed

    @staticmethod
    def _merge(declared: list[Endpoint], observed: list[Endpoint]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        consumed: set[int] = set()
        for item in declared:
            exact_index = next(
                (
                    index
                    for index, listener in enumerate(observed)
                    if index not in consumed
                    and listener.port == item.port
                    and normalized_host(listener.host) == normalized_host(item.host)
                ),
                None,
            )
            if exact_index is None:
                result.append(item.to_dict())
                continue
            consumed.add(exact_index)
            merged = item.to_dict()
            merged["source"] = "both"
            merged["state"] = "declared + listening"
            result.append(merged)
        for index, item in enumerate(observed):
            if index in consumed:
                continue
            payload = item.to_dict()
            payload["state"] = "listening"
            if payload.get("purpose") == "Unknown":
                payload["purpose"] = "External listener"
            result.append(payload)
        return sorted(result, key=lambda item: (int(item["port"]), str(item["bind_address"]), str(item["source"])))

    def project(self) -> dict[str, Any]:
        declared = self.declared()
        observation = self.observe()
        observed = [self._attribute_observed(item, declared) for item in observation.endpoints]
        payload = {
            "ok": True,
            "port_projection_version": PORT_PROJECTION_VERSION,
            "listener_observation": {"state": observation.state.value, "reason": observation.reason},
            "declared_endpoints": [item.to_dict() for item in declared],
            "observed_listeners": [item.to_dict() for item in observed],
            "endpoints": self._merge(declared, observed),
        }
        payload["projection_fingerprint"] = _fingerprint(
            {
                "port_projection_version": PORT_PROJECTION_VERSION,
                "listener_observation": payload["listener_observation"],
                "declared_endpoints": payload["declared_endpoints"],
                "observed_listeners": payload["observed_listeners"],
            }
        )
        return payload

    @staticmethod
    def _eligible(candidate: Endpoint, others: list[Endpoint]) -> bool:
        conflicts, unknown = endpoint_conflicts(candidate, others)
        return not conflicts and not unknown

    def recommend(self, *, purpose: str, host: str, exclude: list[Endpoint] | None = None) -> dict[str, Any]:
        declared = self.declared()
        observation = self.observe()
        others = declared + list(observation.endpoints) + list(exclude or [])
        ports = MCP_PORT_RANGE if purpose == "MCP" else TUNNEL_HEALTH_PORT_RANGE
        selected: int | None = None
        for port in ports:
            endpoint = Endpoint(host, port, purpose, "candidate")
            if self._eligible(endpoint, others):
                selected = port
                break
        return {
            "port": selected,
            "status": "recommended" if observation.state is ListenerState.AVAILABLE else "provisional",
            "listener_observation": observation.state.value,
        }

    def candidate_projection(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        base = self.project()
        declared = self.declared()
        observation = self.observe()
        observed = [self._attribute_observed(item, declared) for item in observation.endpoints]
        instance_id = str(candidate.get("instance_id") or "")
        mcp = candidate.get("mcp") if isinstance(candidate.get("mcp"), Mapping) else {}
        tunnel = candidate.get("tunnel") if isinstance(candidate.get("tunnel"), Mapping) else {}
        candidate_endpoints = [
            Endpoint(str(mcp.get("host") or ""), int(mcp.get("port")), "MCP", "candidate", instance_id)
            if isinstance(mcp.get("port"), int)
            else None,
            Endpoint(str(tunnel.get("health_host") or ""), int(tunnel.get("health_port")), "Tunnel health", "candidate", instance_id)
            if isinstance(tunnel.get("health_port"), int)
            else None,
        ]
        endpoints = [item for item in candidate_endpoints if item is not None]
        current: DesiredInstance | None = None
        if instance_id:
            try:
                current = self.registry.get(instance_id)
            except ManagerError as exc:
                if exc.code is not ErrorCode.INSTANCE_NOT_FOUND:
                    raise

        def unchanged(item: Endpoint) -> bool:
            if current is None:
                return False
            if item.purpose == "MCP":
                return normalized_host(current.mcp.host) == normalized_host(item.host) and current.mcp.port == item.port
            return normalized_host(current.tunnel.health_host) == normalized_host(item.host) and current.tunnel.health_port == item.port

        other_declared = [item for item in declared if not (instance_id and item.instance_id == instance_id)]
        conflicts: list[dict[str, Any]] = []
        unknown = False
        for index, endpoint in enumerate(endpoints):
            internal = [item for other_index, item in enumerate(endpoints) if other_index != index]
            endpoint_conflict, endpoint_unknown = endpoint_conflicts(endpoint, other_declared + internal)
            for item in endpoint_conflict:
                conflicts.append({"candidate": endpoint.to_dict(), "with": item.to_dict(), "reason": "endpoint bind scopes overlap"})
            unknown = unknown or endpoint_unknown

            for listener in observed:
                if unchanged(endpoint) and listener.instance_id == instance_id:
                    continue
                overlap = endpoint_overlap(endpoint, listener)
                if overlap is True:
                    attributed_same = listener.instance_id == instance_id and unchanged(endpoint)
                    if not attributed_same:
                        conflicts.append({"candidate": endpoint.to_dict(), "with": listener.to_dict(), "reason": "observed listener overlaps candidate endpoint"})
                elif overlap is None:
                    unknown = True
            if observation.state is not ListenerState.AVAILABLE and not unchanged(endpoint):
                unknown = True

        state = "conflict" if conflicts else ("unknown" if unknown else "clear")
        return {
            **base,
            "candidate_endpoints": [item.to_dict() for item in endpoints],
            "collision_state": state,
            "conflicts": conflicts,
        }


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/") or pointer == "/" or "//" in pointer:
        raise config_error("field edit path must be a valid RFC 6901 pointer", path=pointer)
    parts = pointer[1:].split("/")
    result: list[str] = []
    for part in parts:
        decoded = part.replace("~1", "/").replace("~0", "~")
        if "~" in decoded and "~" in part and not re.fullmatch(r"(?:[^~]|~[01])*", part):
            raise config_error("field edit path has invalid RFC 6901 escape", path=pointer)
        result.append(decoded)
    return result


def _get_pointer(value: Mapping[str, Any], pointer: str) -> Any:
    current: Any = value
    for part in _pointer_parts(pointer):
        if not isinstance(current, Mapping) or part not in current:
            raise config_error("field edit path does not exist", path=pointer)
        current = current[part]
    return copy.deepcopy(current)


def _set_pointer(value: dict[str, Any], pointer: str, replacement: Any) -> None:
    parts = _pointer_parts(pointer)
    current: dict[str, Any] = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            raise config_error("field edit path does not address an object", path=pointer)
        current = child
    if parts[-1] not in current:
        raise config_error("field edit path does not exist", path=pointer)
    current[parts[-1]] = copy.deepcopy(replacement)


def _leaf_provenance(value: Any, prefix: str, source: str, source_ref: str | None, reason: str) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        result: list[dict[str, Any]] = []
        for key in sorted(value):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            result.extend(_leaf_provenance(value[key], f"{prefix}/{escaped}", source, source_ref, reason))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_leaf_provenance(item, f"{prefix}/{index}", source, source_ref, reason))
        if not value:
            result.append({"path": prefix, "source": source, "source_ref": source_ref, "status": "effective", "reason": reason})
        return result
    return [{"path": prefix, "source": source, "source_ref": source_ref, "status": "effective", "reason": reason}]


def _provenance_map(defaults: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["path"]: item
        for item in _leaf_provenance(defaults, "", "template_default", "template_version=2", "manager template default")
    }


def _mark_provenance(
    provenance: dict[str, dict[str, Any]],
    value: Any,
    pointer: str,
    source: str,
    source_ref: str | None,
    reason: str,
    *,
    status: str = "effective",
) -> None:
    for key in [key for key in provenance if key == pointer or key.startswith(pointer + "/")]:
        provenance.pop(key, None)
    records = _leaf_provenance(value, pointer, source, source_ref, reason)
    for record in records:
        record["status"] = status
        provenance[record["path"]] = record


def _normalize_alias(path: str) -> str:
    base = Path(path).name.lower()
    value = _ACCESS_ALIAS_NORMALIZE_RE.sub("-", base).strip("-") or "folder"
    return value[:63].rstrip("-") or "folder"


def _desired_v2_projection(desired: DesiredInstance) -> dict[str, Any]:
    raw = desired.to_dict()
    if desired.config_version == 2:
        return raw
    github = raw.get("github") if isinstance(raw.get("github"), Mapping) else {}
    config_dir = github.get("config_dir")
    raw["config_version"] = 2
    raw["github"] = {
        "mode": "external" if config_dir else "disabled",
        "config_dir": config_dir,
        "binary": None,
    }
    raw["git"] = {"identity": None, "remote": None}
    raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
    return raw


def _apply_access_edits(candidate: dict[str, Any], edits: list[Any], provenance: dict[str, dict[str, Any]]) -> None:
    access = candidate.get("access")
    if not isinstance(access, dict):
        raise config_error("candidate access state is invalid")
    groups = {"ro": "read_only", "rw": "read_write"}
    for index, raw in enumerate(edits):
        if not isinstance(raw, Mapping):
            raise config_error("access edit must be an object", index=index)
        operation = raw.get("operation")
        mode = raw.get("mode")
        if operation not in {"add", "remove", "update"}:
            raise config_error("access edit operation must be add, remove, or update", index=index)
        if mode not in groups:
            raise config_error("access edit mode must be ro or rw", index=index)
        group = groups[str(mode)]
        values = access.get(group)
        if not isinstance(values, list):
            raise config_error("candidate access group is invalid", group=group)
        target_alias = raw.get("target_alias")
        if operation in {"remove", "update"}:
            if not isinstance(target_alias, str) or not target_alias:
                raise config_error("access remove/update requires target_alias", index=index)
            found_group: str | None = None
            found_index: int | None = None
            for candidate_group in ("read_only", "read_write"):
                for item_index, item in enumerate(access.get(candidate_group, [])):
                    if isinstance(item, Mapping) and item.get("alias") == target_alias:
                        found_group, found_index = candidate_group, item_index
                        break
                if found_group is not None:
                    break
            if found_group is None or found_index is None:
                raise config_error("access target alias is not configured", alias=target_alias)
            access[found_group].pop(found_index)
            if operation == "remove":
                continue
        path = raw.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise config_error("access add/update requires an absolute path", index=index)
        alias = raw.get("alias")
        if alias is None:
            alias = _normalize_alias(path)
        if not isinstance(alias, str) or not alias:
            raise config_error("access alias must be a non-empty string", index=index)
        access[group].append({"alias": alias, "path": path})

    _mark_provenance(
        provenance,
        access,
        "/access",
        "operator_override",
        "access_edits",
        "structured operator Access edits",
    )


def _validate_candidate(candidate: dict[str, Any]) -> None:
    probe = copy.deepcopy(candidate)
    tunnel = probe.get("tunnel")
    if isinstance(tunnel, dict) and not tunnel.get("id"):
        tunnel["id"] = "tunnel_candidatevalidation"
    DesiredInstance.from_dict(probe)


class SetupProjectionService:
    def __init__(
        self,
        paths: ManagerPaths,
        registry: InstanceRegistry,
        *,
        ports: PortProjectionService | None = None,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.ports = ports or PortProjectionService(registry)

    def discover(self, path: str) -> dict[str, Any]:
        return discover_workspace(self.paths, self.registry, path)

    def ports_projection(self) -> dict[str, Any]:
        return self.ports.project()

    def candidate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"candidate_request_version", "workspace_path", "copy_source_instance_id", "field_edits", "access_edits"}
        unknown = sorted(set(request) - allowed)
        if unknown:
            raise config_error("candidate request contains unknown fields", fields=unknown)
        if request.get("candidate_request_version") != CANDIDATE_REQUEST_VERSION:
            raise config_error(
                "unsupported candidate_request_version",
                supported=CANDIDATE_REQUEST_VERSION,
                received=request.get("candidate_request_version"),
            )
        workspace_path = request.get("workspace_path")
        if not isinstance(workspace_path, str) or not workspace_path:
            raise config_error("candidate request requires workspace_path")
        field_edits = request.get("field_edits", [])
        access_edits = request.get("access_edits", [])
        if not isinstance(field_edits, list) or not isinstance(access_edits, list):
            raise config_error("candidate field_edits/access_edits must be arrays")

        discovery = self.discover(workspace_path)
        listener_snapshot = self.ports.observe()
        ports = PortProjectionService(self.registry, listener_observation=listener_snapshot)
        match = discovery.get("instance_match") if isinstance(discovery.get("instance_match"), Mapping) else {}
        if match.get("status") == "conflict":
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "multiple declarations resolve to the selected canonical workspace",
                {"instance_ids": match.get("instance_ids", [])},
            )

        template = operator_template(self.paths)
        defaults = template.get("defaults")
        if not isinstance(defaults, Mapping):
            raise ManagerError(ErrorCode.CONFIG_INVALID, "operator template defaults are unavailable")
        candidate = copy.deepcopy(dict(defaults))
        provenance = _provenance_map(candidate)

        copy_source_id = request.get("copy_source_instance_id")
        copy_source: DesiredInstance | None = None
        if copy_source_id is not None:
            if not isinstance(copy_source_id, str) or not copy_source_id:
                raise config_error("copy_source_instance_id must be a non-empty instance ID")
            copy_source = self.registry.get(copy_source_id)
            source_raw = _desired_v2_projection(copy_source)
            for pointer in _COPY_POINTERS:
                copied = _get_pointer(source_raw, pointer)
                _set_pointer(candidate, pointer, copied)
                _mark_provenance(
                    provenance,
                    copied,
                    pointer,
                    "copy_source",
                    copy_source_id,
                    "reusable copy-source configuration",
                )
            candidate["access"] = copy.deepcopy(source_raw["access"])
            _mark_provenance(
                provenance,
                candidate["access"],
                "/access",
                "copy_source",
                copy_source_id,
                "explicitly copied Access desired state",
            )

        matched_instance: DesiredInstance | None = None
        if match.get("status") == "single":
            match_ids = match.get("instance_ids")
            if isinstance(match_ids, list) and len(match_ids) == 1:
                matched_instance = self.registry.get(str(match_ids[0]))

        candidate["workspace_path"] = str(discovery["workspace_path"])
        _mark_provenance(
            provenance,
            candidate["workspace_path"],
            "/workspace_path",
            "workspace_discovery",
            str(discovery["discovery_fingerprint"]),
            "canonical manager-resolved workspace",
        )
        candidate["instance_id"] = str(discovery["instance_id_suggestion"])
        _mark_provenance(
            provenance,
            candidate["instance_id"],
            "/instance_id",
            "manager_derived",
            str(discovery["discovery_fingerprint"]),
            "deterministic instance-ID suggestion",
        )

        # A matching authoritative declaration is the complete base for an
        # existing-workspace candidate. This keeps settings/update projections
        # manager-owned and preserves custom profiles plus non-default reusable
        # configuration without frontend reconstruction.
        if matched_instance is not None:
            matched = _desired_v2_projection(matched_instance)
            candidate = copy.deepcopy(matched)
            provenance = {
                item["path"]: item
                for item in _leaf_provenance(
                    candidate,
                    "",
                    "workspace_discovery",
                    matched_instance.instance_id.value,
                    "authoritative declaration already matches selected workspace",
                )
            }
        else:
            local_git = discovery.get("local_git") if isinstance(discovery.get("local_git"), Mapping) else {}
            identity = local_git.get("identity") if isinstance(local_git.get("identity"), Mapping) else {}
            if identity.get("classification") == "adoptable":
                local = identity.get("local") if isinstance(identity.get("local"), Mapping) else {}
                if local.get("name") and local.get("email"):
                    candidate["git"]["identity"] = {"name": local["name"], "email": local["email"]}
                    _mark_provenance(
                        provenance,
                        candidate["git"]["identity"],
                        "/git/identity",
                        "workspace_discovery",
                        str(discovery["discovery_fingerprint"]),
                        "repository-local Git identity is safely adoptable",
                    )

            remote = local_git.get("remote") if isinstance(local_git.get("remote"), Mapping) else {}
            auth = discovery.get("authentication_status") if isinstance(discovery.get("authentication_status"), Mapping) else {}
            if remote.get("classification") == "adoptable" and remote.get("selected") and remote.get("desired_protocol"):
                protocol = str(remote["desired_protocol"])
                if protocol == "ssh":
                    ssh_status = _provider_status(auth, "ssh-agent")
                    if ssh_status and ssh_status.get("status") == "available":
                        candidate["git"]["remote"] = {"name": remote["selected"], "protocol": "ssh"}
                        socket = local_git.get("ssh_agent", {}).get("socket") if isinstance(local_git.get("ssh_agent"), Mapping) else None
                        if socket:
                            candidate["agent"] = {"mode": "external", "ssh_auth_sock": socket}
                elif protocol == "https-gh":
                    gh_status = _provider_status(auth, "github-cli")
                    gh = local_git.get("github_cli") if isinstance(local_git.get("github_cli"), Mapping) else {}
                    if gh_status and gh_status.get("status") == "available" and gh.get("path"):
                        candidate["git"]["remote"] = {"name": remote["selected"], "protocol": "https-gh"}
                        candidate["github"] = {
                            "mode": "external",
                            "config_dir": str(self.paths.account_home / ".config/gh"),
                            "binary": gh["path"],
                        }
                if candidate["git"].get("remote") is not None:
                    _mark_provenance(
                        provenance,
                        candidate["git"]["remote"],
                        "/git/remote",
                        "workspace_discovery",
                        str(discovery["discovery_fingerprint"]),
                        "supported target remote is safely adoptable",
                    )
                    if protocol == "ssh":
                        _mark_provenance(provenance, candidate["agent"], "/agent", "manager_derived", "ssh-agent", "external SSH capability required by adopted remote")
                    else:
                        _mark_provenance(provenance, candidate["github"], "/github", "manager_derived", "github-cli", "external GitHub CLI capability required by adopted remote")

            development = (
                discovery.get("development_toolchain")
                if isinstance(discovery.get("development_toolchain"), Mapping)
                else {}
            )
            selection = development.get("selection") if isinstance(development.get("selection"), Mapping) else {}
            node_root = selection.get("node_root")
            if selection.get("state") == "ready" and isinstance(node_root, str) and node_root:
                exec_path, external_roots = recommended_toolchain_values(
                    self.paths,
                    node_root=node_root,
                    current_exec_path=str(candidate["mcp"]["exec_path"]),
                    current_external_roots=tuple(str(item) for item in candidate["mcp"]["external_roots"]),
                )
                candidate["mcp"]["exec_path"] = exec_path
                candidate["mcp"]["external_roots"] = external_roots
                _mark_provenance(
                    provenance,
                    exec_path,
                    "/mcp/exec_path",
                    "workspace_discovery",
                    str(discovery["discovery_fingerprint"]),
                    "compatible repository Node/pnpm toolchain selected from installed NVM roots",
                )
                _mark_provenance(
                    provenance,
                    external_roots,
                    "/mcp/external_roots",
                    "workspace_discovery",
                    str(discovery["discovery_fingerprint"]),
                    "complete selected NVM version root is required by the MCP execution sandbox",
                )

            mcp_recommendation = ports.recommend(purpose="MCP", host=str(candidate["mcp"]["host"]))
            if mcp_recommendation["port"] is not None:
                candidate["mcp"]["port"] = mcp_recommendation["port"]
                _mark_provenance(
                    provenance,
                    candidate["mcp"]["port"],
                    "/mcp/port",
                    "manager_recommendation",
                    "host-ports",
                    "first eligible preferred MCP port",
                    status="provisional" if mcp_recommendation["status"] == "provisional" else "effective",
                )
            exclude = [Endpoint(str(candidate["mcp"]["host"]), int(candidate["mcp"]["port"]), "MCP", "candidate")] if isinstance(candidate["mcp"].get("port"), int) else []
            health_recommendation = ports.recommend(
                purpose="Tunnel health",
                host=str(candidate["tunnel"]["health_host"]),
                exclude=exclude,
            )
            if health_recommendation["port"] is not None:
                candidate["tunnel"]["health_port"] = health_recommendation["port"]
                _mark_provenance(
                    provenance,
                    candidate["tunnel"]["health_port"],
                    "/tunnel/health_port",
                    "manager_recommendation",
                    "host-ports",
                    "first eligible preferred tunnel-health port",
                    status="provisional" if health_recommendation["status"] == "provisional" else "effective",
                )

        normalized_edits: list[dict[str, Any]] = []
        seen_edit_paths: set[str] = set()
        github_mode_edit = next(
            (
                item.get("value")
                for item in field_edits
                if isinstance(item, Mapping)
                and item.get("path") == "/github/mode"
                and item.get("operation") == "set"
            ),
            None,
        )
        effective_github_mode = str(github_mode_edit or candidate.get("github", {}).get("mode") or "disabled")
        if (
            matched_instance is not None
            and matched_instance.github.mode.value == "external"
            and effective_github_mode == "managed"
        ):
            raise config_error("external to managed GitHub profile migration is not supported")
        for index, raw in enumerate(field_edits):
            if not isinstance(raw, Mapping):
                raise config_error("field edit must be an object", index=index)
            if set(raw) - {"path", "operation", "value"}:
                raise config_error("field edit contains unknown fields", index=index)
            pointer = raw.get("path")
            operation = raw.get("operation")
            if not isinstance(pointer, str) or pointer not in _EDITABLE_POINTERS:
                if pointer == "/tunnel/profile":
                    raise config_error("tunnel.profile is manager-derived for new candidates")
                raise config_error("field edit path is not editable", path=pointer)
            if pointer in {"/github/config_dir", "/github/binary"} and effective_github_mode == "managed":
                raise config_error("managed GitHub profile path and binary are manager-derived", path=pointer)
            _pointer_parts(pointer)
            if pointer in seen_edit_paths:
                raise config_error("candidate request contains duplicate field edit path", path=pointer)
            seen_edit_paths.add(pointer)
            if operation not in {"set", "clear"}:
                raise config_error("field edit operation must be set or clear", path=pointer)
            if operation == "set":
                if "value" not in raw:
                    raise config_error("set edit requires value", path=pointer)
                value = raw["value"]
            else:
                if "value" in raw:
                    raise config_error("clear edit must not include value", path=pointer)
                if pointer not in _CLEARABLE_POINTERS:
                    raise config_error("field does not permit clear", path=pointer)
                value = None
            _set_pointer(candidate, pointer, value)
            _mark_provenance(
                provenance,
                value,
                pointer,
                "operator_override",
                f"field_edits[{index}]",
                "explicit operator edit",
            )
            normalized_edits.append({"path": pointer, "operation": operation, **({"value": value} if operation == "set" else {})})

        github = candidate.get("github") if isinstance(candidate.get("github"), dict) else None
        if github is not None and github.get("mode") == "disabled":
            for key in ("config_dir", "binary"):
                if github.get(key) is not None:
                    github[key] = None
                    _mark_provenance(
                        provenance,
                        None,
                        f"/github/{key}",
                        "manager_derived",
                        "/github/mode",
                        f"disabled GitHub mode requires github.{key}=null",
                    )
        elif github is not None and github.get("mode") == "managed":
            instance_id = str(candidate.get("instance_id") or "")
            if not instance_id:
                raise config_error("managed GitHub mode requires an effective instance ID")
            config_dir = str(managed_github_profile_path_for_instance(self.paths, instance_id))
            github["config_dir"] = config_dir
            _mark_provenance(
                provenance,
                config_dir,
                "/github/config_dir",
                "manager_derived",
                instance_id,
                "canonical PM1 managed GitHub profile",
            )
            resolved_gh = shutil.which("gh", path=str(candidate.get("mcp", {}).get("exec_path") or ""))
            if resolved_gh:
                try:
                    resolved_gh = str(Path(resolved_gh).resolve(strict=True))
                except OSError:
                    resolved_gh = None
            github["binary"] = resolved_gh
            _mark_provenance(
                provenance,
                resolved_gh,
                "/github/binary",
                "manager_derived",
                "/mcp/exec_path",
                "resolved from effective MCP execution PATH" if resolved_gh else "GitHub CLI is unavailable on effective MCP execution PATH",
                status="effective" if resolved_gh else "unresolved",
            )
        agent = candidate.get("agent") if isinstance(candidate.get("agent"), dict) else None
        if agent is not None and agent.get("mode") != "external" and agent.get("ssh_auth_sock") is not None:
            agent["ssh_auth_sock"] = None
            _mark_provenance(
                provenance,
                None,
                "/agent/ssh_auth_sock",
                "manager_derived",
                "/agent/mode",
                "non-external agent mode does not use an external socket",
            )

        if access_edits:
            _apply_access_edits(candidate, access_edits, provenance)

        if matched_instance is None:
            candidate["tunnel"]["profile"] = f"workspace-mcp-{candidate['instance_id']}"
            _mark_provenance(
                provenance,
                candidate["tunnel"]["profile"],
                "/tunnel/profile",
                "manager_derived",
                str(candidate["instance_id"]),
                "derived from effective instance ID",
            )

        _validate_candidate(candidate)
        unresolved: list[str] = []
        if not candidate.get("instance_id"):
            unresolved.append("instance_id")
        if not candidate.get("workspace_path"):
            unresolved.append("workspace_path")
        if not candidate.get("mcp", {}).get("port"):
            unresolved.append("mcp.port")
        if not candidate.get("tunnel", {}).get("id"):
            unresolved.append("tunnel.id")
        if not candidate.get("tunnel", {}).get("health_port"):
            unresolved.append("tunnel.health_port")

        port_projection = ports.candidate_projection(candidate)
        warnings = list(discovery.get("warnings", [])) if isinstance(discovery.get("warnings"), list) else []
        if port_projection.get("collision_state") == "conflict":
            warnings.append("One or more candidate endpoints collide with declared/observed endpoints")
        elif port_projection.get("collision_state") == "unknown":
            warnings.append("Endpoint collision state is unknown; create/apply must fail closed until authoritative evidence is available")

        profile = str(candidate.get("tunnel", {}).get("profile") or "")
        profile_owner = next(
            (
                item.instance_id.value
                for item in self.registry.list()
                if item.instance_id.value != candidate.get("instance_id") and item.tunnel.profile == profile
            ),
            None,
        )
        if profile_owner:
            warnings.append(f"Derived tunnel profile collides with declared instance {profile_owner}")

        input_payload = {
            "candidate_request_version": CANDIDATE_REQUEST_VERSION,
            "candidate_version": CANDIDATE_VERSION,
            "discovery_fingerprint": discovery["discovery_fingerprint"],
            "workspace_identity": discovery["workspace_identity"],
            "copy_source": None if copy_source is None else copy_source.instance_id.value,
            "copy_source_fingerprint": None if copy_source is None else copy_source.fingerprint(),
            "field_edits": sorted(normalized_edits, key=lambda item: str(item["path"])),
            "access_edits": access_edits,
            "port_projection_version": PORT_PROJECTION_VERSION,
            "port_projection_fingerprint": port_projection.get("projection_fingerprint"),
            "authentication_status_version": AUTHENTICATION_STATUS_VERSION,
        }
        return {
            "ok": True,
            "candidate_version": CANDIDATE_VERSION,
            "candidate_input_fingerprint": _fingerprint(input_payload),
            "effective_declaration": candidate,
            "candidate_fingerprint": _fingerprint(candidate),
            "field_provenance": [provenance[key] for key in sorted(provenance)],
            "discovery_fingerprint": discovery["discovery_fingerprint"],
            "discovery": discovery,
            "copy_source": None if copy_source is None else copy_source.instance_id.value,
            "copy_source_fingerprint": None if copy_source is None else copy_source.fingerprint(),
            "port_projection": port_projection,
            "warnings": warnings,
            "unresolved_required_operator_fields": unresolved,
            "existing_instance_id": None if matched_instance is None else matched_instance.instance_id.value,
        }
