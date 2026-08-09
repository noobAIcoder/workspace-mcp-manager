from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, ManagerError


_SECRET_ENV_KEYS = {
    "CONTROL_PLANE_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
}


def _systemd_manager_home() -> Path | None:
    """Recover the login home when passwd lookup is blocked by MCP Landlock.

    `coding-tools-mcp` deliberately gives exec requests a synthetic HOME.  The
    user systemd manager retains the real login environment, and its D-Bus API
    remains available even when `/etc/passwd` is not readable inside Landlock.
    Only HOME is retained from the command output.
    """

    systemctl = shutil.which("systemctl")
    if not systemctl:
        return None
    env = {key: value for key, value in os.environ.items() if key not in _SECRET_ENV_KEYS}
    try:
        completed = subprocess.run(
            [systemctl, "--user", "show-environment"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            timeout=2,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if not line.startswith("HOME="):
            continue
        candidate = Path(line.partition("=")[2])
        if candidate.is_absolute():
            return candidate
    return None


def current_account_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        systemd_home = _systemd_manager_home()
        if systemd_home is not None:
            return systemd_home
        raise ManagerError(
            ErrorCode.HOST_INSPECTION_FAILED,
            "cannot resolve the real Unix account home; passwd lookup is unavailable and user-systemd exposes no HOME",
        )


@dataclass(frozen=True, slots=True)
class ManagerPaths:
    account_home: Path
    config_root: Path
    registry_dir: Path
    state_root: Path

    @classmethod
    def for_current_user(cls, *, registry_override: Path | None = None) -> "ManagerPaths":
        account_home = current_account_home()
        config_root = account_home / ".config" / "workspace-mcp-manager"
        registry_dir = registry_override or config_root / "instances"
        state_root = account_home / ".local" / "state" / "workspace-mcp-manager"
        return cls(
            account_home=account_home,
            config_root=config_root,
            registry_dir=registry_dir,
            state_root=state_root,
        )

