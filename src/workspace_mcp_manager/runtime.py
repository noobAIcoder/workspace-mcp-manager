from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError, config_error
from .paths import ManagerPaths
from .redaction import sanitized_subprocess_env


ENV_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
MCP_PROTOCOL_VERSION = "2025-11-25"
SATURATION_BODY = "maximum HTTP session count reached"
ADMISSION_RECOVERY_SCHEMA_VERSION = 1
ADMISSION_RECOVERY_STATE_FILE = "admission-recovery.json"
MAX_PROBE_BODY_BYTES = 4096


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            errors="strict",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
        raise ManagerError(ErrorCode.IO_ERROR, f"cannot persist admission-recovery evidence: {path}") from exc


def _new_recovery_state(desired: DesiredInstance) -> dict[str, Any]:
    return {
        "schema_version": ADMISSION_RECOVERY_SCHEMA_VERSION,
        "instance_id": desired.instance_id.value,
        "guard_interval_seconds": desired.recovery.guard_interval_seconds,
        "recovery_cooldown_seconds": desired.recovery.recovery_cooldown_seconds,
        "saturation_detection_count": 0,
        "cooldown_suppression_count": 0,
        "restart_attempt_count": 0,
        "last_observation": None,
        "last_saturation_at": None,
        "last_saturation_signature": None,
        "last_cooldown_suppression_at": None,
        "last_restart_attempt_at": None,
        "last_restart_result": None,
        "cooldown_until": None,
    }


def _load_recovery_state(path: Path, desired: DesiredInstance) -> dict[str, Any]:
    if not path.exists():
        return _new_recovery_state(desired)
    if path.is_symlink() or not path.is_file():
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "admission-recovery evidence has unexpected filesystem type",
            {"path": str(path)},
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "admission-recovery evidence is unreadable or invalid",
            {"path": str(path)},
        ) from exc
    if not isinstance(payload, dict):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "admission-recovery evidence must be an object")
    if payload.get("schema_version") != ADMISSION_RECOVERY_SCHEMA_VERSION:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "unsupported admission-recovery evidence schema")
    if payload.get("instance_id") != desired.instance_id.value:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "admission-recovery instance identity mismatch")
    for key in ("saturation_detection_count", "cooldown_suppression_count", "restart_attempt_count"):
        if key not in payload and key == "cooldown_suppression_count":
            payload[key] = 0
        if isinstance(payload.get(key), bool) or not isinstance(payload.get(key), int) or payload[key] < 0:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"invalid admission-recovery counter: {key}")
    return payload


def _read_bounded_response(response: Any) -> tuple[str | None, bool]:
    data = response.read(MAX_PROBE_BODY_BYTES + 1)
    oversized = len(data) > MAX_PROBE_BODY_BYTES
    if oversized:
        data = data[:MAX_PROBE_BODY_BYTES]
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError:
        return None, oversized
    return text.strip(), oversized


def _probe_admission(desired: DesiredInstance) -> dict[str, Any]:
    endpoint = f"http://{desired.mcp.host}:{desired.mcp.port}/mcp"
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "workspace-mcp-manager-admission-guard", "version": "1"},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Connection": "close",
    }
    request = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = int(response.status)
            session_id = response.headers.get("Mcp-Session-Id")
            if status != 200:
                return {
                    "classification": "http_error",
                    "http_status": status,
                    "exact_signature": False,
                    "session_cleanup": None,
                }
            cleanup = "not-required"
            if session_id:
                delete_headers = {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                    "Mcp-Session-Id": session_id,
                    "Connection": "close",
                }
                delete_request = urllib.request.Request(endpoint, method="DELETE", headers=delete_headers)
                try:
                    with urllib.request.urlopen(delete_request, timeout=5) as delete_response:
                        cleanup = "deleted" if int(delete_response.status) == 200 else "delete-failed"
                except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
                    cleanup = "delete-failed"
            return {
                "classification": "healthy" if cleanup != "delete-failed" else "session_cleanup_error",
                "http_status": status,
                "exact_signature": False,
                "session_cleanup": cleanup,
            }
    except urllib.error.HTTPError as exc:
        text, oversized = _read_bounded_response(exc)
        exact = exc.code == 503 and not oversized and text == SATURATION_BODY
        return {
            "classification": "saturation" if exact else "http_error",
            "http_status": int(exc.code),
            "exact_signature": exact,
            "session_cleanup": None,
        }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "classification": "probe_error",
            "http_status": None,
            "exact_signature": False,
            "session_cleanup": None,
            "error_type": type(exc).__name__,
        }


