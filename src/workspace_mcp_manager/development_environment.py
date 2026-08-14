from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from .domain import (
    AgentMode,
    DeploymentTarget,
    DesiredInstance,
    GitRemoteProtocol,
    GithubConfigMode,
)
from .errors import ErrorCode, ManagerError
from .paths import ManagerPaths
from .redaction import sanitized_subprocess_env


GIT_CONFIG_OWNERSHIP_VERSION = "1"
IDENTITY_OWNER_KEY = "workspaceMcpManager.identityOwner"
IDENTITY_VERSION_KEY = "workspaceMcpManager.identityVersion"
IDENTITY_ORIGIN_KEY = "workspaceMcpManager.identityOrigin"
IDENTITY_ORIGINAL_NAME_KEY = "workspaceMcpManager.identityOriginalName"
IDENTITY_ORIGINAL_EMAIL_KEY = "workspaceMcpManager.identityOriginalEmail"
REMOTE_OWNER_KEY = "workspaceMcpManager.remoteOwner"
REMOTE_VERSION_KEY = "workspaceMcpManager.remoteVersion"
REMOTE_NAME_KEY = "workspaceMcpManager.remoteName"
REMOTE_ORIGINAL_URL_KEY = "workspaceMcpManager.remoteOriginalUrl"
HTTPS_HELPER_KEY = "credential.https://github.com.helper"
CORE_SSH_COMMAND_KEY = "core.sshCommand"


class DevObservationStatus(StrEnum):
    ABSENT = "absent"
    EXACT = "exact"
    DIFFERENT = "different"
    FOREIGN = "foreign"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class DevObservation:
    status: DevObservationStatus
    detail: str


def managed_github_profile_path(paths: ManagerPaths, desired: DesiredInstance) -> Path:
    return paths.config_root / "github" / desired.instance_id.value


def managed_agent_runtime_dir(desired: DesiredInstance) -> Path:
    return Path(f"/run/user/{os.getuid()}") / f"workspace-mcp-manager-{desired.instance_id.value}"


def managed_ssh_auth_sock(desired: DesiredInstance) -> Path:
    return managed_agent_runtime_dir(desired) / "ssh-agent.sock"


def effective_ssh_auth_sock(desired: DesiredInstance) -> str | None:
    if desired.agent.mode is AgentMode.MANAGED_SSH_AGENT:
        return str(managed_ssh_auth_sock(desired))
    if desired.agent.mode is AgentMode.EXTERNAL:
        return desired.agent.ssh_auth_sock
    return None


def github_cli_helper_path(paths: ManagerPaths, desired: DesiredInstance) -> Path:
    return paths.account_home / ".local" / "bin" / f"{desired.instance_id.value}-git-credential-github"


def development_subprocess_env(paths: ManagerPaths, desired: DesiredInstance) -> dict[str, str]:
    env = sanitized_subprocess_env(os.environ)
    env["HOME"] = str(paths.account_home)
    env["PATH"] = desired.mcp.exec_path
    env["GIT_TERMINAL_PROMPT"] = "0"
    if desired.github.config_dir:
        env["GH_CONFIG_DIR"] = desired.github.config_dir
    else:
        env.pop("GH_CONFIG_DIR", None)
    socket = effective_ssh_auth_sock(desired)
    if socket:
        env["SSH_AUTH_SOCK"] = socket
    else:
        env.pop("SSH_AUTH_SOCK", None)
    return env


