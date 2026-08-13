from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .domain import DeploymentTarget, RuntimeTarget
from .errors import ErrorCode, ManagerError
from .paths import ManagerPaths
from .redaction import redact_object, redact_text, sanitized_subprocess_env
from .registry import InstanceRegistry


CHECKPOINT_SCHEMA_VERSION = 1
MAX_REASON_CHARS = 512
MAX_ERROR_CHARS = 2000
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
SUDO_PATH = "/usr/bin/sudo"
SYSTEMCTL_PATH = "/usr/bin/systemctl"
WSL_OSRELEASE_PATH = Path("/proc/sys/kernel/osrelease")


def is_wsl() -> bool:
    try:
        release = WSL_OSRELEASE_PATH.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        release = ""
    lowered = release.lower()
    return "microsoft" in lowered or "wsl" in lowered


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checkpoint_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def _json_text(value: Mapping[str, Any]) -> str:
    sanitized = redact_object(dict(value))
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _atomic_write(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="strict") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except FileNotFoundError as exc:
        raise ManagerError(ErrorCode.INSTANCE_NOT_FOUND, f"reboot checkpoint is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagerError(ErrorCode.IO_ERROR, f"cannot read reboot checkpoint: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "reboot checkpoint schema is invalid")
    return value


def _validate_boot_id(raw: str) -> str:
    text = raw.strip().lower()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ManagerError(ErrorCode.HOST_INSPECTION_FAILED, "host boot ID is invalid") from exc
    if str(parsed) != text:
        raise ManagerError(ErrorCode.HOST_INSPECTION_FAILED, "host boot ID is not canonical UUID text")
    return text


def reboot_bridge_main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else os.sys.argv[1:])
    if args not in ([], ["--check"]):
        os.sys.stderr.write("workspace-mcp-reboot accepts only --check or no arguments\n")
        return 2
    if is_wsl():
        os.sys.stderr.write(
            "workspace-mcp-reboot is unsupported on WSL; use a future external Windows restart service instead\n"
        )
        return 69
    if args == ["--check"]:
        command = [SUDO_PATH, "-n", "-l", SYSTEMCTL_PATH, "reboot"]
    else:
        command = [SUDO_PATH, "-n", SYSTEMCTL_PATH, "reboot"]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=sanitized_subprocess_env(os.environ),
        )
    except OSError:
        return 127
    return completed.returncode


