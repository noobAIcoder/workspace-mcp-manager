from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .development_environment import MANAGED_GITHUB_ENV_REMOVE
from .domain import DeploymentTarget, DesiredInstance, GithubConfigMode
from .errors import ErrorCode, ManagerError, config_error
from .paths import ManagerPaths
from .redaction import SECRET_ENV_KEYS, redact_text
from .registry import InstanceRegistry


MAX_PROMPT_BYTES = 1024 * 1024
MAX_OUTPUT_READ_BYTES = 64 * 1024
MAX_LAST_MESSAGE_BYTES = 64 * 1024
INTERRUPT_GRACE_SECONDS = 5.0
JOB_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[a-f0-9]{12}$")
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
MODE_SANDBOX = {"read": "read-only", "write": "workspace-write"}
CODEX_JOB_FILES = frozenset({"request.json", "status.json", "stdout.log", "stderr.log", "result.json"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:12]}"


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _atomic_write(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
        value = json.loads(text)
    except FileNotFoundError as exc:
        raise ManagerError(ErrorCode.INSTANCE_NOT_FOUND, f"Codex job state not found: {path.parent.name}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagerError(ErrorCode.IO_ERROR, f"cannot read Codex job state: {path}") from exc
    if not isinstance(value, dict):
        raise ManagerError(ErrorCode.IO_ERROR, f"Codex job state must be an object: {path}")
    return value


def _resolve_from_path(exec_path: str, executable: str = "codex") -> str | None:
    for entry in exec_path.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / executable
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


class CodexJobManager:
    """Durable detached Codex job lifecycle bound to one managed instance."""

    def __init__(self, paths: ManagerPaths, registry: InstanceRegistry) -> None:
        self.paths = paths
        self.registry = registry
        self.jobs_root = paths.state_root / "instances"

    def _instance_root(self, instance_id: str) -> Path:
        # Registry lookup is the authoritative instance-ID validation boundary.
        desired = self.registry.get(instance_id)
        return self.jobs_root / desired.instance_id.value / "codex"

    def _job_dir(self, instance_id: str, job_id: str) -> Path:
        if not JOB_ID_RE.fullmatch(job_id):
            raise config_error("invalid Codex job id", job_id=job_id)
        return self._instance_root(instance_id) / job_id

    @staticmethod
    def _unit_name(instance_id: str, job_id: str) -> str:
        return f"workspace-mcp-codex-{instance_id}-{job_id}.service"

    def _execution_env(self, desired: DesiredInstance) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in SECRET_ENV_KEYS}
        if desired.config_version >= 2 and desired.github.mode is GithubConfigMode.MANAGED:
            for key in MANAGED_GITHUB_ENV_REMOVE:
                env.pop(key, None)
        env["HOME"] = str(self.paths.account_home)
        env["PATH"] = desired.mcp.exec_path
        env["CODEX_HOME"] = str(self.paths.account_home / ".codex")
        env["GIT_TERMINAL_PROMPT"] = "0"
        if desired.github.config_dir:
            env["GH_CONFIG_DIR"] = desired.github.config_dir
        else:
            env.pop("GH_CONFIG_DIR", None)
        return env

    def _preflight(self, desired: DesiredInstance) -> tuple[str, str]:
        codex_path = _resolve_from_path(desired.mcp.exec_path)
        if codex_path is None:
            raise ManagerError(
                ErrorCode.HOST_INSPECTION_FAILED,
                "Codex executable is not resolvable from the configured MCP execution PATH",
                {"instance_id": desired.instance_id.value, "path": desired.mcp.exec_path},
            )
        try:
            completed = subprocess.run(
                [codex_path, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=10,
                check=False,
                env=self._execution_env(desired),
                cwd=desired.workspace_path,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError(
                ErrorCode.HOST_INSPECTION_FAILED,
                "Codex executable preflight failed",
                {"path": codex_path, "error": redact_text(str(exc))},
            ) from exc
        if completed.returncode != 0:
            detail = redact_text((completed.stderr or completed.stdout).strip())[-2000:]
            raise ManagerError(
                ErrorCode.HOST_INSPECTION_FAILED,
                "Codex executable preflight failed",
                {"path": codex_path, "exit_code": completed.returncode, "detail": detail},
            )
        lines = (completed.stdout or completed.stderr).strip().splitlines()
        version = lines[0] if lines else "unknown"
        return codex_path, version

    def _validate_desired(self, desired: DesiredInstance) -> None:
        if desired.lifecycle.deployment is not DeploymentTarget.PRESENT:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "Codex jobs require a present instance")
        workspace = Path(desired.workspace_path)
        if workspace.is_symlink() or not workspace.is_dir():
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "Codex workspace is unavailable",
                {"workspace": desired.workspace_path},
            )

    def start(self, instance_id: str, *, mode: str, prompt: str) -> dict[str, Any]:
        if mode not in {"read", "write"}:
            raise config_error("Codex mode must be read or write")
        if not isinstance(prompt, str) or not prompt:
            raise config_error("Codex prompt must be a non-empty string")
        prompt_bytes = prompt.encode("utf-8")
        if len(prompt_bytes) > MAX_PROMPT_BYTES:
            raise config_error("Codex prompt exceeds the maximum persisted size", max_bytes=MAX_PROMPT_BYTES)

        desired = self.registry.get(instance_id)
        self._validate_desired(desired)
        codex_path, version = self._preflight(desired)
        job_id = _job_id()
        job_dir = self._job_dir(instance_id, job_id)
        try:
            self.jobs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.jobs_root, 0o700)
            instance_root = self._instance_root(instance_id)
            instance_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(instance_root, 0o700)
            job_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            os.chmod(job_dir, 0o700)
        except OSError as exc:
            raise ManagerError(ErrorCode.IO_ERROR, "cannot create Codex job state directory") from exc

        sandbox = MODE_SANDBOX[mode]
        request = {
            "schema_version": 1,
            "job_id": job_id,
            "instance_id": desired.instance_id.value,
            "workspace_path": desired.workspace_path,
            "desired_fingerprint": desired.fingerprint(),
            "mode": mode,
            "sandbox": sandbox,
            "codex_path": codex_path,
            "codex_version": version,
            "account_home": str(self.paths.account_home),
            "codex_home": str(self.paths.account_home / ".codex"),
            "exec_path": desired.mcp.exec_path,
            "gh_config_dir": desired.github.config_dir,
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "created_at": _utc_now(),
        }
        _atomic_write(job_dir / "request.json", _json_text(request))
        _atomic_write(
            job_dir / "status.json",
            _json_text(
                {
                    "schema_version": 1,
                    "job_id": job_id,
                    "instance_id": desired.instance_id.value,
                    "workspace_path": desired.workspace_path,
                    "mode": mode,
                    "state": "queued",
                    "created_at": request["created_at"],
                    "started_at": None,
                    "completed_at": None,
                }
            ),
        )
        for name in ("stdout.log", "stderr.log"):
            log_path = job_dir / name
            log_path.touch(mode=0o600, exist_ok=False)
            os.chmod(log_path, 0o600)

        unit = self._unit_name(desired.instance_id.value, job_id)
        argv = [
            "systemd-run",
            "--user",
            "--no-block",
            "--collect",
            "--quiet",
            f"--unit={unit.removesuffix('.service')}",
            "--property=Type=exec",
            "--property=UMask=0077",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=10",
            f"--property=WorkingDirectory={desired.workspace_path}",
            str(self.paths.manager_executable),
            "--registry-dir",
            str(self.paths.registry_dir),
            "_runtime",
            "codex-worker",
            desired.instance_id.value,
            job_id,
        ]
        try:
            launched = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._record_launch_failure(job_dir, request, redact_text(str(exc)))
            raise ManagerError(ErrorCode.IO_ERROR, "cannot launch detached Codex worker", {"job_id": job_id}) from exc
        if launched.returncode != 0:
            detail = redact_text((launched.stderr or launched.stdout).strip())[-2000:]
            self._record_launch_failure(job_dir, request, detail)
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "cannot launch detached Codex worker",
                {"job_id": job_id, "exit_code": launched.returncode, "detail": detail},
            )
        return {
            "ok": True,
            "action": "start",
            "instance_id": desired.instance_id.value,
            "workspace": desired.workspace_path,
            "job_id": job_id,
            "state": "queued",
            "mode": mode,
            "sandbox": sandbox,
            "unit": unit,
            "codex_path": codex_path,
            "codex_version": version,
        }

    @staticmethod
    def _record_launch_failure(job_dir: Path, request: Mapping[str, Any], detail: str) -> None:
        completed_at = _utc_now()
        status = {
            "schema_version": 1,
            "job_id": request["job_id"],
            "instance_id": request["instance_id"],
            "workspace_path": request["workspace_path"],
            "mode": request["mode"],
            "state": "failed",
            "created_at": request["created_at"],
            "started_at": None,
            "completed_at": completed_at,
        }
        result = {
            "schema_version": 1,
            "job_id": request["job_id"],
            "state": "failed",
            "exit_code": None,
            "completed_at": completed_at,
            "error": "worker launch failed",
            "detail": detail,
            "last_message": None,
        }
        _atomic_write(job_dir / "status.json", _json_text(status))
        _atomic_write(job_dir / "result.json", _json_text(result))

    def _load_binding(self, instance_id: str, job_id: str) -> tuple[DesiredInstance, Path, dict[str, Any]]:
        desired = self.registry.get(instance_id)
        job_dir = self._job_dir(instance_id, job_id)
        request = _read_json(job_dir / "request.json")
        if request.get("schema_version") != 1:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "Codex job request schema is invalid", {"job_id": job_id})
        mode = request.get("mode")
        if mode not in MODE_SANDBOX:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "Codex job mode is invalid", {"job_id": job_id})
        expected = {
            "job_id": job_id,
            "instance_id": desired.instance_id.value,
            "workspace_path": desired.workspace_path,
            "sandbox": MODE_SANDBOX[mode],
        }
        for key, value in expected.items():
            if request.get(key) != value:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    "Codex job binding does not match the managed instance",
                    {"job_id": job_id, "field": key},
                )
        prompt = request.get("prompt")
        digest = request.get("prompt_sha256")
        if not isinstance(prompt, str) or hashlib.sha256(prompt.encode("utf-8")).hexdigest() != digest:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "Codex persisted request digest is invalid", {"job_id": job_id})
        self._validate_execution_binding(desired, request)
        return desired, job_dir, request

    def _validate_execution_binding(self, desired: DesiredInstance, request: Mapping[str, Any]) -> None:
        current_codex = _resolve_from_path(desired.mcp.exec_path)
        expected = {
            "codex_path": current_codex,
            "account_home": str(self.paths.account_home),
            "codex_home": str(self.paths.account_home / ".codex"),
            "exec_path": desired.mcp.exec_path,
            "gh_config_dir": desired.github.config_dir,
        }
        if current_codex is None:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "Codex executable is no longer resolvable from the configured MCP execution PATH",
                {"job_id": request.get("job_id")},
            )
        for key, value in expected.items():
            if request.get(key) != value:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    "Codex execution environment changed after job submission",
                    {"job_id": request.get("job_id"), "field": key},
                )

    def run_worker(self, instance_id: str, job_id: str) -> None:
        desired, job_dir, request = self._load_binding(instance_id, job_id)
        self._validate_desired(desired)
        self._validate_execution_binding(desired, request)
        codex_path = str(request["codex_path"])

        status = _read_json(job_dir / "status.json")
        status["state"] = "running"
        status["started_at"] = _utc_now()
        _atomic_write(job_dir / "status.json", _json_text(status))

        sandbox = str(request["sandbox"])
        last_message_path = job_dir / "last-message.txt"
        argv = [
            codex_path,
            "exec",
            "--color",
            "never",
            "-s",
            sandbox,
            "-C",
            desired.workspace_path,
            "--output-last-message",
            str(last_message_path),
            "-",
        ]
        try:
            with (job_dir / "stdout.log").open("ab", buffering=0) as stdout, (
                job_dir / "stderr.log"
            ).open("ab", buffering=0) as stderr:
                completed = subprocess.run(
                    argv,
                    input=request["prompt"].encode("utf-8"),
                    stdout=stdout,
                    stderr=stderr,
                    cwd=desired.workspace_path,
                    env=self._execution_env(desired),
                    check=False,
                )
            exit_code = completed.returncode
        except OSError as exc:
            self._finish_worker(job_dir, request, exit_code=None, error=redact_text(str(exc)))
            raise ManagerError(ErrorCode.IO_ERROR, "Codex worker execution failed", {"job_id": job_id}) from exc

        self._finish_worker(job_dir, request, exit_code=exit_code, error=None)
        if exit_code != 0:
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "Codex worker exited unsuccessfully",
                {"job_id": job_id, "exit_code": exit_code},
            )

    def _finish_worker(
        self,
        job_dir: Path,
        request: Mapping[str, Any],
        *,
        exit_code: int | None,
        error: str | None,
    ) -> None:
        completed_at = _utc_now()
        state = "succeeded" if exit_code == 0 and error is None else "failed"
        last_message: str | None = None
        last_path = job_dir / "last-message.txt"
        if last_path.is_file():
            try:
                data = last_path.read_bytes()[:MAX_LAST_MESSAGE_BYTES]
                last_message = data.decode("utf-8", errors="replace")
                last_path.unlink()
            except OSError:
                last_message = None
        status = _read_json(job_dir / "status.json")
        status["state"] = state
        status["completed_at"] = completed_at
        _atomic_write(job_dir / "status.json", _json_text(status))
        result = {
            "schema_version": 1,
            "job_id": request["job_id"],
            "instance_id": request["instance_id"],
            "workspace_path": request["workspace_path"],
            "mode": request["mode"],
            "state": state,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "error": error,
            "last_message": last_message,
        }
        _atomic_write(job_dir / "result.json", _json_text(result))

    @staticmethod
    def _unit_snapshot(unit: str) -> dict[str, Any]:
        env = {key: value for key, value in os.environ.items() if key not in SECRET_ENV_KEYS}
        try:
            completed = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit,
                    "--no-pager",
                    "--property=LoadState",
                    "--property=ActiveState",
                    "--property=SubState",
                    "--property=Result",
                    "--property=ExecMainStatus",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=5,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "LoadState": "unknown",
                "ActiveState": "unknown",
                "SubState": "unknown",
                "error": redact_text(str(exc)),
            }
        snapshot: dict[str, Any] = {
            "query_exit_code": completed.returncode,
            "query_error": redact_text(completed.stderr.strip())[-1000:] or None,
        }
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                snapshot[key] = value
        snapshot.setdefault("LoadState", "not-found" if completed.returncode else "unknown")
        snapshot.setdefault("ActiveState", "unknown")
        snapshot.setdefault("SubState", "unknown")
        return snapshot

    def _persist_interrupted(
        self,
        job_dir: Path,
        request: Mapping[str, Any],
        status: dict[str, Any],
    ) -> dict[str, Any]:
        completed_at = _utc_now()
        status["state"] = "interrupted"
        status["completed_at"] = completed_at
        _atomic_write(job_dir / "status.json", _json_text(status))
        result = {
            "schema_version": 1,
            "job_id": request["job_id"],
            "instance_id": request["instance_id"],
            "workspace_path": request["workspace_path"],
            "mode": request["mode"],
            "state": "interrupted",
            "exit_code": None,
            "completed_at": completed_at,
            "error": "detached Codex unit is no longer active",
            "last_message": None,
        }
        _atomic_write(job_dir / "result.json", _json_text(result))
        return result

    @staticmethod
    def _within_interrupt_grace(status: Mapping[str, Any]) -> bool:
        timestamp = status.get("started_at") or status.get("created_at")
        if not isinstance(timestamp, str):
            return False
        try:
            captured = datetime.fromisoformat(timestamp)
        except ValueError:
            return False
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()
        return 0 <= age < INTERRUPT_GRACE_SECONDS

    def _reconciled_status(
        self,
        desired: DesiredInstance,
        job_dir: Path,
        request: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        status = _read_json(job_dir / "status.json")
        result_path = job_dir / "result.json"
        unit = self._unit_name(desired.instance_id.value, str(request["job_id"]))
        snapshot = self._unit_snapshot(unit)
        if result_path.is_file():
            result = _read_json(result_path)
            if result.get("state") in TERMINAL_STATES:
                status["state"] = result.get("state")
                status["completed_at"] = result.get("completed_at")
            return status, result, snapshot

        active = snapshot.get("ActiveState")
        if active in {"active", "activating", "reloading", "deactivating"}:
            # Do not collapse a queued/activating worker into interrupted.
            if status.get("state") not in {"queued", "running"}:
                status["state"] = "running"
            return status, None, snapshot

        if status.get("state") in {"queued", "running"} and self._within_interrupt_grace(status):
            # `systemd-run --no-block` may return just before the transient unit
            # becomes observable through `systemctl show`. Do not permanently
            # classify that normal activation race as interruption.
            return status, None, snapshot

        result = self._persist_interrupted(job_dir, request, status)
        return status, result, snapshot

    def status(self, instance_id: str, job_id: str) -> dict[str, Any]:
        desired, job_dir, request = self._load_binding(instance_id, job_id)
        status, result, unit_state = self._reconciled_status(desired, job_dir, request)
        return {
            "ok": True,
            "instance_id": desired.instance_id.value,
            "workspace": desired.workspace_path,
            "job_id": job_id,
            "mode": request["mode"],
            "state": status.get("state"),
            "created_at": status.get("created_at"),
            "started_at": status.get("started_at"),
            "completed_at": status.get("completed_at"),
            "unit": self._unit_name(desired.instance_id.value, job_id),
            "unit_state": unit_state,
            "result": result,
        }

    @staticmethod
    def _tail(path: Path, limit_bytes: int) -> dict[str, Any]:
        try:
            total = path.stat().st_size
            with path.open("rb") as handle:
                start = max(0, total - limit_bytes)
                handle.seek(start)
                data = handle.read(limit_bytes)
        except OSError as exc:
            raise ManagerError(ErrorCode.IO_ERROR, f"cannot read Codex job output: {path.name}") from exc
        return {
            "content": data.decode("utf-8", errors="replace"),
            "total_bytes": total,
            "returned_bytes": len(data),
            "truncated": total > len(data),
        }

    def output(self, instance_id: str, job_id: str, *, limit_bytes: int) -> dict[str, Any]:
        desired, job_dir, request = self._load_binding(instance_id, job_id)
        if (
            isinstance(limit_bytes, bool)
            or not isinstance(limit_bytes, int)
            or not 1 <= limit_bytes <= MAX_OUTPUT_READ_BYTES
        ):
            raise config_error("Codex output limit is out of range", max_bytes=MAX_OUTPUT_READ_BYTES)
        status, result, _unit_state = self._reconciled_status(desired, job_dir, request)
        return {
            "ok": True,
            "instance_id": desired.instance_id.value,
            "workspace": desired.workspace_path,
            "job_id": job_id,
            "mode": request["mode"],
            "state": result.get("state") if result is not None else status.get("state"),
            "limit_bytes": limit_bytes,
            "stdout": self._tail(job_dir / "stdout.log", limit_bytes),
            "stderr": self._tail(job_dir / "stderr.log", limit_bytes),
        }

    def cancel(self, instance_id: str, job_id: str) -> dict[str, Any]:
        desired, job_dir, request = self._load_binding(instance_id, job_id)
        status, result, _unit_state = self._reconciled_status(desired, job_dir, request)
        state = result.get("state") if result is not None else status.get("state")
        if state in TERMINAL_STATES:
            return {
                "ok": True,
                "action": "cancel",
                "instance_id": desired.instance_id.value,
                "job_id": job_id,
                "state": state,
                "already_terminal": True,
            }
        unit = self._unit_name(desired.instance_id.value, job_id)
        try:
            stopped = subprocess.run(
                ["systemctl", "--user", "stop", unit],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=20,
                check=False,
                env={key: value for key, value in os.environ.items() if key not in SECRET_ENV_KEYS},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError(ErrorCode.IO_ERROR, "cannot stop detached Codex worker", {"job_id": job_id}) from exc
        # A fast worker may have completed between the first status read and stop.
        refreshed = _read_json(job_dir / "status.json")
        existing_result = _read_json(job_dir / "result.json") if (job_dir / "result.json").is_file() else None
        if existing_result is not None and existing_result.get("state") in TERMINAL_STATES:
            return {
                "ok": True,
                "action": "cancel",
                "instance_id": desired.instance_id.value,
                "job_id": job_id,
                "state": existing_result.get("state"),
                "already_terminal": True,
            }
        if stopped.returncode != 0:
            detail = redact_text((stopped.stderr or stopped.stdout).strip())[-2000:]
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "cannot stop detached Codex worker",
                {"job_id": job_id, "exit_code": stopped.returncode, "detail": detail},
            )
        completed_at = _utc_now()
        refreshed["state"] = "cancelled"
        refreshed["completed_at"] = completed_at
        _atomic_write(job_dir / "status.json", _json_text(refreshed))
        _atomic_write(
            job_dir / "result.json",
            _json_text(
                {
                    "schema_version": 1,
                    "job_id": job_id,
                    "instance_id": desired.instance_id.value,
                    "workspace_path": desired.workspace_path,
                    "mode": request["mode"],
                    "state": "cancelled",
                    "exit_code": None,
                    "completed_at": completed_at,
                    "error": None,
                    "last_message": None,
                }
            ),
        )
        return {
            "ok": True,
            "action": "cancel",
            "instance_id": desired.instance_id.value,
            "job_id": job_id,
            "state": "cancelled",
            "already_terminal": False,
        }