def validate_pm1_host_preconditions(paths: ManagerPaths, desired: DesiredInstance) -> tuple[str, ...]:
    if desired.config_version < 2:
        return ()
    problems: list[str] = []
    if desired.github.mode is GithubConfigMode.MANAGED:
        expected = managed_github_profile_path(paths, desired)
        if desired.github.config_dir != str(expected):
            problems.append(f"managed GitHub profile path must be {expected}")
    if desired.github.binary is not None:
        candidate = Path(desired.github.binary)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            problems.append("configured GitHub CLI executable is unavailable")
    if desired.git.remote is not None and desired.git.remote.protocol is GitRemoteProtocol.SSH:
        candidate = Path("/usr/bin/ssh")
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            problems.append("/usr/bin/ssh is unavailable")
    if desired.agent.mode is AgentMode.MANAGED_SSH_AGENT:
        candidate = Path("/usr/bin/ssh-agent")
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            problems.append("/usr/bin/ssh-agent is unavailable")
    elif desired.agent.mode is AgentMode.EXTERNAL:
        socket = Path(desired.agent.ssh_auth_sock or "")
        try:
            mode = socket.stat().st_mode
        except OSError:
            problems.append("external SSH_AUTH_SOCK is unavailable")
        else:
            if not stat.S_ISSOCK(mode):
                problems.append("external SSH_AUTH_SOCK is not a Unix socket")
    return tuple(problems)


def _github_repo_path(url: str) -> str | None:
    value = url.strip()
    if not value:
        return None
    path: str | None = None
    if value.startswith("git@github.com:"):
        path = value[len("git@github.com:") :]
    elif "://" in value:
        parsed = urlparse(value)
        if parsed.hostname != "github.com" or parsed.password is not None:
            return None
        if parsed.scheme == "https" and parsed.username is not None:
            return None
        if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
            return None
        if parsed.scheme not in {"https", "ssh"}:
            return None
        path = parsed.path.lstrip("/")
    if path is None:
        return None
    if path.endswith(".git"):
        path = path[:-4]
    pieces = path.split("/")
    if len(pieces) != 2 or not all(pieces) or any(piece in {".", ".."} for piece in pieces):
        return None
    return "/".join(pieces)


def canonical_github_url(repo_path: str, protocol: GitRemoteProtocol) -> str:
    if protocol is GitRemoteProtocol.SSH:
        return f"git@github.com:{repo_path}.git"
    return f"https://github.com/{repo_path}.git"


def git_ssh_command(desired: DesiredInstance) -> str:
    socket = effective_ssh_auth_sock(desired)
    if not socket:
        raise ManagerError(ErrorCode.CONFIG_INVALID, "SSH Git transport requires an agent socket")
    identity_agent = shlex.quote(f"IdentityAgent={socket}")
    return f"/usr/bin/ssh -o BatchMode=yes -o {identity_agent}"


def transport_is_requested(desired: DesiredInstance) -> bool:
    return (
        desired.lifecycle.deployment is DeploymentTarget.PRESENT
        and desired.git.remote is not None
    )


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    exit_code: int
    stdout: str
    stderr: str