class RebootService:
    def __init__(
        self,
        paths: ManagerPaths,
        registry: InstanceRegistry,
        *,
        wsl_detector: Callable[[], bool] = is_wsl,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self._wsl_detector = wsl_detector
        self.reboot_root = paths.state_root / "reboot"
        self.checkpoint_path = self.reboot_root / "checkpoint.json"

    def _boot_id(self) -> str:
        try:
            text = BOOT_ID_PATH.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise ManagerError(ErrorCode.HOST_INSPECTION_FAILED, "cannot read host boot ID") from exc
        return _validate_boot_id(text)

    def _bridge_path(self) -> Path:
        return self.paths.manager_executable.with_name("workspace-mcp-reboot")

    def _capture_expected_instances(self) -> list[dict[str, Any]]:
        expected: list[dict[str, Any]] = []
        for desired in self.registry.list():
            if desired.lifecycle.deployment is not DeploymentTarget.PRESENT:
                continue
            if desired.lifecycle.runtime is not RuntimeTarget.RUNNING:
                continue
            iid = desired.instance_id.value
            expected.append(
                {
                    "instance_id": iid,
                    "desired_fingerprint": desired.fingerprint(),
                    "workspace_path": desired.workspace_path,
                    "mcp_unit": f"coding-tools-mcp-{iid}.service",
                    "tunnel_unit": f"tunnel-client-{iid}.service",
                    "mcp_host": desired.mcp.host,
                    "mcp_port": desired.mcp.port,
                    "tunnel_health_host": desired.tunnel.health_host,
                    "tunnel_health_port": desired.tunnel.health_port,
                }
            )
        return expected

    def _persist(self, checkpoint: dict[str, Any]) -> None:
        checkpoint["updated_at"] = _utc_now()
        try:
            _atomic_write(self.checkpoint_path, _json_text(checkpoint), mode=0o600)
        except OSError as exc:
            raise ManagerError(ErrorCode.IO_ERROR, "cannot persist reboot checkpoint") from exc

    def _run_bridge(self, bridge: Path, *, check_only: bool) -> subprocess.CompletedProcess[str]:
        argv = [str(bridge), "--check"] if check_only else [str(bridge)]
        try:
            return subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=15,
                check=False,
                env=sanitized_subprocess_env(os.environ),
            )
        except subprocess.TimeoutExpired as exc:
            raise ManagerError(ErrorCode.IO_ERROR, "reboot bridge timed out synchronously") from exc
        except OSError as exc:
            raise ManagerError(ErrorCode.IO_ERROR, "cannot execute reboot bridge") from exc

    @staticmethod
    def _bounded_error(text: str | None) -> str | None:
        if not text:
            return None
        cleaned = redact_text(text.strip())
        return cleaned[-MAX_ERROR_CHARS:] if cleaned else None

    def request(self, *, reason: str) -> dict[str, Any]:
        if self._wsl_detector():
            raise ManagerError(
                ErrorCode.FEATURE_NOT_IMPLEMENTED,
                "host reboot is unsupported on WSL; WSL restart via an external Windows listener is deferred",
                {"platform": "wsl", "reboot_supported": False},
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ManagerError(ErrorCode.CONFIG_INVALID, "reboot reason must be a non-empty string")
        if len(reason) > MAX_REASON_CHARS:
            raise ManagerError(
                ErrorCode.CONFIG_INVALID,
                "reboot reason exceeds maximum length",
                {"max_chars": MAX_REASON_CHARS},
            )

        boot_id = self._boot_id()
        bridge = self._bridge_path()
        now = _utc_now()
        checkpoint: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_id": _checkpoint_id(),
            "reason": redact_text(reason.strip()),
            "requested_at": now,
            "updated_at": now,
            "state": "prepared",
            "boot_id_before": boot_id,
            "boot_id_after": None,
            "bridge_path": str(bridge),
            "authorization": None,
            "request_result": None,
            "expected_instances": self._capture_expected_instances(),
            "reconciliation": None,
            "last_checked_at": None,
        }
        # CAP-066: this write is authoritative and happens before even the safe
        # authorization preflight subprocess.
        self._persist(checkpoint)

        checked_at = _utc_now()
        if not bridge.is_file() or not os.access(bridge, os.X_OK):
            checkpoint["authorization"] = {
                "checked_at": checked_at,
                "ok": False,
                "exit_code": None,
                "error": "workspace-mcp-reboot bridge is unavailable",
            }
            checkpoint["state"] = "authorization_failed"
            checkpoint["request_result"] = {
                "ok": False,
                "exit_code": None,
                "error": "reboot authorization preflight did not run",
            }
            self._persist(checkpoint)
            return self._public(checkpoint, ok=False)

        try:
            preflight = self._run_bridge(bridge, check_only=True)
        except ManagerError as exc:
            checkpoint["authorization"] = {
                "checked_at": checked_at,
                "ok": False,
                "exit_code": None,
                "error": self._bounded_error(exc.message),
            }
            checkpoint["state"] = "authorization_failed"
            checkpoint["request_result"] = {
                "ok": False,
                "exit_code": None,
                "error": "reboot authorization preflight failed",
            }
            self._persist(checkpoint)
            return self._public(checkpoint, ok=False)

        authorization_error = self._bounded_error(preflight.stderr or preflight.stdout)
        checkpoint["authorization"] = {
            "checked_at": checked_at,
            "ok": preflight.returncode == 0,
            "exit_code": preflight.returncode,
            "error": authorization_error,
        }
        if preflight.returncode != 0:
            checkpoint["state"] = "authorization_failed"
            checkpoint["request_result"] = {
                "ok": False,
                "exit_code": preflight.returncode,
                "error": "reboot authorization is unavailable",
            }
            self._persist(checkpoint)
            return self._public(checkpoint, ok=False)

        checkpoint["state"] = "requesting"
        checkpoint["request_result"] = {"ok": None, "exit_code": None, "error": None}
        self._persist(checkpoint)

        try:
            requested = self._run_bridge(bridge, check_only=False)
        except ManagerError as exc:
            checkpoint["state"] = "request_failed"
            checkpoint["request_result"] = {
                "ok": False,
                "exit_code": None,
                "error": self._bounded_error(exc.message),
            }
            self._persist(checkpoint)
            return self._public(checkpoint, ok=False)

        if requested.returncode != 0:
            checkpoint["state"] = "request_failed"
            checkpoint["request_result"] = {
                "ok": False,
                "exit_code": requested.returncode,
                "error": self._bounded_error(requested.stderr or requested.stdout)
                or "reboot bridge returned failure",
            }
            self._persist(checkpoint)
            return self._public(checkpoint, ok=False)

        checkpoint["state"] = "request_accepted"
        checkpoint["request_result"] = {"ok": True, "exit_code": 0, "error": None}
        self._persist(checkpoint)
        return self._public(checkpoint, ok=True)

    @staticmethod
    def _unit_active(unit: str) -> bool:
        try:
            completed = subprocess.run(
                [SYSTEMCTL_PATH, "--user", "is-active", "--quiet", unit],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                env=sanitized_subprocess_env(os.environ),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    @staticmethod
    def _http(url: str) -> tuple[int | None, str | None]:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                status = int(response.status)
                body = response.read(4096).decode("utf-8", errors="replace").strip()
                return status, body
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(4096).decode("utf-8", errors="replace").strip()
            except Exception:
                body = None
            return int(exc.code), body
        except (urllib.error.URLError, TimeoutError, OSError):
            return None, None

    def _reconcile_instances(self, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        all_ok = True
        raw_expected = checkpoint.get("expected_instances")
        if not isinstance(raw_expected, list):
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "reboot checkpoint expected_instances is invalid")
        for item in raw_expected:
            if not isinstance(item, dict) or not isinstance(item.get("instance_id"), str):
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "reboot checkpoint instance authority is invalid")
            iid = item["instance_id"]
            result: dict[str, Any] = {"instance_id": iid, "ok": False}
            try:
                desired = self.registry.get(iid)
            except ManagerError:
                result["state"] = "desired_missing"
                results.append(result)
                all_ok = False
                continue
            if desired.fingerprint() != item.get("desired_fingerprint"):
                result["state"] = "desired_changed"
                results.append(result)
                all_ok = False
                continue
            mcp_active = self._unit_active(str(item.get("mcp_unit")))
            tunnel_active = self._unit_active(str(item.get("tunnel_unit")))
            discovery_status, _ = self._http(
                f"http://{item.get('mcp_host')}:{item.get('mcp_port')}/.well-known/mcp.json"
            )
            health_status, health_text = self._http(
                f"http://{item.get('tunnel_health_host')}:{item.get('tunnel_health_port')}/healthz"
            )
            ready_status, ready_text = self._http(
                f"http://{item.get('tunnel_health_host')}:{item.get('tunnel_health_port')}/readyz"
            )
            recovered = (
                mcp_active
                and tunnel_active
                and discovery_status == 200
                and health_status == 200
                and health_text == "live"
                and ready_status == 200
                and ready_text == "ready"
            )
            result.update(
                {
                    "ok": recovered,
                    "state": "recovered" if recovered else "waiting_recovery",
                    "mcp_active": mcp_active,
                    "tunnel_active": tunnel_active,
                    "mcp_discovery_status": discovery_status,
                    "tunnel_health_status": health_status,
                    "tunnel_health": health_text,
                    "tunnel_ready_status": ready_status,
                    "tunnel_ready": ready_text,
                }
            )
            results.append(result)
            if not recovered:
                all_ok = False
        return {"ok": all_ok, "checked_at": _utc_now(), "instances": results}

    def check(self) -> dict[str, Any]:
        if not self.checkpoint_path.is_file():
            return {
                "ok": True,
                "action": "reboot-check",
                "state": "no_checkpoint",
                "checkpoint_path": str(self.checkpoint_path),
            }
        checkpoint = _read_json(self.checkpoint_path)
        current_boot_id = self._boot_id()
        checkpoint["last_checked_at"] = _utc_now()
        before = checkpoint.get("boot_id_before")
        if not isinstance(before, str):
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "reboot checkpoint boot_id_before is invalid")

        if current_boot_id == before:
            self._persist(checkpoint)
            state = checkpoint.get("state")
            if state in {"authorization_failed", "request_failed"}:
                return self._public(checkpoint, ok=False, state=str(state))
            return self._public(checkpoint, ok=True, state="pending_no_boot_change")

        checkpoint["boot_id_after"] = current_boot_id
        checkpoint["state"] = "boot_observed"
        reconciliation = self._reconcile_instances(checkpoint)
        checkpoint["reconciliation"] = reconciliation
        checkpoint["state"] = "reconciled" if reconciliation["ok"] else "boot_observed_waiting_recovery"
        self._persist(checkpoint)
        return self._public(checkpoint, ok=bool(reconciliation["ok"]), state=str(checkpoint["state"]))

    def _public(self, checkpoint: Mapping[str, Any], *, ok: bool, state: str | None = None) -> dict[str, Any]:
        return redact_object(
            {
                "ok": ok,
                "action": "reboot" if state is None else "reboot-check",
                "state": state if state is not None else checkpoint.get("state"),
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "checkpoint_path": str(self.checkpoint_path),
                "boot_id_before": checkpoint.get("boot_id_before"),
                "boot_id_after": checkpoint.get("boot_id_after"),
                "authorization": checkpoint.get("authorization"),
                "request_result": checkpoint.get("request_result"),
                "expected_instances": checkpoint.get("expected_instances"),
                "reconciliation": checkpoint.get("reconciliation"),
                "last_checked_at": checkpoint.get("last_checked_at"),
            }
        )