def _parse_restart_time(value: Any, *, now: datetime) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "invalid admission-recovery restart timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "invalid admission-recovery restart timestamp") from exc
    if parsed.tzinfo is None:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "admission-recovery restart timestamp lacks timezone")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "admission-recovery restart timestamp is in the future")
    return parsed


def _restart_mcp(desired: DesiredInstance) -> subprocess.CompletedProcess[str]:
    unit = f"coding-tools-mcp-{desired.instance_id.value}.service"
    try:
        return subprocess.run(
            ["systemctl", "--user", "restart", unit],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
            env=sanitized_subprocess_env(os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagerError(
            ErrorCode.IO_ERROR,
            "admission recovery could not execute MCP restart",
            {"unit": unit, "error_type": type(exc).__name__},
        ) from exc


def run_admission_guard(desired: DesiredInstance, paths: ManagerPaths) -> None:
    if not desired.recovery.admission_guard_enabled:
        print(json.dumps({"ok": True, "instance_id": desired.instance_id.value, "classification": "disabled"}))
        return

    state_dir = paths.state_root / "instances" / desired.instance_id.value
    state_path = state_dir / ADMISSION_RECOVERY_STATE_FILE
    state = _load_recovery_state(state_path, desired)
    now = _utc_now()
    observation = _probe_admission(desired)
    state["guard_interval_seconds"] = desired.recovery.guard_interval_seconds
    state["recovery_cooldown_seconds"] = desired.recovery.recovery_cooldown_seconds
    state["last_observation"] = {"at": _iso(now), **observation}

    if not observation["exact_signature"]:
        _atomic_json(state_path, state)
        print(json.dumps({"ok": True, "instance_id": desired.instance_id.value, **state["last_observation"]}, sort_keys=True))
        return

    state["saturation_detection_count"] += 1
    state["last_saturation_at"] = _iso(now)
    state["last_saturation_signature"] = f"503 {SATURATION_BODY}"
    previous_attempt = _parse_restart_time(state.get("last_restart_attempt_at"), now=now)
    cooldown = timedelta(seconds=desired.recovery.recovery_cooldown_seconds)
    if previous_attempt is not None and now - previous_attempt < cooldown:
        cooldown_until = previous_attempt + cooldown
        state["cooldown_suppression_count"] = int(state.get("cooldown_suppression_count", 0)) + 1
        state["last_cooldown_suppression_at"] = _iso(now)
        state["cooldown_until"] = _iso(cooldown_until)
        state["last_observation"]["classification"] = "cooldown"
        state["last_observation"]["cooldown_remaining_seconds"] = max(
            1, int((cooldown_until - now).total_seconds())
        )
        _atomic_json(state_path, state)
        print(json.dumps({"ok": True, "instance_id": desired.instance_id.value, **state["last_observation"]}, sort_keys=True))
        return

    state["restart_attempt_count"] += 1
    state["last_restart_attempt_at"] = _iso(now)
    state["cooldown_until"] = _iso(now + cooldown)
    state["last_restart_result"] = {
        "at": _iso(now),
        "ok": None,
        "exit_code": None,
        "unit": f"coding-tools-mcp-{desired.instance_id.value}.service",
    }
    # Persist the attempt before mutation so an initiating-request or process
    # failure cannot erase the evidence/cooldown boundary.
    _atomic_json(state_path, state)

    try:
        restarted = _restart_mcp(desired)
    except ManagerError:
        state["last_restart_result"] = {
            "at": _iso(_utc_now()),
            "ok": False,
            "exit_code": None,
            "unit": f"coding-tools-mcp-{desired.instance_id.value}.service",
        }
        _atomic_json(state_path, state)
        raise

    state["last_restart_result"] = {
        "at": _iso(_utc_now()),
        "ok": restarted.returncode == 0,
        "exit_code": restarted.returncode,
        "unit": f"coding-tools-mcp-{desired.instance_id.value}.service",
    }
    _atomic_json(state_path, state)
    print(
        json.dumps(
            {
                "ok": restarted.returncode == 0,
                "instance_id": desired.instance_id.value,
                "classification": "restarted" if restarted.returncode == 0 else "restart_failed",
                "exact_signature": True,
                "http_status": 503,
                "restart_exit_code": restarted.returncode,
            },
            sort_keys=True,
        )
    )
    if restarted.returncode != 0:
        raise ManagerError(
            ErrorCode.IO_ERROR,
            "admission recovery MCP restart failed",
            {"unit": f"coding-tools-mcp-{desired.instance_id.value}.service", "exit_code": restarted.returncode},
        )