class DevelopmentEnvironmentReconciler:
    def __init__(self, paths: ManagerPaths) -> None:
        self.paths = paths

    @staticmethod
    def _git_binary(desired: DesiredInstance) -> str | None:
        return shutil.which("git", path=desired.mcp.exec_path)

    def _run(self, desired: DesiredInstance, args: Sequence[str]) -> GitCommandResult:
        git = self._git_binary(desired)
        if not git:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "Git executable is unavailable on mcp.exec_path")
        completed = subprocess.run(
            [git, "-C", desired.workspace_path, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=20.0,
            check=False,
            env=development_subprocess_env(self.paths, desired),
        )
        return GitCommandResult(completed.returncode, completed.stdout, completed.stderr)

    def repository_present(self, desired: DesiredInstance) -> bool:
        try:
            result = self._run(desired, ("rev-parse", "--is-inside-work-tree"))
        except (ManagerError, OSError, subprocess.TimeoutExpired):
            return False
        return result.exit_code == 0 and result.stdout.strip() == "true"

    def local_config_path(self, desired: DesiredInstance) -> Path:
        result = self._run(desired, ("rev-parse", "--git-path", "config"))
        if result.exit_code != 0 or not result.stdout.strip():
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "cannot resolve repository-local Git config path")
        path = Path(result.stdout.strip())
        if not path.is_absolute():
            path = Path(desired.workspace_path) / path
        return path.resolve(strict=False)

    def _get(self, desired: DesiredInstance, key: str) -> str | None:
        result = self._run(desired, ("config", "--local", "--get", key))
        if result.exit_code == 1:
            return None
        if result.exit_code != 0:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"cannot inspect local Git key {key}")
        return result.stdout.rstrip("\n")

    def _get_all(self, desired: DesiredInstance, key: str) -> tuple[str, ...]:
        result = self._run(desired, ("config", "--local", "--get-all", key))
        if result.exit_code == 1:
            return ()
        if result.exit_code != 0:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"cannot inspect local Git key {key}")
        return tuple(result.stdout.rstrip("\n").splitlines()) if result.stdout else ()

    def _set(self, desired: DesiredInstance, key: str, value: str) -> None:
        result = self._run(desired, ("config", "--local", "--replace-all", key, value))
        if result.exit_code != 0:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"cannot set local Git key {key}")

    def _unset(self, desired: DesiredInstance, key: str) -> None:
        if not self._get_all(desired, key):
            return
        result = self._run(desired, ("config", "--local", "--unset-all", key))
        if result.exit_code != 0:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"cannot unset local Git key {key}")

    def observe_identity(self, desired: DesiredInstance) -> DevObservation:
        if desired.config_version < 2:
            return DevObservation(DevObservationStatus.EXACT, "v1 has no managed Git identity")
        if not self.repository_present(desired):
            if desired.git.identity is None or desired.lifecycle.deployment is DeploymentTarget.ABSENT:
                return DevObservation(DevObservationStatus.EXACT, "Git identity management is not requested")
            return DevObservation(DevObservationStatus.UNVERIFIED, "workspace is not a Git working tree")
        owner = self._get(desired, IDENTITY_OWNER_KEY)
        version = self._get(desired, IDENTITY_VERSION_KEY)
        name = self._get(desired, "user.name")
        email = self._get(desired, "user.email")
        requested = desired.git.identity if desired.lifecycle.deployment is DeploymentTarget.PRESENT else None
        if requested is None:
            if owner == desired.instance_id.value and version != GIT_CONFIG_OWNERSHIP_VERSION:
                return DevObservation(DevObservationStatus.FOREIGN, "Git identity ownership version is unsupported")
            if owner == desired.instance_id.value:
                origin = self._get(desired, IDENTITY_ORIGIN_KEY)
                if origin not in {"created", "adopted"}:
                    return DevObservation(DevObservationStatus.FOREIGN, "Git identity ownership metadata is incomplete")
                if origin == "adopted" and (
                    self._get(desired, IDENTITY_ORIGINAL_NAME_KEY) is None
                    or self._get(desired, IDENTITY_ORIGINAL_EMAIL_KEY) is None
                ):
                    return DevObservation(DevObservationStatus.FOREIGN, "adopted Git identity original values are missing")
                return DevObservation(DevObservationStatus.DIFFERENT, "manager-owned Git identity must be released")
            return DevObservation(DevObservationStatus.EXACT, "Git identity is not manager-owned")
        if owner is None and version is None:
            if name is None and email is None:
                return DevObservation(DevObservationStatus.ABSENT, "Git identity is absent")
            if name == requested.name and email == requested.email:
                return DevObservation(DevObservationStatus.ABSENT, "exact existing Git identity may be adopted")
            return DevObservation(DevObservationStatus.FOREIGN, "existing Git identity differs from desired state")
        if owner != desired.instance_id.value or version != GIT_CONFIG_OWNERSHIP_VERSION:
            return DevObservation(DevObservationStatus.FOREIGN, "Git identity ownership marker is foreign or unsupported")
        origin = self._get(desired, IDENTITY_ORIGIN_KEY)
        if origin not in {"created", "adopted"}:
            return DevObservation(DevObservationStatus.FOREIGN, "Git identity ownership metadata is incomplete")
        if origin == "adopted" and (
            self._get(desired, IDENTITY_ORIGINAL_NAME_KEY) is None
            or self._get(desired, IDENTITY_ORIGINAL_EMAIL_KEY) is None
        ):
            return DevObservation(DevObservationStatus.FOREIGN, "adopted Git identity original values are missing")
        if name == requested.name and email == requested.email:
            return DevObservation(DevObservationStatus.EXACT, "manager-owned Git identity matches desired state")
        return DevObservation(DevObservationStatus.DIFFERENT, "manager-owned Git identity differs from desired state")

    def observe_transport(self, desired: DesiredInstance) -> DevObservation:
        if desired.config_version < 2:
            return DevObservation(DevObservationStatus.EXACT, "v1 has no managed Git remote")
        if not self.repository_present(desired):
            if desired.git.remote is None or desired.lifecycle.deployment is DeploymentTarget.ABSENT:
                return DevObservation(DevObservationStatus.EXACT, "Git remote management is not requested")
            return DevObservation(DevObservationStatus.UNVERIFIED, "workspace is not a Git working tree")
        owner = self._get(desired, REMOTE_OWNER_KEY)
        version = self._get(desired, REMOTE_VERSION_KEY)
        requested = desired.git.remote if desired.lifecycle.deployment is DeploymentTarget.PRESENT else None
        if requested is None:
            if owner == desired.instance_id.value and version != GIT_CONFIG_OWNERSHIP_VERSION:
                return DevObservation(DevObservationStatus.FOREIGN, "Git transport ownership version is unsupported")
            if owner == desired.instance_id.value:
                if self._get(desired, REMOTE_NAME_KEY) is None or self._get(desired, REMOTE_ORIGINAL_URL_KEY) is None:
                    return DevObservation(DevObservationStatus.FOREIGN, "Git transport ownership metadata is incomplete")
                return DevObservation(DevObservationStatus.DIFFERENT, "manager-owned Git remote settings must be released")
            return DevObservation(DevObservationStatus.EXACT, "Git remote is not manager-owned")
        url = self._get(desired, f"remote.{requested.name}.url")
        if url is None:
            return DevObservation(DevObservationStatus.FOREIGN, "configured Git remote does not exist")
        repo_path = _github_repo_path(url)
        if repo_path is None:
            return DevObservation(DevObservationStatus.FOREIGN, "configured remote is not a supported GitHub URL")
        desired_url = canonical_github_url(repo_path, requested.protocol)
        helpers = self._get_all(desired, HTTPS_HELPER_KEY)
        ssh_commands = self._get_all(desired, CORE_SSH_COMMAND_KEY)
        helper_path = str(github_cli_helper_path(self.paths, desired))
        helper_exact = helpers == ((helper_path,) if requested.protocol is GitRemoteProtocol.HTTPS_GH else ())
        ssh_exact = ssh_commands == ((git_ssh_command(desired),) if requested.protocol is GitRemoteProtocol.SSH else ())
        if owner is None and version is None:
            if url != desired_url:
                return DevObservation(DevObservationStatus.FOREIGN, "existing Git remote protocol differs from desired state")
            if helpers or ssh_commands:
                return DevObservation(DevObservationStatus.FOREIGN, "existing repository-local Git transport settings are foreign")
            return DevObservation(DevObservationStatus.ABSENT, "exact existing Git remote may be adopted")
        if owner != desired.instance_id.value or version != GIT_CONFIG_OWNERSHIP_VERSION:
            return DevObservation(DevObservationStatus.FOREIGN, "Git remote ownership marker is foreign or unsupported")
        if self._get(desired, REMOTE_NAME_KEY) != requested.name:
            return DevObservation(DevObservationStatus.FOREIGN, "Git remote ownership metadata names a different remote")
        if self._get(desired, REMOTE_ORIGINAL_URL_KEY) is None:
            return DevObservation(DevObservationStatus.FOREIGN, "Git remote original URL metadata is missing")
        if url == desired_url and helper_exact and ssh_exact:
            return DevObservation(DevObservationStatus.EXACT, "manager-owned Git remote matches desired state")
        return DevObservation(DevObservationStatus.DIFFERENT, "manager-owned Git remote differs from desired state")

    def converge_identity(self, desired: DesiredInstance) -> None:
        requested = desired.git.identity if desired.lifecycle.deployment is DeploymentTarget.PRESENT else None
        owner = self._get(desired, IDENTITY_OWNER_KEY)
        if requested is None:
            if owner != desired.instance_id.value:
                return
            origin = self._get(desired, IDENTITY_ORIGIN_KEY)
            if origin == "adopted":
                original_name = self._get(desired, IDENTITY_ORIGINAL_NAME_KEY)
                original_email = self._get(desired, IDENTITY_ORIGINAL_EMAIL_KEY)
                if original_name is None or original_email is None:
                    raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "adopted Git identity is missing original values")
                self._set(desired, "user.name", original_name)
                self._set(desired, "user.email", original_email)
            elif origin == "created":
                self._unset(desired, "user.name")
                self._unset(desired, "user.email")
            else:
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "Git identity origin marker is invalid")
            for key in (
                IDENTITY_OWNER_KEY,
                IDENTITY_VERSION_KEY,
                IDENTITY_ORIGIN_KEY,
                IDENTITY_ORIGINAL_NAME_KEY,
                IDENTITY_ORIGINAL_EMAIL_KEY,
            ):
                self._unset(desired, key)
            return

        if owner is None:
            current_name = self._get(desired, "user.name")
            current_email = self._get(desired, "user.email")
            if current_name is None and current_email is None:
                origin = "created"
            elif current_name == requested.name and current_email == requested.email:
                origin = "adopted"
                self._set(desired, IDENTITY_ORIGINAL_NAME_KEY, current_name)
                self._set(desired, IDENTITY_ORIGINAL_EMAIL_KEY, current_email)
            else:
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "refusing to replace foreign Git identity")
            self._set(desired, IDENTITY_ORIGIN_KEY, origin)
            self._set(desired, IDENTITY_OWNER_KEY, desired.instance_id.value)
            self._set(desired, IDENTITY_VERSION_KEY, GIT_CONFIG_OWNERSHIP_VERSION)
        self._set(desired, "user.name", requested.name)
        self._set(desired, "user.email", requested.email)

    def converge_transport(self, desired: DesiredInstance) -> None:
        requested = desired.git.remote if desired.lifecycle.deployment is DeploymentTarget.PRESENT else None
        owner = self._get(desired, REMOTE_OWNER_KEY)
        if requested is None:
            if owner != desired.instance_id.value:
                return
            original = self._get(desired, REMOTE_ORIGINAL_URL_KEY)
            managed_remote = self._get(desired, REMOTE_NAME_KEY)
            if original is None or managed_remote is None:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    "managed Git transport is missing reversible ownership metadata",
                )
            self._set(desired, f"remote.{managed_remote}.url", original)
            self._unset(desired, HTTPS_HELPER_KEY)
            self._unset(desired, CORE_SSH_COMMAND_KEY)
            for key in (REMOTE_OWNER_KEY, REMOTE_VERSION_KEY, REMOTE_NAME_KEY, REMOTE_ORIGINAL_URL_KEY):
                self._unset(desired, key)
            return

        key = f"remote.{requested.name}.url"
        current = self._get(desired, key)
        if current is None:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "configured Git remote does not exist")
        repo_path = _github_repo_path(current)
        if repo_path is None:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "configured Git remote is not a supported GitHub URL")
        desired_url = canonical_github_url(repo_path, requested.protocol)
        if owner is None:
            if current != desired_url or self._get_all(desired, HTTPS_HELPER_KEY) or self._get_all(desired, CORE_SSH_COMMAND_KEY):
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "refusing to replace foreign Git remote settings")
            self._set(desired, REMOTE_ORIGINAL_URL_KEY, current)
            self._set(desired, REMOTE_NAME_KEY, requested.name)
            self._set(desired, REMOTE_OWNER_KEY, desired.instance_id.value)
            self._set(desired, REMOTE_VERSION_KEY, GIT_CONFIG_OWNERSHIP_VERSION)
        self._set(desired, key, desired_url)
        if requested.protocol is GitRemoteProtocol.HTTPS_GH:
            self._set(desired, HTTPS_HELPER_KEY, str(github_cli_helper_path(self.paths, desired)))
            self._unset(desired, CORE_SSH_COMMAND_KEY)
        else:
            self._unset(desired, HTTPS_HELPER_KEY)
            self._set(desired, CORE_SSH_COMMAND_KEY, git_ssh_command(desired))
