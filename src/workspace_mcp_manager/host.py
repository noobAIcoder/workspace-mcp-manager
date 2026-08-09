from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .paths import ManagerPaths
from .redaction import SECRET_ENV_KEYS, redact_text, sanitized_subprocess_env


COMPONENT_COMMANDS: dict[str, tuple[str, ...] | None] = {
    "coding-tools-mcp": None,
    "tunnel-client": ("--version",),
    "codex": ("--version",),
    "git": ("--version",),
    "gh": ("--version",),
    "systemctl": ("--version",),
    "python3": ("--version",),
    "uv": ("--version",),
    "node": ("--version",),
    "npm": ("--version",),
}

SYSTEM_ROOTS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/")


@dataclass(frozen=True, slots=True)
class CommandObservation:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "ok": self.ok,
        }


class HostInspector:
    def __init__(self, paths: ManagerPaths, *, environ: dict[str, str] | None = None) -> None:
        self.paths = paths
        self.environ = dict(os.environ if environ is None else environ)

    def _run(self, argv: Sequence[str], *, timeout: float = 4.0) -> CommandObservation:
        try:
            completed = subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                env=sanitized_subprocess_env(self.environ),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CommandObservation(tuple(argv), None, "", "", redact_text(str(exc)))
        return CommandObservation(
            tuple(argv),
            completed.returncode,
            redact_text(completed.stdout[-8192:]),
            redact_text(completed.stderr[-8192:]),
        )

    def _read_kernel_release(self) -> str:
        try:
            return Path("/proc/sys/kernel/osrelease").read_text(encoding="ascii", errors="replace").strip()
        except OSError:
            return platform.release()

    def _components(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, version_args in COMPONENT_COMMANDS.items():
            executable = shutil.which(name)
            item: dict[str, Any] = {"path": executable, "present": bool(executable), "version": None}
            if executable and version_args is not None:
                observation = self._run((executable, *version_args))
                version_text = (observation.stdout or observation.stderr).strip().splitlines()
                item["version"] = version_text[0] if observation.ok and version_text else None
                if not observation.ok:
                    item["version_error"] = observation.error or observation.stderr.strip() or f"exit {observation.exit_code}"
            result[name] = item
        return result

    def _systemd(self) -> dict[str, Any]:
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return {"available": False, "user_state": None, "units": [], "error": "systemctl not found"}
        state = self._run((systemctl, "--user", "is-system-running"))
        units = self._run((systemctl, "--user", "list-unit-files", "--no-legend", "--no-pager"))
        relevant: list[str] = []
        if units.exit_code == 0:
            for line in units.stdout.splitlines():
                name = line.split(maxsplit=1)[0] if line.split() else ""
                if name.startswith(("coding-tools-mcp-", "tunnel-client-", "workspace-mcp-")):
                    relevant.append(line.strip())
        return {
            "available": True,
            "user_state": (state.stdout or state.stderr).strip() or None,
            "user_state_exit_code": state.exit_code,
            "units": sorted(relevant),
            "error": units.error,
        }

    def _linger(self) -> dict[str, Any]:
        loginctl = shutil.which("loginctl")
        if not loginctl:
            return {"available": False, "enabled": None}
        obs = self._run((loginctl, "show-user", str(os.getuid()), "-p", "Linger", "--value"))
        text = obs.stdout.strip().lower()
        if not obs.ok:
            # Under coding-tools-mcp Landlock/NSS confinement, show-user can
            # fail even though logind can enumerate the active login record.
            # `list-users` exposes UID USER LINGER STATE without requiring the
            # passwd lookup that failed above.
            fallback = self._run((loginctl, "list-users", "--no-legend", "--no-pager"))
            if fallback.ok:
                for line in fallback.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] == str(os.getuid()):
                        linger = parts[2].lower()
                        if linger in {"yes", "no"}:
                            return {
                                "available": True,
                                "enabled": linger == "yes",
                                "source": "loginctl-list-users",
                                "error": None,
                            }
        return {
            "available": True,
            "enabled": text == "yes" if obs.ok else None,
            "source": "loginctl-show-user",
            "error": None if obs.ok else (obs.error or obs.stderr.strip()),
        }

    def _listeners(self) -> dict[str, Any]:
        ss = shutil.which("ss")
        if not ss:
            return {"available": False, "tcp_listeners": []}
        obs = self._run((ss, "-H", "-ltn"))
        listeners: list[str] = []
        if obs.ok:
            for line in obs.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    listeners.append(parts[3])
        return {
            "available": True,
            "tcp_listeners": sorted(set(listeners)),
            "error": None if obs.ok else (obs.error or obs.stderr.strip()),
        }

    @staticmethod
    def _safe_glob(directory: Path, pattern: str) -> tuple[list[Path], str | None]:
        try:
            return sorted(directory.glob(pattern)), None
        except OSError as exc:
            return [], redact_text(str(exc))

    def _legacy_inventory(self, systemd: dict[str, Any]) -> dict[str, Any]:
        config_dir = self.paths.account_home / ".config" / "workspace-mcp"
        profile_dir = self.paths.account_home / ".config" / "tunnel-client"
        configs, config_error = self._safe_glob(config_dir, "*.conf")
        profiles, profile_error = self._safe_glob(profile_dir, "workspace-mcp-*.yaml")
        candidates: set[str] = set()
        for line in systemd.get("units", []):
            unit = str(line).split(maxsplit=1)[0]
            for prefix in ("coding-tools-mcp-", "tunnel-client-"):
                if unit.startswith(prefix) and unit.endswith(".service"):
                    candidate = unit[len(prefix) : -len(".service")]
                    if candidate:
                        candidates.add(candidate)
        return {
            "instance_configs": [str(path) for path in configs],
            "tunnel_profiles": [str(path) for path in profiles],
            "systemd_instance_candidates": sorted(candidates),
            "config_scan_incomplete": bool(candidates and not configs),
            "config_scan_error": config_error,
            "profile_scan_error": profile_error,
        }

    def _environment_metadata(self) -> dict[str, Any]:
        path_keys = ("PATH", "SSH_AUTH_SOCK", "GH_CONFIG_DIR", "CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS", "XDG_RUNTIME_DIR")
        return {
            "paths": {key: self.environ.get(key) for key in path_keys},
            "secret_presence": {key: bool(self.environ.get(key)) for key in sorted(SECRET_ENV_KEYS)},
        }

    @staticmethod
    def _external_roots(components: dict[str, Any]) -> list[str]:
        roots: set[str] = set()
        for item in components.values():
            path = item.get("path") if isinstance(item, dict) else None
            if not path:
                continue
            try:
                resolved = str(Path(path).resolve())
            except OSError:
                resolved = str(path)
            if not resolved.startswith(SYSTEM_ROOTS):
                roots.add(str(Path(resolved).parent))
        return sorted(roots)

    def inspect(self) -> dict[str, Any]:
        kernel_release = self._read_kernel_release()
        components = self._components()
        systemd = self._systemd()
        return {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "host": {
                "system": platform.system(),
                "kernel_release": kernel_release,
                "architecture": platform.machine(),
                "wsl": "microsoft" in kernel_release.lower() or bool(self.environ.get("WSL_INTEROP")),
                "uid": os.getuid(),
                "account_home": str(self.paths.account_home),
            },
            "systemd": systemd,
            "linger": self._linger(),
            "components": components,
            "listeners": self._listeners(),
            "legacy": self._legacy_inventory(systemd),
            "environment": self._environment_metadata(),
            "candidate_external_roots": self._external_roots(components),
        }

    def components(self) -> dict[str, Any]:
        return {"schema_version": 1, "components": self._components()}

    def doctor(self) -> dict[str, Any]:
        snapshot = self.inspect()
        components = snapshot["components"]
        checks = [
            {
                "name": "python3",
                "status": "PASS" if components["python3"]["present"] else "FAIL",
                "detail": components["python3"]["path"],
            },
            {
                "name": "systemd-user",
                "status": "PASS" if snapshot["systemd"]["available"] else "FAIL",
                "detail": snapshot["systemd"]["user_state"],
            },
            {
                "name": "coding-tools-mcp",
                "status": "PASS" if components["coding-tools-mcp"]["present"] else "FAIL",
                "detail": components["coding-tools-mcp"]["path"],
            },
            {
                "name": "tunnel-client",
                "status": "PASS" if components["tunnel-client"]["present"] else "FAIL",
                "detail": components["tunnel-client"]["path"],
            },
        ]
        return {
            "schema_version": 1,
            "ok": all(item["status"] == "PASS" for item in checks),
            "checks": checks,
        }

