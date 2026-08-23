from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .development_environment import (
    MANAGED_GITHUB_ENV_REMOVE,
    _github_repo_path,
    effective_ssh_auth_sock,
    managed_github_profile_path,
    managed_github_subprocess_env,
)
from .domain import DeploymentTarget, DesiredInstance, GitRemoteProtocol, GithubConfigMode
from .errors import ErrorCode, ManagerError
from .generation import OWNER_PREFIX
from .github_auth_helper import (
    HELPER_CANCELLED,
    HELPER_INPUT_EMPTY,
    HELPER_INPUT_TOO_LARGE,
    HELPER_INTERRUPTED,
    HELPER_LOGIN_FAILED,
    HELPER_OK,
    HELPER_TIMEOUT,
    HELPER_TTY_REQUIRED,
    QUALIFIED_LOGIN_ARGS,
)
from .github_token_guidance import PROVIDER_TOKEN_URL, fine_grained_pat_guidance
from .paths import ManagerPaths
from .registry import InstanceRegistry


GITHUB_ACCESS_PROJECTION_VERSION = 1
GITHUB_ACCESS_VERIFICATION_RECORD_VERSION = 1
GITHUB_HOST = "github.com"
SUPPORTED_GH_VERSIONS = frozenset({"2.45.0"})
AUTH_STATUS_ARGS = ("auth", "status", "--hostname", GITHUB_HOST)
ACCOUNT_ARGS = ("api", "--hostname", GITHUB_HOST, "user", "--jq", ".login")
VERIFICATION_FRESH_SECONDS = 300
LOCK_TIMEOUT_SECONDS = 2.0
COMMAND_TIMEOUT_SECONDS = 12.0
COMMAND_OUTPUT_LIMIT = 16 * 1024
MCP_TOOL_TIMEOUT_MS = 8_000
MCP_OUTPUT_LIMIT = 8_192
MCP_PREVIEW_LIMIT = 4_096
MCP_PROTOCOL_VERSION = "2025-11-25"
QUALIFIED_MCP_VERSIONS = frozenset({"0.2.2", "0.3.0"})

LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
GH_VERSION_RE = re.compile(r"^gh version ([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)")


