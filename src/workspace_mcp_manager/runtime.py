from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError, config_error
from .paths import ManagerPaths


ENV_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _decode_environment_value(raw: str, *, key: str, line_number: int) -> str:
    if raw != raw.strip():
        raise config_error(
            f"environment value has leading or trailing whitespace on line {line_number}: {key}"
        )
    if not raw:
        return ""
    if raw[0] in {"'", '"'}:
        try:
            parts = shlex.split(raw, comments=False, posix=True)
        except ValueError as exc:
            raise config_error(f"invalid quoted environment value on line {line_number}: {key}") from exc
        if len(parts) != 1:
            raise config_error(f"invalid environment value on line {line_number}: {key}")
        return parts[0]
    return raw


def parse_tunnel_environment(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ManagerError(ErrorCode.IO_ERROR, f"cannot read tunnel environment file: {path}") from exc
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        assignment = line[7:] if line.startswith("export ") else line
        match = ENV_ASSIGNMENT_RE.fullmatch(assignment)
        if not match:
            raise config_error(
                f"invalid tunnel environment line {line_number}; expected KEY=VALUE or export KEY=VALUE"
            )
        key, raw_value = match.groups()
        if key in values:
            raise config_error(f"duplicate tunnel environment key on line {line_number}: {key}")
        values[key] = _decode_environment_value(raw_value, key=key, line_number=line_number)
    if not values.get("CONTROL_PLANE_API_KEY"):
        raise config_error(f"CONTROL_PLANE_API_KEY is missing or empty in {path}")
    return values


def run_tunnel(desired: DesiredInstance, paths: ManagerPaths) -> None:
    profile_path = paths.tunnel_profile_dir / f"{desired.tunnel.profile}.yaml"
    binary = Path(desired.tunnel.binary)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ManagerError(ErrorCode.IO_ERROR, f"tunnel binary is not executable: {binary}")
    if not profile_path.is_file():
        raise ManagerError(ErrorCode.IO_ERROR, f"tunnel profile is missing: {profile_path}")
    environment = os.environ.copy()
    environment.update(parse_tunnel_environment(Path(desired.tunnel.env_file)))
    environment["CONTROL_PLANE_TUNNEL_ID"] = desired.tunnel.id
    state_dir = paths.state_root / "instances" / desired.instance_id.value
    argv = [
        str(binary),
        "run",
        "--profile",
        desired.tunnel.profile,
        "--profile-dir",
        str(paths.tunnel_profile_dir),
        "--log.file",
        str(state_dir / "tunnel.log"),
    ]
    os.execve(str(binary), argv, environment)


def run_admission_guard(_desired: DesiredInstance, _paths: ManagerPaths) -> None:
    raise ManagerError(
        ErrorCode.FEATURE_NOT_IMPLEMENTED,
        "admission recovery runtime is intentionally deferred to P11",
    )
