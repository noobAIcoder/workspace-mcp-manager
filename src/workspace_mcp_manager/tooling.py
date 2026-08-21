from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import ErrorCode, ManagerError
from .paths import ManagerPaths
from .redaction import redact_object, redact_text, sanitized_subprocess_env


CODEX_VERSION_RE = re.compile(r"(?:codex-cli\s+)?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?)")
TOOLING_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="strict") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _is_regular_executable(path: Path) -> bool:
    try:
        metadata = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and os.access(path, os.X_OK)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ToolingService:
    def __init__(self, paths: ManagerPaths) -> None:
        self.paths = paths
        self.state_dir = paths.state_root / "tooling"

    def _run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        base_env = sanitized_subprocess_env(dict(os.environ if env is None else env))
        try:
            return subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                env=base_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError(
                ErrorCode.IO_ERROR,
                f"tooling command failed to execute: {argv[0]}",
                {"error": redact_text(str(exc))},
            ) from exc

    @staticmethod
    def _bounded_error(completed: subprocess.CompletedProcess[str]) -> str | None:
        text = (completed.stderr or completed.stdout).strip()
        return redact_text(text)[-2000:] if text else None

    def _version(
        self,
        path: Path,
        args: Sequence[str] = ("--version",),
        *,
        env: Mapping[str, str] | None = None,
    ) -> str | None:
        if not _is_regular_executable(path):
            return None
        completed = self._run([str(path), *args], timeout=10, env=env)
        if completed.returncode != 0:
            return None
        lines = (completed.stdout or completed.stderr).strip().splitlines()
        return lines[0] if lines else None

    @staticmethod
    def _codex_version(text: str | None) -> str | None:
        if not text:
            return None
        match = CODEX_VERSION_RE.search(text)
        return match.group(1) if match else None

    def _persist(self, name: str, payload: Mapping[str, Any]) -> str:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        path = self.state_dir / f"{name}.json"
        value = redact_object({"schema_version": TOOLING_SCHEMA_VERSION, "updated_at": _utc_now(), **dict(payload)})
        _atomic_write(path, json.dumps(value, sort_keys=True, indent=2) + "\n", mode=0o600)
        return str(path)

    def _tool(
        self,
        path: Path,
        *,
        version_args: Sequence[str] = ("--version",),
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        present = _is_regular_executable(path)
        return {
            "path": str(path),
            "present": present,
            "version": self._version(path, version_args, env=env) if present else None,
        }

    def audit(self) -> dict[str, Any]:
        home = self.paths.account_home
        local_bin = home / ".local" / "bin"
        tools: dict[str, Any] = {
            "uv": self._tool(local_bin / "uv"),
            "coding-tools-mcp": self._tool(local_bin / "coding-tools-mcp", version_args=("--help",)),
            "tunnel-client": self._tool(local_bin / "tunnel-client"),
            "workspace-mcp-manager": self._tool(local_bin / "workspace-mcp-manager", version_args=("--help",)),
            "gh-system": self._tool(Path("/usr/bin/gh")),
            "gh-local": self._tool(local_bin / "gh"),
            "ssh-agent": self._tool(Path("/usr/bin/ssh-agent"), version_args=("-h",)),
            "gpg-agent": self._tool(Path("/usr/bin/gpg-agent")),
            "gpgconf": self._tool(Path("/usr/bin/gpgconf")),
        }
        nvm_root = home / ".nvm" / "versions" / "node"
        node_roots: list[dict[str, Any]] = []
        try:
            roots = sorted(path for path in nvm_root.iterdir() if path.is_dir() and not path.is_symlink())
        except OSError:
            roots = []
        for root in roots:
            bin_dir = root / "bin"
            node_env = dict(os.environ)
            node_env["HOME"] = str(home)
            node_env["PATH"] = os.pathsep.join((str(bin_dir), "/usr/local/bin", "/usr/bin", "/bin"))
            node_roots.append(
                {
                    "root": str(root),
                    "node": self._tool(bin_dir / "node", env=node_env),
                    "npm": self._tool(bin_dir / "npm", env=node_env),
                    "corepack": self._tool(bin_dir / "corepack", env=node_env),
                    "pnpm": self._tool(bin_dir / "pnpm", env=node_env),
                    "codex": self._tool(bin_dir / "codex", env=node_env),
                }
            )
        return {
            "ok": True,
            "action": "tools-audit",
            "tools": tools,
            "node_roots": node_roots,
            "agents": self.agents_audit(),
        }

    def codex(self, *, node_root: str, version: str | None, upgrade: bool) -> dict[str, Any]:
        root = Path(node_root)
        if not root.is_absolute():
            raise ManagerError(ErrorCode.CONFIG_INVALID, "Codex node root must be absolute")
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise ManagerError(ErrorCode.CONFIG_INVALID, f"Codex node root does not exist: {root}") from exc
        if not resolved.is_dir():
            raise ManagerError(ErrorCode.CONFIG_INVALID, f"Codex node root is not a directory: {resolved}")
        node_bin = resolved / "bin"
        node = node_bin / "node"
        npm = node_bin / "npm"
        codex_path = node_bin / "codex"
        if not _is_regular_executable(node) or not _is_regular_executable(npm):
            raise ManagerError(ErrorCode.CONFIG_INVALID, "selected Node root does not contain executable node/npm")
        if version is not None and not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]*", version):
            raise ManagerError(ErrorCode.CONFIG_INVALID, "Codex version contains unsupported characters")

        env = dict(os.environ)
        env["HOME"] = str(self.paths.account_home)
        env["PATH"] = os.pathsep.join((str(node_bin), "/usr/local/bin", "/usr/bin", "/bin"))
        env["NPM_CONFIG_PREFIX"] = str(resolved)
        current_version = (
            self._codex_version(self._version(codex_path, env=env))
            if _is_regular_executable(codex_path)
            else None
        )
        if current_version is not None and not upgrade:
            if version is None or current_version == version:
                payload = {
                    "ok": True,
                    "action": "codex",
                    "result": "noop",
                    "codex_path": str(codex_path),
                    "codex_version": current_version,
                    "required_exec_path": str(node_bin),
                    "required_external_root": str(resolved),
                }
                payload["state_path"] = self._persist("codex", payload)
                return payload
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "installed Codex differs from requested version; explicit --upgrade is required",
                {"installed_version": current_version, "requested_version": version},
            )

        package = "@openai/codex" if version is None else f"@openai/codex@{version}"
        completed = self._run([str(npm), "install", "-g", package], env=env, timeout=300)
        if completed.returncode != 0:
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "Codex npm installation failed",
                {"exit_code": completed.returncode, "error": self._bounded_error(completed)},
            )
        final_version = self._codex_version(self._version(codex_path, env=env))
        if final_version is None:
            raise ManagerError(ErrorCode.IO_ERROR, "Codex installation completed but codex --version failed")
        if version is not None and final_version != version:
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "Codex installed version does not match requested version",
                {"requested_version": version, "installed_version": final_version},
            )
        payload = {
            "ok": True,
            "action": "codex",
            "result": "installed" if current_version is None else "upgraded",
            "codex_path": str(codex_path),
            "codex_version": final_version,
            "required_exec_path": str(node_bin),
            "required_external_root": str(resolved),
        }
        payload["state_path"] = self._persist("codex", payload)
        return payload

    def gh(self, *, source: str | None, sha256: str | None, upgrade: bool) -> dict[str, Any]:
        system_gh = Path("/usr/bin/gh")
        local_gh = self.paths.account_home / ".local" / "bin" / "gh"
        selected = local_gh if _is_regular_executable(local_gh) else system_gh
        current = self._version(selected) if _is_regular_executable(selected) else None
        if source is None:
            if current is None:
                raise ManagerError(
                    ErrorCode.CONFIG_INVALID,
                    "gh is not installed; provide --source and --sha256 for deterministic user-local installation",
                )
            payload = {"ok": True, "action": "gh", "result": "noop", "path": str(selected), "version": current}
            payload["state_path"] = self._persist("gh", payload)
            return payload
        if sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ManagerError(ErrorCode.CONFIG_INVALID, "gh source installation requires a lowercase SHA-256 digest")
        src = Path(source)
        if not src.is_absolute():
            raise ManagerError(ErrorCode.CONFIG_INVALID, "gh source path must be absolute")
        try:
            metadata = src.stat()
        except OSError as exc:
            raise ManagerError(ErrorCode.CONFIG_INVALID, f"gh source does not exist: {src}") from exc
        if not stat.S_ISREG(metadata.st_mode) or src.is_symlink() or not os.access(src, os.X_OK):
            raise ManagerError(ErrorCode.CONFIG_INVALID, "gh source must be a regular executable file")
        actual_digest = _sha256(src)
        if actual_digest != sha256:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "gh source SHA-256 does not match expected digest",
                {"actual_sha256": actual_digest},
            )
        if local_gh.exists() or local_gh.is_symlink():
            manager_root = self.paths.account_home / ".local" / "lib" / "workspace-mcp-manager" / "tools" / "gh"
            try:
                local_resolved = local_gh.resolve(strict=True)
                local_resolved.relative_to(manager_root.resolve())
                manager_owned = True
            except (OSError, ValueError):
                manager_owned = False
            if not manager_owned and not upgrade:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    "user-local gh path exists outside manager ownership; explicit --upgrade is required",
                    {"path": str(local_gh)},
                )
        release_root = self.paths.account_home / ".local" / "lib" / "workspace-mcp-manager" / "tools" / "gh" / "releases"
        release = release_root / actual_digest
        release.mkdir(parents=True, exist_ok=True, mode=0o755)
        target = release / "gh"
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix=".gh.", suffix=".tmp", dir=release)
            try:
                with os.fdopen(fd, "wb") as handle, src.open("rb") as source_handle:
                    for block in iter(lambda: source_handle.read(1024 * 1024), b""):
                        handle.write(block)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o755)
                os.replace(temporary, target)
            except Exception:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        if _sha256(target) != actual_digest:
            raise ManagerError(ErrorCode.IO_ERROR, "installed gh release digest verification failed")
        final_version = self._version(target)
        if final_version is None:
            raise ManagerError(ErrorCode.IO_ERROR, "gh source copied but gh --version validation failed")
        relative_target = os.path.relpath(target, local_gh.parent)
        _atomic_symlink(local_gh, relative_target)
        payload = {
            "ok": True,
            "action": "gh",
            "result": "installed" if current is None else "upgraded",
            "path": str(local_gh),
            "version": final_version,
            "sha256": actual_digest,
        }
        payload["state_path"] = self._persist("gh", payload)
        return payload

    def agents_audit(self) -> dict[str, Any]:
        socket = self.paths.state_root / "agents" / "ssh-agent.sock"
        unit = self.paths.user_unit_dir / "workspace-mcp-ssh-agent.service"
        env_file = self.paths.account_home / ".config" / "environment.d" / "90-workspace-mcp-manager-agent.conf"
        current_socket = os.environ.get("SSH_AUTH_SOCK")
        return {
            "ok": True,
            "action": "agents-audit",
            "ssh_agent_binary": _is_regular_executable(Path("/usr/bin/ssh-agent")),
            "gpg_agent_binary": _is_regular_executable(Path("/usr/bin/gpg-agent")),
            "gpgconf_binary": _is_regular_executable(Path("/usr/bin/gpgconf")),
            "current_ssh_auth_sock": current_socket,
            "current_ssh_auth_sock_present": bool(current_socket and Path(current_socket).exists()),
            "ssh_socket": str(socket),
            "ssh_socket_present": socket.exists(),
            "unit_path": str(unit),
            "unit_present": unit.is_file() and not unit.is_symlink(),
            "environment_path": str(env_file),
            "environment_present": env_file.is_file() and not env_file.is_symlink(),
        }

    def agents_configure(self) -> dict[str, Any]:
        raise ManagerError(
            ErrorCode.FEATURE_NOT_IMPLEMENTED,
            "host-wide agent activation is intentionally unsupported; use config_version=2 per-instance agent.mode",
            {
                "ssh_agent_present": _is_regular_executable(Path("/usr/bin/ssh-agent")),
                "gpg_agent_present": _is_regular_executable(Path("/usr/bin/gpg-agent")),
                "gpgconf_present": _is_regular_executable(Path("/usr/bin/gpgconf")),
            },
        )
