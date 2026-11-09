"""Local, foreground onboarding for the shared Tunnel Client API key.

Only the operator's controlling terminal receives credential input. Do not
include credential values in CLI argv, environment variables, JSON contracts,
TUI widget state, error messages, or logs.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Callable

from .errors import ManagerError
from .runtime import parse_tunnel_environment


MAX_KEY_LENGTH = 4096
KEY = "CONTROL_PLANE_API_KEY"
_ASSIGNMENT = re.compile(r"^(?:export )?CONTROL_PLANE_API_KEY=")


class TunnelCredentialSetupError(ValueError):
    """A non-secret explanation of why credential setup was not possible."""


def tunnel_credential_ready(path: Path) -> bool:
    """Check the entire environment file without returning any credential."""
    try:
        parse_tunnel_environment(path)
    except (ManagerError, OSError, UnicodeError):
        return False
    return True


def _safe_destination(path: Path, home: Path) -> Path:
    home = home.expanduser().absolute()
    root = home / ".config" / "tunnel-client"
    candidate = path.expanduser().absolute()
    if candidate.parent != root or candidate.suffix != ".env":
        raise TunnelCredentialSetupError(
            "Automatic credential setup supports only .env files in ~/.config/tunnel-client"
        )
    config = home / ".config"
    for directory in (config, root):
        if directory.is_symlink():
            raise TunnelCredentialSetupError("Credential directory must not be a symlink")
        if directory.exists() and not directory.is_dir():
            raise TunnelCredentialSetupError("Credential directory is not a directory")
    if candidate.is_symlink():
        raise TunnelCredentialSetupError("Credential file must not be a symlink")
    if candidate.exists():
        info = candidate.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise TunnelCredentialSetupError("Credential file must be a regular file owned by this account")
    return candidate


def _updated_contents(previous: str, key: str) -> str:
    lines = [line for line in previous.splitlines() if not _ASSIGNMENT.match(line)]
    lines.append(f"{KEY}={key}")
    return "\n".join(lines) + "\n"


def configure_tunnel_credential(
    path: Path,
    *,
    home: Path,
    prompt: Callable[[str], str] = input,
) -> bool:
    """Prompt visibly on the foreground terminal and atomically save mode 0600.

    A cancelled/empty prompt changes nothing. Existing unrelated environment
    assignments and comments are retained when replacing the API key.
    """
    target = _safe_destination(path, home)
    print(f"\nTunnel Client API key setup\nDestination: {target}\n")
    print(
        "Paste the control-plane API key. Input is VISIBLE so you can verify "
        "the paste. Anyone viewing the terminal or scrollback can see it."
    )
    try:
        credential = prompt("API key (blank cancels): ")
    except (EOFError, KeyboardInterrupt):
        return False
    if not credential:
        return False
    if (
        len(credential) > MAX_KEY_LENGTH
        or credential[0] in ("'", '"')
        or any(ord(char) < 33 or ord(char) > 126 for char in credential)
    ):
        raise TunnelCredentialSetupError("Invalid API key format; no file was changed")

    previous = ""
    if target.exists():
        try:
            previous = target.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise TunnelCredentialSetupError("Existing credential file is unreadable") from exc

    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _safe_destination(target, home)
    data = _updated_contents(previous, credential)
    credential = ""  # Do not retain a second reference beyond this point.
    fd, name = tempfile.mkstemp(prefix=".tunnel-env-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="strict") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Never replace an unexpectedly changed target with a symlink.
        _safe_destination(target, home)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)

    if not tunnel_credential_ready(target):
        raise TunnelCredentialSetupError(
            "Credential file was written but its environment format is invalid"
        )
    print("Tunnel API key configured.")
    return True