class GithubAccessBusy(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BoundedCommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    output_overflow: bool


@dataclass(frozen=True, slots=True)
class GhResolution:
    binary: str | None
    version: str | None
    raw_version: str | None
    available: bool
    supported: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            name = handle.name
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if name:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass


def _settle(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _run_bounded(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    output_limit: int = COMMAND_OUTPUT_LIMIT,
) -> BoundedCommandResult:
    """Run a non-secret command with a hard in-memory output cap."""

    command = tuple(str(item) for item in argv)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=False,
            env=dict(env),
        )
    except OSError:
        return BoundedCommandResult(command, None, b"", b"", False, False)

    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    guard = threading.Lock()
    total = 0

    def reader(pipe: Any, target: bytearray) -> None:
        nonlocal total
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    return
                with guard:
                    remaining = max(0, output_limit - total)
                    if remaining:
                        kept = chunk[:remaining]
                        target.extend(kept)
                        total += len(kept)
                    if len(chunk) > remaining:
                        overflow.set()
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(target=reader, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + max(0.1, timeout)
    timed_out = False
    while process.poll() is None:
        if overflow.is_set():
            _settle(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _settle(process)
            break
        time.sleep(0.01)
    for thread in threads:
        thread.join(timeout=1.0)
    return BoundedCommandResult(
        command,
        process.returncode,
        bytes(stdout),
        bytes(stderr),
        timed_out,
        overflow.is_set(),
    )


class GithubAccessService:
    def __init__(self, paths: ManagerPaths, registry: InstanceRegistry) -> None:
        self.paths = paths
        self.registry = registry
        self.state_root = paths.state_root / "github-access"

    def _record_path(self, instance_id: str) -> Path:
        return self.state_root / f"{instance_id}.json"

    @contextmanager
    def _operation_lock(self, instance_id: str) -> Iterator[None]:
        lock_root = self.state_root / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(lock_root, 0o700)
        except OSError:
            pass
        path = lock_root / f"{instance_id}.lock"
        with path.open("a+", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise GithubAccessBusy(instance_id)
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _metadata(path: Path) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _managed_profile_state(self, desired: DesiredInstance) -> dict[str, Any]:
        expected = managed_github_profile_path(self.paths, desired)
        if desired.github.config_dir != str(expected):
            return {"state": "invalid", "present": expected.exists(), "ownership": "manager", "path": str(expected)}
        metadata = self._metadata(expected)
        if metadata is None:
            return {"state": "absent", "present": False, "ownership": "manager", "path": str(expected)}
        if stat.S_ISLNK(metadata.st_mode):
            return {"state": "unsafe", "present": True, "ownership": "unknown", "path": str(expected)}
        if not stat.S_ISDIR(metadata.st_mode):
            return {"state": "invalid", "present": True, "ownership": "unknown", "path": str(expected)}
        if metadata.st_uid != os.getuid():
            return {"state": "foreign", "present": True, "ownership": "foreign", "path": str(expected)}

        marker = expected / ".owner"
        marker_metadata = self._metadata(marker)
        if marker_metadata is None:
            return {"state": "foreign", "present": True, "ownership": "unknown", "path": str(expected)}
        if stat.S_ISLNK(marker_metadata.st_mode) or not stat.S_ISREG(marker_metadata.st_mode):
            return {"state": "unsafe", "present": True, "ownership": "unknown", "path": str(expected)}
        if marker_metadata.st_uid != os.getuid():
            return {"state": "foreign", "present": True, "ownership": "foreign", "path": str(expected)}
        try:
            marker_text = marker.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            return {"state": "invalid", "present": True, "ownership": "unknown", "path": str(expected)}
        if marker_text != f"{OWNER_PREFIX}{desired.instance_id.value}\n":
            return {"state": "foreign", "present": True, "ownership": "foreign", "path": str(expected)}
        if stat.S_IMODE(metadata.st_mode) != 0o700 or stat.S_IMODE(marker_metadata.st_mode) != 0o600:
            return {"state": "unsafe", "present": True, "ownership": "manager", "path": str(expected)}
        return {"state": "ready", "present": True, "ownership": "manager", "path": str(expected)}

    def _external_profile_state(self, desired: DesiredInstance) -> dict[str, Any]:
        path_text = desired.github.config_dir
        if not path_text:
            return {"state": "invalid", "present": False, "ownership": "external", "path": None}
        path = Path(path_text)
        metadata = self._metadata(path)
        if metadata is None:
            return {"state": "absent", "present": False, "ownership": "external", "path": path_text}
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return {"state": "unsafe", "present": True, "ownership": "external", "path": path_text}
        return {"state": "ready", "present": True, "ownership": "external", "path": path_text}

    def _profile(self, desired: DesiredInstance) -> dict[str, Any]:
        if desired.github.mode is GithubConfigMode.DISABLED:
            return {"mode": "disabled", "state": "not_applicable", "path": None, "present": False, "ownership": "none"}
        if desired.github.mode is GithubConfigMode.EXTERNAL:
            return {"mode": "external", **self._external_profile_state(desired)}
        return {"mode": "managed", **self._managed_profile_state(desired)}

    def ensure_managed_profile(self, instance_id: str) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        if desired.config_version < 2 or desired.github.mode is not GithubConfigMode.MANAGED:
            raise ManagerError(ErrorCode.CONFIG_INVALID, "managed GitHub profile provisioning requires registered github.mode=managed")
        profile = managed_github_profile_path(self.paths, desired)
        if desired.github.config_dir != str(profile):
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "registered managed GitHub profile is not canonical")
        root = profile.parent
        root_meta = self._metadata(root)
        if root_meta is not None:
            if stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode) or root_meta.st_uid != os.getuid():
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "managed GitHub profile root is unsafe or foreign")
        else:
            root.mkdir(parents=True, mode=0o700)
        try:
            os.chmod(root, 0o700)
        except OSError as exc:
            raise ManagerError(ErrorCode.IO_ERROR, "cannot secure managed GitHub profile root") from exc

        profile_metadata = self._metadata(profile)
        if profile_metadata is None:
            try:
                profile.mkdir(mode=0o700)
            except OSError as exc:
                raise ManagerError(ErrorCode.IO_ERROR, "cannot create managed GitHub profile") from exc
            marker = profile / ".owner"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(marker, flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(f"{OWNER_PREFIX}{desired.instance_id.value}\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise ManagerError(ErrorCode.IO_ERROR, "cannot create managed GitHub profile owner marker") from exc
        else:
            metadata = profile_metadata
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "managed GitHub profile is unsafe or foreign")
            marker = profile / ".owner"
            marker_meta = self._metadata(marker)
            if marker_meta is None or marker.is_symlink() or not stat.S_ISREG(marker_meta.st_mode) or marker_meta.st_uid != os.getuid():
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "managed GitHub profile owner marker is absent, unsafe, or foreign")
            try:
                owner = marker.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeError) as exc:
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "cannot verify managed GitHub profile owner marker") from exc
            if owner != f"{OWNER_PREFIX}{desired.instance_id.value}\n":
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "managed GitHub profile belongs to another owner")

        try:
            os.chmod(profile, 0o700)
            os.chmod(profile / ".owner", 0o600)
        except OSError as exc:
            raise ManagerError(ErrorCode.IO_ERROR, "cannot enforce managed GitHub profile permissions") from exc
        result = self._managed_profile_state(desired)
        if result["state"] != "ready":
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "managed GitHub profile failed post-provision safety verification")
        return result

    def _storage_state(self, desired: DesiredInstance) -> tuple[bool, str | None]:
        if desired.github.mode is not GithubConfigMode.MANAGED:
            return False, "PROFILE_STORAGE_UNSAFE"
        path = managed_github_profile_path(self.paths, desired) / "hosts.yml"
        metadata = self._metadata(path)
        if metadata is None:
            return False, "PROFILE_MISSING"
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return False, "PROFILE_STORAGE_UNSAFE"
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            return False, "PROFILE_STORAGE_UNSAFE"
        if path.parent != managed_github_profile_path(self.paths, desired):
            return False, "PROFILE_STORAGE_UNSAFE"
        return True, None

    def _resolve_gh(self, desired: DesiredInstance) -> GhResolution:
        binary = desired.github.binary
        if binary is None:
            binary = shutil.which("gh", path=desired.mcp.exec_path)
        if binary is None:
            return GhResolution(None, None, None, False, False)
        candidate = Path(binary)
        try:
            if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
                return GhResolution(str(candidate), None, None, False, False)
            real = str(candidate.resolve(strict=True))
        except OSError:
            return GhResolution(str(candidate), None, None, False, False)

        env = dict(os.environ)
        for key in MANAGED_GITHUB_ENV_REMOVE:
            env.pop(key, None)
        env["HOME"] = str(self.paths.account_home)
        env["PATH"] = desired.mcp.exec_path
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GH_PROMPT_DISABLED"] = "1"
        env["GH_NO_UPDATE_NOTIFIER"] = "1"
        result = _run_bounded([real, "--version"], env=env, timeout=5.0, output_limit=8_192)
        if result.returncode != 0 or result.timed_out or result.output_overflow:
            return GhResolution(real, None, None, True, False)
        text = result.stdout.decode("utf-8", errors="replace").strip()
        first = text.splitlines()[0] if text else ""
        match = GH_VERSION_RE.match(first)
        version = match.group(1) if match else None
        return GhResolution(real, version, text[:4_096], True, version in SUPPORTED_GH_VERSIONS)

    def _repository(self, desired: DesiredInstance) -> dict[str, str] | None:
        if desired.git.remote is None:
            return None
        git = shutil.which("git", path=desired.mcp.exec_path)
        if not git:
            return None
        env = dict(os.environ)
        for key in MANAGED_GITHUB_ENV_REMOVE:
            env.pop(key, None)
        env["HOME"] = str(self.paths.account_home)
        env["PATH"] = desired.mcp.exec_path
        env["GIT_TERMINAL_PROMPT"] = "0"
        result = _run_bounded(
            [git, "-C", desired.workspace_path, "remote", "get-url", desired.git.remote.name],
            env=env,
            timeout=5.0,
            output_limit=4_096,
        )
        if result.returncode != 0 or result.timed_out or result.output_overflow:
            return None
        path = _github_repo_path(result.stdout.decode("utf-8", errors="replace").strip())
        if not path:
            return None
        return {"host": GITHUB_HOST, "repository": path}

    def _read_record(self, instance_id: str) -> dict[str, Any] | None:
        path = self._record_path(instance_id)
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
            value = json.loads(text)
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("github_access_verification_record_version") != GITHUB_ACCESS_VERIFICATION_RECORD_VERSION:
            return None
        return value

    def _context(
        self,
        desired: DesiredInstance,
        *,
        profile: Mapping[str, Any],
        gh: GhResolution,
        repository: Mapping[str, Any] | None,
    ) -> str:
        return _sha256(
            {
                "instance_id": desired.instance_id.value,
                "profile_mode": desired.github.mode.value,
                "profile_path": profile.get("path"),
                "profile_state": profile.get("state"),
                "profile_ownership": profile.get("ownership"),
                "github_binary": gh.binary,
                "github_version": gh.version,
                "repository": repository,
                "deployment": desired.lifecycle.deployment.value,
                "mcp": {
                    "service": f"coding-tools-mcp-{desired.instance_id.value}.service",
                    "binary": desired.mcp.binary,
                    "host": desired.mcp.host,
                    "port": desired.mcp.port,
                },
            }
        )

    @staticmethod
    def _freshness(record: Mapping[str, Any] | None, context: str) -> tuple[str, bool, str | None]:
        if record is None:
            return "never", False, None
        if record.get("verification_context_fingerprint") != context:
            return "stale", False, "VERIFICATION_CONTEXT_CHANGED"
        observed = record.get("observed_at")
        if not isinstance(observed, str):
            return "stale", False, "VERIFICATION_UNKNOWN"
        try:
            timestamp = datetime.fromisoformat(observed)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except ValueError:
            return "stale", False, "VERIFICATION_UNKNOWN"
        age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
        return ("fresh", True, None) if 0 <= age <= VERIFICATION_FRESH_SECONDS else ("stale", True, None)

    def status(self, instance_id: str) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        profile = self._profile(desired)
        gh = self._resolve_gh(desired)
        repository = self._repository(desired)
        context = self._context(desired, profile=profile, gh=gh, repository=repository)
        record = self._read_record(instance_id)
        freshness, context_valid, context_reason = self._freshness(record, context)

        authentication_state = "not_applicable" if desired.github.mode is GithubConfigMode.DISABLED else "unknown"
        account: str | None = None
        verification = {
            "profile": "not_applicable" if desired.github.mode is GithubConfigMode.DISABLED else "unknown",
            "manager_environment": "not_applicable" if desired.github.mode is GithubConfigMode.DISABLED else "unknown",
            "mcp_execution": "not_applicable" if desired.github.mode is GithubConfigMode.DISABLED else "unknown",
            "repository_read": "not_applicable" if desired.github.mode is GithubConfigMode.DISABLED else "unknown",
            "observed_at": None,
            "freshness": freshness,
            "context_valid": context_valid,
            "reason_code": context_reason,
        }
        if record is not None:
            authentication_state = str(record.get("authentication_state") or authentication_state)
            account_value = record.get("account")
            account = account_value if isinstance(account_value, str) and LOGIN_RE.fullmatch(account_value) else None
            for key in ("profile", "manager_environment", "mcp_execution", "repository_read"):
                value = record.get(f"{key}_result")
                if isinstance(value, str):
                    verification[key] = value
            verification["observed_at"] = record.get("observed_at")
            if context_reason is None:
                verification["reason_code"] = record.get("reason_code")

        if desired.github.mode is GithubConfigMode.DISABLED:
            cli_state = "available" if gh.available else "unavailable"
        elif not gh.available:
            cli_state = "unavailable"
        elif not gh.supported:
            cli_state = "unsupported"
        else:
            cli_state = "available"

        ssh_socket = effective_ssh_auth_sock(desired)
        ssh_status = "not_applicable"
        if ssh_socket:
            try:
                ssh_status = "available" if stat.S_ISSOCK(Path(ssh_socket).stat().st_mode) else "unavailable"
            except OSError:
                ssh_status = "unavailable"

        protocol = desired.git.remote.protocol.value if desired.git.remote is not None else None
        git_status = "not_configured"
        if protocol == GitRemoteProtocol.SSH.value:
            git_status = "ready" if ssh_status == "available" else "not_ready"
        elif protocol == GitRemoteProtocol.HTTPS_GH.value:
            git_status = (
                "ready"
                if freshness == "fresh"
                and context_valid
                and authentication_state == "authenticated"
                and verification["manager_environment"] == "passed"
                else "not_ready"
            )

        configure_allowed = (
            desired.github.mode is GithubConfigMode.MANAGED
            and profile.get("state") in {"ready", "absent"}
            and gh.available
            and gh.supported
        )
        return {
            "ok": True,
            "github_access_projection_version": GITHUB_ACCESS_PROJECTION_VERSION,
            "instance_id": desired.instance_id.value,
            "profile": profile,
            "github_cli": {
                "binary": gh.binary,
                "available": gh.available,
                "state": cli_state,
                "version": gh.version,
                "supported": gh.supported,
            },
            "authentication": {"state": authentication_state, "host": GITHUB_HOST, "account": account},
            "verification": verification,
            "repository": repository,
            "git_transport": {"protocol": protocol, "status": git_status},
            "ssh_agent": {"status": ssh_status},
            "setup": {
                "enable_managed_allowed": desired.github.mode is GithubConfigMode.DISABLED,
                "configure_allowed": configure_allowed,
                "reconfigure_allowed": configure_allowed,
                "token_management_url": PROVIDER_TOKEN_URL,
                "token_guidance": fine_grained_pat_guidance(
                    str(repository.get("repository")) if isinstance(repository, Mapping) and repository.get("repository") else None
                ),
            },
        }

    def _write_record(
        self,
        desired: DesiredInstance,
        *,
        gh: GhResolution,
        profile: Mapping[str, Any],
        repository: Mapping[str, Any] | None,
        authentication_state: str,
        account: str | None,
        profile_result: str,
        manager_environment_result: str,
        mcp_execution_result: str,
        repository_read_result: str,
        reason_code: str | None,
    ) -> dict[str, Any]:
        record = {
            "github_access_verification_record_version": GITHUB_ACCESS_VERIFICATION_RECORD_VERSION,
            "instance_id": desired.instance_id.value,
            "github_binary": gh.binary,
            "github_version": gh.version,
            "profile_mode": desired.github.mode.value,
            "profile_path": profile.get("path"),
            "profile_state": profile.get("state"),
            "authentication_state": authentication_state,
            "account": account,
            "profile_result": profile_result,
            "manager_environment_result": manager_environment_result,
            "mcp_execution_result": mcp_execution_result,
            "repository_read_result": repository_read_result,
            "reason_code": reason_code,
            "verification_context_fingerprint": self._context(desired, profile=profile, gh=gh, repository=repository),
            "observed_at": _utc_now(),
        }
        _atomic_json(self._record_path(desired.instance_id.value), record)
        return record

    def verify(self, instance_id: str) -> dict[str, Any]:
        try:
            with self._operation_lock(instance_id):
                return self._verify_locked(instance_id)
        except GithubAccessBusy:
            payload = self.status(instance_id)
            payload["ok"] = False
            payload["verification"]["reason_code"] = "GITHUB_ACCESS_BUSY"
            return payload

    def _verify_locked(self, instance_id: str) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        profile = self._profile(desired)
        gh = self._resolve_gh(desired)
        repository = self._repository(desired)

        if desired.github.mode is GithubConfigMode.DISABLED:
            self._write_record(
                desired,
                gh=gh,
                profile=profile,
                repository=repository,
                authentication_state="not_applicable",
                account=None,
                profile_result="not_applicable",
                manager_environment_result="not_applicable",
                mcp_execution_result="not_applicable",
                repository_read_result="not_applicable",
                reason_code=None,
            )
            return self.status(instance_id)

        if desired.github.mode is GithubConfigMode.EXTERNAL:
            self._write_record(
                desired,
                gh=gh,
                profile=profile,
                repository=repository,
                authentication_state="unknown",
                account=None,
                profile_result="unavailable",
                manager_environment_result="unavailable",
                mcp_execution_result="unavailable",
                repository_read_result="unavailable" if repository else "not_applicable",
                reason_code="EXTERNAL_PROFILE_WRITE_PROTECTION_UNAVAILABLE",
            )
            return self.status(instance_id)

        if profile.get("state") != "ready":
            reason = {
                "absent": "PROFILE_MISSING",
                "foreign": "PROFILE_FOREIGN",
                "unsafe": "PROFILE_STORAGE_UNSAFE",
            }.get(str(profile.get("state")), "PROFILE_INVALID")
            self._write_record(
                desired,
                gh=gh,
                profile=profile,
                repository=repository,
                authentication_state="unknown",
                account=None,
                profile_result="failed",
                manager_environment_result="unknown",
                mcp_execution_result="unknown",
                repository_read_result="unknown" if repository else "not_applicable",
                reason_code=reason,
            )
            return self.status(instance_id)

        if not gh.available or gh.binary is None:
            self._write_record(
                desired,
                gh=gh,
                profile=profile,
                repository=repository,
                authentication_state="unknown",
                account=None,
                profile_result="unavailable",
                manager_environment_result="unavailable",
                mcp_execution_result="unavailable",
                repository_read_result="unavailable" if repository else "not_applicable",
                reason_code="GH_BINARY_UNAVAILABLE",
            )
            return self.status(instance_id)
        if not gh.supported:
            self._write_record(
                desired,
                gh=gh,
                profile=profile,
                repository=repository,
                authentication_state="unknown",
                account=None,
                profile_result="unavailable",
                manager_environment_result="unavailable",
                mcp_execution_result="unavailable",
                repository_read_result="unavailable" if repository else "not_applicable",
                reason_code="GH_VERSION_UNSUPPORTED",
            )
            return self.status(instance_id)

        hosts_path = managed_github_profile_path(self.paths, desired) / "hosts.yml"
        if self._metadata(hosts_path) is not None:
            storage_ok, storage_reason = self._storage_state(desired)
            if not storage_ok and storage_reason == "PROFILE_STORAGE_UNSAFE":
                self._write_record(
                    desired,
                    gh=gh,
                    profile=profile,
                    repository=repository,
                    authentication_state="unknown",
                    account=None,
                    profile_result="failed",
                    manager_environment_result="unknown",
                    mcp_execution_result="unknown",
                    repository_read_result="unknown" if repository else "not_applicable",
                    reason_code="PROFILE_STORAGE_UNSAFE",
                )
                return self.status(instance_id)

        env = managed_github_subprocess_env(self.paths, desired)
        auth = _run_bounded([gh.binary, *AUTH_STATUS_ARGS], env=env)
        if auth.timed_out:
            auth_state = "unknown"
            reason = "PROVIDER_TIMEOUT"
        elif auth.output_overflow or auth.returncode is None:
            auth_state = "unknown"
            reason = "VERIFICATION_UNKNOWN"
        elif auth.returncode != 0:
            auth_state = "unauthenticated"
            reason = "AUTHENTICATION_MISSING"
        else:
            auth_state = "authenticated"
            reason = None

        account: str | None = None
        profile_result = "passed" if auth_state == "authenticated" else "failed"
        manager_result = "unknown"
        repository_result = "not_applicable" if repository is None else "unknown"
        mcp_result = "unknown"

        if auth_state == "authenticated":
            storage_ok, storage_reason = self._storage_state(desired)
            if not storage_ok:
                profile_result = "failed"
                reason = storage_reason or "PROFILE_STORAGE_UNSAFE"

        if auth_state == "authenticated":
            identity = _run_bounded([gh.binary, *ACCOUNT_ARGS], env=env)
            if identity.timed_out:
                reason = "PROVIDER_TIMEOUT"
                manager_result = "failed"
            elif identity.output_overflow or identity.returncode is None:
                reason = "ACCOUNT_IDENTITY_UNAVAILABLE"
                manager_result = "failed"
            elif identity.returncode != 0:
                reason = "PROVIDER_UNREACHABLE"
                manager_result = "failed"
            else:
                candidate = identity.stdout.decode("utf-8", errors="replace").strip()
                if LOGIN_RE.fullmatch(candidate):
                    account = candidate
                    manager_result = "passed"
                else:
                    reason = "ACCOUNT_IDENTITY_UNAVAILABLE"
                    manager_result = "failed"

        if account and repository is not None:
            repository_name = str(repository["repository"])
            repo_probe = _run_bounded(
                [gh.binary, "api", "--hostname", GITHUB_HOST, f"repos/{repository_name}", "--jq", ".full_name"],
                env=env,
            )
            if repo_probe.returncode == 0 and not repo_probe.timed_out and not repo_probe.output_overflow:
                reported = repo_probe.stdout.decode("utf-8", errors="replace").strip()
                repository_result = "passed" if reported.casefold() == repository_name.casefold() else "failed"
                if repository_result == "failed" and reason is None:
                    reason = "REPOSITORY_NOT_FOUND_OR_ACCESS_DENIED"
            else:
                repository_result = "failed"
                if reason is None:
                    reason = "REPOSITORY_NOT_FOUND_OR_ACCESS_DENIED"

        if account:
            mcp_result, mcp_reason = self._verify_mcp(desired, gh.binary, account)
            if mcp_reason is not None and reason is None:
                reason = mcp_reason
        elif desired.lifecycle.deployment is DeploymentTarget.ABSENT:
            mcp_result = "not_deployed"

        self._write_record(
            desired,
            gh=gh,
            profile=profile,
            repository=repository,
            authentication_state=auth_state,
            account=account,
            profile_result=profile_result,
            manager_environment_result=manager_result,
            mcp_execution_result=mcp_result,
            repository_read_result=repository_result,
            reason_code=reason,
        )
        return self.status(instance_id)

    @staticmethod
    def _local_mcp_url(desired: DesiredInstance) -> str:
        host = desired.mcp.host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{desired.mcp.port}/mcp"

    @staticmethod
    def _http_json(
        url: str,
        payload: Mapping[str, Any],
        *,
        session_id: str | None = None,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> tuple[Mapping[str, Any], str | None]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Connection": "close",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MCP_OUTPUT_LIMIT + 1)
            if len(raw) > MCP_OUTPUT_LIMIT:
                raise ValueError("MCP response exceeded output bound")
            text = raw.decode("utf-8", errors="strict").strip()
            if text.startswith("data:"):
                data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
                text = data_lines[-1] if data_lines else ""
            decoded = json.loads(text) if text else {}
            if not isinstance(decoded, Mapping):
                raise ValueError("MCP response is not a JSON object")
            return decoded, response.headers.get("Mcp-Session-Id")

    def _verify_mcp(self, desired: DesiredInstance, gh_binary: str, account: str) -> tuple[str, str | None]:
        if desired.lifecycle.deployment is DeploymentTarget.ABSENT:
            return "not_deployed", "MCP_NOT_DEPLOYED"
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return "unavailable", "MCP_EXECUTION_UNAVAILABLE"
        env = dict(os.environ)
        for key in MANAGED_GITHUB_ENV_REMOVE:
            env.pop(key, None)
        active = _run_bounded(
            [systemctl, "--user", "is-active", f"coding-tools-mcp-{desired.instance_id.value}.service"],
            env=env,
            timeout=5.0,
            output_limit=2_048,
        )
        if active.returncode != 0 or active.stdout.decode("utf-8", errors="replace").strip() != "active":
            return "unavailable", "MCP_NOT_RUNNING"

        url = self._local_mcp_url(desired)
        session_id: str | None = None
        try:
            initialized, session_id = self._http_json(
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "workspace-mcp-manager-pm3.1.1", "version": "1"},
                    },
                },
            )
            result = initialized.get("result") if isinstance(initialized.get("result"), Mapping) else {}
            server = result.get("serverInfo") if isinstance(result.get("serverInfo"), Mapping) else {}
            server_version = server.get("version")
            if result.get("protocolVersion") != MCP_PROTOCOL_VERSION or server_version not in QUALIFIED_MCP_VERSIONS:
                return "unavailable", "MCP_EXECUTION_UNAVAILABLE"
            if server_version == "0.2.2" and not session_id:
                return "unavailable", "MCP_EXECUTION_UNAVAILABLE"
            if session_id:
                self._http_json(
                    url,
                    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    session_id=session_id,
                )
            command = shlex.join([gh_binary, *ACCOUNT_ARGS])
            called, _ = self._http_json(
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "exec_command",
                        "arguments": {
                            "cmd": command,
                            "timeout_ms": MCP_TOOL_TIMEOUT_MS,
                            "max_output_bytes": MCP_OUTPUT_LIMIT,
                            "preview_bytes": MCP_PREVIEW_LIMIT,
                            "verbosity": "full",
                        },
                    },
                },
                session_id=session_id,
            )
            call_result = called.get("result") if isinstance(called.get("result"), Mapping) else {}
            structured = call_result.get("structuredContent") if isinstance(call_result.get("structuredContent"), Mapping) else {}
            stdout = structured.get("stdout")
            if (
                call_result.get("isError") is False
                and structured.get("ok") is True
                and structured.get("exit_code") == 0
                and structured.get("truncated") is False
                and isinstance(stdout, str)
                and LOGIN_RE.fullmatch(stdout.strip())
                and stdout.strip() == account
            ):
                return "passed", None
            return "failed", "MCP_VERIFICATION_FAILED"
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError, urllib.error.URLError, TimeoutError):
            return "unavailable", "MCP_EXECUTION_UNAVAILABLE"
        finally:
            if session_id:
                try:
                    request = urllib.request.Request(
                        url,
                        method="DELETE",
                        headers={
                            "Mcp-Session-Id": session_id,
                            "Accept": "application/json",
                            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                        },
                    )
                    with urllib.request.urlopen(request, timeout=2.0) as response:
                        response.read(1_024)
                except (OSError, urllib.error.URLError, TimeoutError):
                    pass

    def configure(self, instance_id: str) -> dict[str, Any]:
        try:
            with self._operation_lock(instance_id):
                return self._configure_locked(instance_id)
        except GithubAccessBusy:
            return {
                "ok": False,
                "instance_id": instance_id,
                "operation": {"outcome": "busy", "reason_code": "GITHUB_ACCESS_BUSY"},
                "status": self.status(instance_id),
            }

    def _configure_locked(self, instance_id: str) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        if desired.config_version < 2 or desired.github.mode is not GithubConfigMode.MANAGED:
            return {
                "ok": False,
                "instance_id": instance_id,
                "operation": {"outcome": "failed", "reason_code": "PROFILE_INVALID"},
                "status": self.status(instance_id),
            }
        self.ensure_managed_profile(instance_id)
        gh = self._resolve_gh(desired)
        if not gh.available or gh.binary is None:
            return {
                "ok": False,
                "instance_id": instance_id,
                "operation": {"outcome": "failed", "reason_code": "GH_BINARY_UNAVAILABLE"},
                "status": self.status(instance_id),
            }
        if not gh.supported:
            return {
                "ok": False,
                "instance_id": instance_id,
                "operation": {"outcome": "failed", "reason_code": "GH_VERSION_UNSUPPORTED"},
                "status": self.status(instance_id),
            }
        profile = managed_github_profile_path(self.paths, desired)
        repository = self._repository(desired)
        helper_env = dict(os.environ)
        for key in MANAGED_GITHUB_ENV_REMOVE:
            helper_env.pop(key, None)
        helper_argv = [
            sys.executable,
            "-m",
            "workspace_mcp_manager.github_auth_helper",
            "--instance-id",
            desired.instance_id.value,
            "--profile",
            str(profile),
            "--gh-binary",
            gh.binary,
            "--home",
            str(self.paths.account_home),
            "--exec-path",
            desired.mcp.exec_path,
            "--timeout-seconds",
            "120",
        ]
        if repository and repository.get("repository"):
            helper_argv.extend(["--repository", str(repository["repository"])])
        try:
            completed = subprocess.run(
                helper_argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=False,
                env=helper_env,
                timeout=130.0,
                check=False,
            )
            helper_code = completed.returncode
        except subprocess.TimeoutExpired:
            helper_code = HELPER_TIMEOUT
        except KeyboardInterrupt:
            helper_code = HELPER_INTERRUPTED
        except OSError:
            helper_code = HELPER_LOGIN_FAILED

        outcome, reason = {
            HELPER_OK: ("succeeded", None),
            HELPER_LOGIN_FAILED: ("failed", "AUTHENTICATION_COMMAND_FAILED"),
            HELPER_TTY_REQUIRED: ("failed", "INTERACTIVE_TTY_REQUIRED"),
            HELPER_INPUT_EMPTY: ("failed", "CREDENTIAL_INPUT_EMPTY"),
            HELPER_INPUT_TOO_LARGE: ("failed", "AUTHENTICATION_COMMAND_FAILED"),
            HELPER_TIMEOUT: ("timeout", "AUTHENTICATION_TIMEOUT"),
            HELPER_INTERRUPTED: ("interrupted", "AUTHENTICATION_INTERRUPTED"),
            HELPER_CANCELLED: ("cancelled", None),
        }.get(helper_code, ("failed", "AUTHENTICATION_COMMAND_FAILED"))

        if helper_code == HELPER_OK:
            storage_ok, storage_reason = self._storage_state(desired)
            if not storage_ok:
                outcome = "failed"
                reason = storage_reason or "PROFILE_STORAGE_UNSAFE"

        if helper_code == HELPER_TTY_REQUIRED:
            final_status = self.status(instance_id)
        else:
            final_status = self._verify_locked(instance_id)
        if reason is None and outcome != "succeeded":
            reason = final_status.get("verification", {}).get("reason_code") if isinstance(final_status.get("verification"), Mapping) else None
        return {
            "ok": outcome == "succeeded",
            "instance_id": instance_id,
            "operation": {"outcome": outcome, "reason_code": reason},
            "status": final_status,
        }


__all__ = [
    "ACCOUNT_ARGS",
    "AUTH_STATUS_ARGS",
    "GITHUB_ACCESS_PROJECTION_VERSION",
    "GITHUB_ACCESS_VERIFICATION_RECORD_VERSION",
    "GithubAccessService",
    "QUALIFIED_LOGIN_ARGS",
    "SUPPORTED_GH_VERSIONS",
]
