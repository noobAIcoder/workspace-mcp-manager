from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .domain import DeploymentTarget, DesiredInstance, LifecycleIntent, RuntimeTarget
from .errors import ErrorCode, ManagerError
from .generation import GeneratedBundle, GeneratedResource, ResourceGenerator, ResourceKind
from .paths import ManagerPaths
from .planning import PlanOperation, ReconciliationPlan, ReconciliationPlanner
from .redaction import redact_object, redact_text, sanitized_subprocess_env
from .registry import InstanceRegistry


JOURNAL_SCHEMA_VERSION = 1
APPLIED_SCHEMA_VERSION = 1
DEFAULT_READINESS_TIMEOUT_SECONDS = 30.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(redact_object(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            errors="strict",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise ManagerError(ErrorCode.IO_ERROR, f"cannot atomically write {path}: {exc}") from exc


def _run(
    argv: Sequence[str],
    *,
    timeout: float = 30.0,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    safe_env = dict(env) if env is not None else sanitized_subprocess_env(os.environ)
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
            env=safe_env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagerError(ErrorCode.IO_ERROR, f"command failed to execute: {argv[0]}: {exc}") from exc
    if check and completed.returncode != 0:
        detail = redact_text((completed.stderr or completed.stdout).strip())[-2000:]
        raise ManagerError(
            ErrorCode.IO_ERROR,
            f"command failed: {' '.join(argv[:3])}",
            {"exit_code": completed.returncode, "detail": detail},
        )
    return completed


class SystemdUserAdapter:
    def __init__(self, *, state_root: Path) -> None:
        self.state_root = state_root

    def daemon_reload(self) -> None:
        _run(["systemctl", "--user", "daemon-reload"])

    def enable(self, unit: str) -> None:
        _run(["systemctl", "--user", "enable", unit])

    def disable(self, unit: str) -> None:
        _run(["systemctl", "--user", "disable", unit])

    def start(self, unit: str) -> None:
        _run(["systemctl", "--user", "start", unit])

    def stop(self, unit: str) -> None:
        _run(["systemctl", "--user", "stop", unit])

    def restart(self, unit: str) -> None:
        _run(["systemctl", "--user", "restart", unit])

    def is_active(self, unit: str) -> bool:
        completed = _run(["systemctl", "--user", "is-active", "--quiet", unit], check=False)
        return completed.returncode == 0

    def is_enabled(self, unit: str) -> bool:
        completed = _run(["systemctl", "--user", "is-enabled", "--quiet", unit], check=False)
        return completed.returncode == 0

    def verify_generated_units(self, bundle: GeneratedBundle) -> None:
        units = [resource for resource in bundle.resources if resource.kind is ResourceKind.SYSTEMD_UNIT]
        if not units:
            return
        staging_parent = self.state_root / "staging"
        staging_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="systemd-verify-", dir=staging_parent) as directory:
            root = Path(directory)
            unit_paths: list[str] = []
            for resource in units:
                if resource.content is None:
                    raise ManagerError(
                        ErrorCode.IO_ERROR,
                        f"generated systemd resource has no content: {resource.resource_id}",
                    )
                path = root / Path(resource.path).name
                path.write_text(resource.content, encoding="utf-8", errors="strict")
                os.chmod(path, resource.mode)
                unit_paths.append(str(path))
            _run(["systemd-analyze", "--user", "verify", *unit_paths], timeout=30.0)


class HostApplyService:
    """Host-side P6 reconciler.

    This service is designed to run as a transient ``systemd --user`` process,
    outside the coding-tools-mcp Landlock sandbox. It always recomputes a fresh
    P5 plan immediately before mutation and persists a non-secret transaction
    journal. ``applied.json`` is written only after post-apply qualification.
    """

    def __init__(
        self,
        paths: ManagerPaths,
        registry: InstanceRegistry,
        *,
        planner: ReconciliationPlanner | None = None,
        generator: ResourceGenerator | None = None,
        systemd: SystemdUserAdapter | None = None,
        readiness_timeout_seconds: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.generator = generator or ResourceGenerator(paths)
        self.planner = planner or ReconciliationPlanner(paths, registry, generator=self.generator)
        self.systemd = systemd or SystemdUserAdapter(state_root=paths.state_root)
        self.readiness_timeout_seconds = readiness_timeout_seconds

    @property
    def journal_root(self) -> Path:
        return self.paths.state_root / "transactions"

    @property
    def applied_root(self) -> Path:
        return self.paths.state_root / "applied"

    @property
    def lock_root(self) -> Path:
        return self.paths.state_root / "locks"

    @contextmanager
    def _instance_lock(self, instance_id: str):
        self.lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.lock_root / f"{instance_id}.lock"
        with path.open("a+", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _journal_path(self, instance_id: str, transaction_id: str) -> Path:
        return self.journal_root / instance_id / f"{transaction_id}.json"

    def _write_journal(self, path: Path, payload: Mapping[str, Any]) -> None:
        _atomic_write(path, _json_text(payload), mode=0o600)

    def _conflict(self, plan: ReconciliationPlan) -> ManagerError:
        conflicts = [item.to_dict() for item in plan.operations if item.operation is PlanOperation.CONFLICT]
        return ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            f"reconciliation plan is not valid for {plan.instance_id}",
            {"plan": plan.to_dict(), "conflicts": conflicts},
        )

    def apply(self, instance_id: str, *, force_restart: bool = False, action: str = "apply") -> dict[str, Any]:
        with self._instance_lock(instance_id):
            desired = self.registry.get(instance_id)
            bundle = self.generator.generate(desired)

            # P6 host-side verification gate that could not be run reliably from
            # the MCP Landlock context during P4/P5. Deletion does not need to
            # re-validate unit content that is about to be removed.
            if desired.lifecycle.deployment is DeploymentTarget.PRESENT:
                self.systemd.verify_generated_units(bundle)

            # First observation validates preconditions. The second observation
            # happens immediately before the first mutation and is authoritative.
            initial = self.planner.plan(desired)
            if not initial.valid:
                raise self._conflict(initial)
            plan = self.planner.plan(desired)
            if not plan.valid:
                raise self._conflict(plan)

            transaction_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
            journal_path = self._journal_path(instance_id, transaction_id)
            journal: dict[str, Any] = {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "transaction_id": transaction_id,
                "instance_id": instance_id,
                "action": action,
                "desired_fingerprint": desired.fingerprint(),
                "started_at": utc_now(),
                "status": "running",
                "plan": plan.to_dict(),
                "executed": [],
            }
            self._write_journal(journal_path, journal)

            rollback = _RollbackState()
            try:
                self._execute(plan, bundle, desired, rollback, force_restart=force_restart, journal=journal, journal_path=journal_path)
                self._qualify(desired)

                final_plan = self.planner.plan(desired)
                if not final_plan.valid:
                    raise self._conflict(final_plan)
                remaining = [
                    item.to_dict()
                    for item in final_plan.operations
                    if item.operation not in {PlanOperation.NOOP}
                ]
                if remaining:
                    raise ManagerError(
                        ErrorCode.RECONCILIATION_CONFLICT,
                        "post-apply observation is not converged",
                        {"remaining_operations": remaining},
                    )

                self._write_applied(desired, bundle, transaction_id)
                rollback.discard()
                journal["status"] = "succeeded"
                journal["completed_at"] = utc_now()
                journal["post_plan"] = final_plan.to_dict()
                self._write_journal(journal_path, journal)
                return {
                    "ok": True,
                    "action": action,
                    "instance_id": instance_id,
                    "transaction_id": transaction_id,
                    "desired_fingerprint": desired.fingerprint(),
                    "journal": str(journal_path),
                }
            except Exception as exc:
                rollback_result = self._rollback(rollback)
                journal["status"] = "failed"
                journal["completed_at"] = utc_now()
                journal["error"] = redact_text(str(exc))
                journal["rollback"] = rollback_result
                self._write_journal(journal_path, journal)
                if isinstance(exc, ManagerError):
                    raise
                raise ManagerError(ErrorCode.IO_ERROR, f"apply failed: {redact_text(str(exc))}") from exc

    def _execute(
        self,
        plan: ReconciliationPlan,
        bundle: GeneratedBundle,
        desired: DesiredInstance,
        rollback: "_RollbackState",
        *,
        force_restart: bool,
        journal: dict[str, Any],
        journal_path: Path,
    ) -> None:
        by_id = {resource.resource_id: resource for resource in bundle.resources}
        operations = list(plan.operations)
        present = desired.lifecycle.deployment is DeploymentTarget.PRESENT

        if present:
            file_ops = [item for item in operations if item.operation in {PlanOperation.CREATE, PlanOperation.UPDATE}]
            self._apply_resource_operations(file_ops, by_id, rollback, journal, journal_path)
            unit_file_changed = any(
                item.resource_id in by_id
                and by_id[item.resource_id].kind is ResourceKind.SYSTEMD_UNIT
                for item in file_ops
            )
            if unit_file_changed:
                self.systemd.daemon_reload()
                self._record(journal, journal_path, "DAEMON_RELOAD", "systemd --user")

            for item in self._ordered_unit_ops(operations, {PlanOperation.ENABLE}, start_order=True):
                self.systemd.enable(item.target)
                rollback.enabled_units.append(item.target)
                self._record(journal, journal_path, item.operation.value, item.target)

            for item in self._ordered_unit_ops(operations, {PlanOperation.START, PlanOperation.RESTART}, start_order=True):
                was_active = self.systemd.is_active(item.target)
                if item.operation is PlanOperation.START:
                    self.systemd.start(item.target)
                else:
                    self.systemd.restart(item.target)
                if not was_active:
                    rollback.started_units.append(item.target)
                self._record(journal, journal_path, item.operation.value, item.target)

            if force_restart and desired.lifecycle.runtime is RuntimeTarget.RUNNING:
                units = self._core_runtime_units(bundle)
                for unit in units:
                    was_active = self.systemd.is_active(unit)
                    self.systemd.restart(unit)
                    if not was_active:
                        rollback.started_units.append(unit)
                    self._record(journal, journal_path, "FORCE_RESTART", unit)
        else:
            # Stop and disable before deleting any unit/profile/state resource.
            for item in self._ordered_unit_ops(operations, {PlanOperation.STOP}, start_order=False):
                self.systemd.stop(item.target)
                self._record(journal, journal_path, item.operation.value, item.target)
            for item in self._ordered_unit_ops(operations, {PlanOperation.DISABLE}, start_order=False):
                self.systemd.disable(item.target)
                self._record(journal, journal_path, item.operation.value, item.target)

            remove_ops = [item for item in operations if item.operation is PlanOperation.REMOVE]
            unit_file_changed = any(
                item.resource_id in by_id
                and by_id[item.resource_id].kind is ResourceKind.SYSTEMD_UNIT
                for item in remove_ops
            )
            self._remove_resource_operations(remove_ops, by_id, rollback, journal, journal_path)
            if unit_file_changed:
                self.systemd.daemon_reload()
                self._record(journal, journal_path, "DAEMON_RELOAD", "systemd --user")

    def _record(self, journal: dict[str, Any], journal_path: Path, operation: str, target: str) -> None:
        journal["executed"].append({"operation": operation, "target": target, "at": utc_now()})
        self._write_journal(journal_path, journal)

    @staticmethod
    def _core_runtime_units(bundle: GeneratedBundle) -> list[str]:
        mcp: str | None = None
        tunnel: str | None = None
        for resource in bundle.resources:
            if resource.resource_id == "mcp-unit":
                mcp = resource.unit_name
            elif resource.resource_id == "tunnel-unit":
                tunnel = resource.unit_name
        return [unit for unit in (mcp, tunnel) if unit]

    @staticmethod
    def _unit_priority(unit: str, *, start_order: bool) -> tuple[int, str]:
        if start_order:
            if unit.startswith("coding-tools-mcp-"):
                return (10, unit)
            if "admission-guard" in unit:
                return (20, unit)
            if unit.startswith("tunnel-client-"):
                return (30, unit)
        else:
            if unit.startswith("tunnel-client-"):
                return (10, unit)
            if "admission-guard" in unit:
                return (20, unit)
            if unit.startswith("coding-tools-mcp-"):
                return (30, unit)
        return (50, unit)

    def _ordered_unit_ops(
        self,
        operations: Iterable[Any],
        accepted: set[PlanOperation],
        *,
        start_order: bool,
    ) -> list[Any]:
        result = [item for item in operations if item.operation in accepted]
        result.sort(key=lambda item: self._unit_priority(item.target, start_order=start_order))
        return result

    def _apply_resource_operations(
        self,
        operations: Iterable[Any],
        by_id: Mapping[str, GeneratedResource],
        rollback: "_RollbackState",
        journal: dict[str, Any],
        journal_path: Path,
    ) -> None:
        # Bundle/resource ordering is deterministic; parent directories appear
        # before their child markers/files.
        for item in operations:
            resource = by_id.get(item.resource_id or "")
            if resource is None:
                raise ManagerError(ErrorCode.IO_ERROR, f"plan references unknown generated resource: {item.resource_id}")
            path = Path(resource.path)
            if item.operation is PlanOperation.UPDATE and path.exists():
                rollback.backup(path)
            if resource.kind is ResourceKind.DIRECTORY:
                if not path.exists():
                    path.mkdir(parents=True, exist_ok=False, mode=resource.mode)
                    rollback.created_paths.append(path)
                os.chmod(path, resource.mode)
            else:
                if resource.content is None:
                    raise ManagerError(ErrorCode.IO_ERROR, f"generated resource has no content: {resource.resource_id}")
                if not path.exists():
                    rollback.created_paths.append(path)
                _atomic_write(path, resource.content, mode=resource.mode)
            self._record(journal, journal_path, item.operation.value, str(path))

    def _remove_resource_operations(
        self,
        operations: Iterable[Any],
        by_id: Mapping[str, GeneratedResource],
        rollback: "_RollbackState",
        journal: dict[str, Any],
        journal_path: Path,
    ) -> None:
        resources: list[tuple[Any, GeneratedResource]] = []
        for item in operations:
            resource = by_id.get(item.resource_id or "")
            if resource is None:
                raise ManagerError(ErrorCode.IO_ERROR, f"plan references unknown generated resource: {item.resource_id}")
            if getattr(resource, "remove_with_instance", True) is False:
                continue
            resources.append((item, resource))

        # Files first, then deepest directories. This keeps ownership markers in
        # place until all destructive planning has already been validated.
        resources.sort(
            key=lambda pair: (
                1 if pair[1].kind is ResourceKind.DIRECTORY else 0,
                -len(Path(pair[1].path).parts),
            )
        )
        for item, resource in resources:
            path = Path(resource.path)
            if not path.exists() and not path.is_symlink():
                continue
            if resource.kind is ResourceKind.DIRECTORY:
                if path.is_symlink() or not path.is_dir():
                    raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"refusing to remove non-directory {path}")
                if resource.resource_id == "state-dir":
                    self._remove_known_runtime_state(path, rollback)
                if any(path.iterdir()):
                    raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"refusing to remove non-empty directory {path}")
                path.rmdir()
                rollback.removed_directories.append((path, resource.mode))
            else:
                rollback.backup(path)
                path.unlink()
            self._record(journal, journal_path, item.operation.value, str(path))

    @staticmethod
    def _remove_known_runtime_state(path: Path, rollback: "_RollbackState") -> None:
        # The P4 units append only these bounded runtime artifacts into the
        # manager-owned per-instance state directory. They are not declarative
        # resources, but retaining them would make a legitimate remove unable
        # to converge. Unknown files/directories still fail closed.
        allowed = {
            "mcp.log",
            "tunnel.log",
            "tunnel-supervisor.log",
            "admission-guard.log",
        }
        for child in list(path.iterdir()):
            if child.name not in allowed:
                continue
            if child.is_symlink() or not child.is_file():
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    f"refusing to remove unexpected runtime-state type: {child}",
                )
            rollback.backup(child)
            child.unlink()

    def _qualify(self, desired: DesiredInstance) -> None:
        iid = desired.instance_id.value
        mcp_unit = f"coding-tools-mcp-{iid}.service"
        tunnel_unit = f"tunnel-client-{iid}.service"
        present = desired.lifecycle.deployment is DeploymentTarget.PRESENT
        running = desired.lifecycle.runtime is RuntimeTarget.RUNNING
        if not present or not running:
            if self.systemd.is_active(tunnel_unit) or self.systemd.is_active(mcp_unit):
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "runtime units remain active after stop/remove")
            return

        deadline = time.monotonic() + self.readiness_timeout_seconds
        discovery = f"http://{desired.mcp.host}:{desired.mcp.port}/.well-known/mcp.json"
        health = f"http://{desired.tunnel.health_host}:{desired.tunnel.health_port}/healthz"
        ready = f"http://{desired.tunnel.health_host}:{desired.tunnel.health_port}/readyz"
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = {
                "mcp_unit_active": self.systemd.is_active(mcp_unit),
                "tunnel_unit_active": self.systemd.is_active(tunnel_unit),
                "discovery": _http_status(discovery),
                "health": _http_status(health),
                "ready": _http_status(ready),
            }
            if (
                last["mcp_unit_active"]
                and last["tunnel_unit_active"]
                and last["discovery"] == 200
                and last["health"] == 200
                and last["ready"] == 200
            ):
                return
            time.sleep(0.5)
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "instance did not become ready before timeout",
            {"last_observation": last},
        )

    def _write_applied(self, desired: DesiredInstance, bundle: GeneratedBundle, transaction_id: str) -> None:
        owned = []
        if desired.lifecycle.deployment is DeploymentTarget.PRESENT:
            for resource in bundle.resources:
                owned.append(
                    {
                        "resource_id": resource.resource_id,
                        "kind": resource.kind.value,
                        "path": resource.path,
                        "fingerprint": resource.fingerprint(),
                    }
                )
        payload = {
            "schema_version": APPLIED_SCHEMA_VERSION,
            "instance_id": desired.instance_id.value,
            "desired_fingerprint": desired.fingerprint(),
            "transaction_id": transaction_id,
            "last_successful_apply_at": utc_now(),
            "deployment": desired.lifecycle.deployment.value,
            "runtime": desired.lifecycle.runtime.value,
            "owned_resources": owned,
        }
        _atomic_write(self.applied_root / f"{desired.instance_id.value}.json", _json_text(payload), mode=0o600)

    def _rollback(self, rollback: "_RollbackState") -> dict[str, Any]:
        errors: list[str] = []
        for unit in reversed(rollback.started_units):
            try:
                self.systemd.stop(unit)
            except Exception as exc:  # best-effort fail-safe
                errors.append(redact_text(f"stop {unit}: {exc}"))
        for unit in reversed(rollback.enabled_units):
            try:
                self.systemd.disable(unit)
            except Exception as exc:
                errors.append(redact_text(f"disable {unit}: {exc}"))
        try:
            rollback.restore()
        except Exception as exc:
            errors.append(redact_text(f"restore resources: {exc}"))
        try:
            self.systemd.daemon_reload()
        except Exception as exc:
            errors.append(redact_text(f"daemon-reload: {exc}"))
        return {"ok": not errors, "errors": errors}


class HostLifecycleWorker:
    def __init__(self, paths: ManagerPaths, registry: InstanceRegistry) -> None:
        self.paths = paths
        self.registry = registry
        self.apply_service = HostApplyService(paths, registry)

    def run(self, action: str, instance_id: str) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        force_restart = False
        if action == "apply":
            pass
        elif action == "start":
            if desired.lifecycle.deployment is DeploymentTarget.ABSENT:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    "cannot start an instance whose deployment target is absent; update/create the desired declaration first",
                )
            desired = replace(
                desired,
                lifecycle=LifecycleIntent(DeploymentTarget.PRESENT, RuntimeTarget.RUNNING),
            )
            self.registry.update(desired)
        elif action == "stop":
            desired = replace(
                desired,
                lifecycle=LifecycleIntent(desired.lifecycle.deployment, RuntimeTarget.STOPPED),
            )
            self.registry.update(desired)
        elif action == "restart":
            if desired.lifecycle.deployment is DeploymentTarget.ABSENT:
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "cannot restart an absent deployment")
            desired = replace(
                desired,
                lifecycle=LifecycleIntent(DeploymentTarget.PRESENT, RuntimeTarget.RUNNING),
            )
            self.registry.update(desired)
            force_restart = True
        elif action == "remove":
            desired = replace(
                desired,
                lifecycle=LifecycleIntent(DeploymentTarget.ABSENT, RuntimeTarget.STOPPED),
            )
            self.registry.update(desired)
        else:
            raise ManagerError(ErrorCode.IO_ERROR, f"unsupported lifecycle action: {action}")
        return self.apply_service.apply(instance_id, force_restart=force_restart, action=action)


class HostExecutionBridge:
    """Dispatch a manager operation through a transient user-systemd worker."""

    def __init__(self, paths: ManagerPaths) -> None:
        self.paths = paths

    def run_lifecycle(self, action: str, instance_id: str) -> dict[str, Any]:
        return self._run_json(["_runtime", "lifecycle", action, instance_id])

    def run_plan(self, instance_id: str) -> dict[str, Any]:
        return self._run_json(["_runtime", "plan", instance_id])

    def _run_json(self, runtime_args: Sequence[str]) -> dict[str, Any]:
        if not self.paths.manager_executable.is_file() or not os.access(self.paths.manager_executable, os.X_OK):
            raise ManagerError(
                ErrorCode.IO_ERROR,
                f"manager executable is not installed/executable: {self.paths.manager_executable}",
            )
        instance_fragment = runtime_args[-1] if runtime_args else "operation"
        unit_name = f"workspace-mcp-manager-host-{_safe_unit_fragment(instance_fragment)}-{uuid.uuid4().hex[:8]}"
        argv = [
            "systemd-run",
            "--user",
            "--wait",
            "--collect",
            "--pipe",
            "--quiet",
            f"--unit={unit_name}",
            "--property=Type=exec",
            "--property=UMask=0077",
            str(self.paths.manager_executable),
            "--registry-dir",
            str(self.paths.registry_dir),
            *runtime_args,
        ]
        completed = _run(argv, timeout=180.0, check=False)
        stdout = completed.stdout.strip()
        stderr = redact_text(completed.stderr.strip())
        payload: dict[str, Any] | None = None
        if stdout:
            # The worker emits one JSON document. Be tolerant of a systemd line
            # preceding it on older systemd versions by trying lines from tail.
            candidates = [stdout, *reversed(stdout.splitlines())]
            for candidate in candidates:
                try:
                    decoded = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    payload = decoded
                    break
        if completed.returncode != 0:
            if payload is not None and payload.get("error"):
                error = payload["error"]
                code_value = error.get("code") if isinstance(error, Mapping) else None
                try:
                    code = ErrorCode(code_value)
                except Exception:
                    code = ErrorCode.IO_ERROR
                raise ManagerError(
                    code,
                    str(error.get("message", "host worker failed")),
                    error.get("details", {}) if isinstance(error, Mapping) else {},
                )
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "host worker failed",
                {"exit_code": completed.returncode, "stderr": stderr[-2000:]},
            )
        if payload is None:
            raise ManagerError(ErrorCode.IO_ERROR, "host worker returned no JSON result")
        return payload


class _RollbackState:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="workspace-mcp-manager-rollback-"))
        self.backups: list[tuple[Path, Path, int]] = []
        self.created_paths: list[Path] = []
        self.removed_directories: list[tuple[Path, int]] = []
        self.started_units: list[str] = []
        self.enabled_units: list[str] = []

    def backup(self, path: Path) -> None:
        if not path.exists() or path.is_dir():
            return
        metadata = path.stat()
        backup = self.root / f"{len(self.backups):04d}-{path.name}"
        shutil.copy2(path, backup)
        self.backups.append((path, backup, stat.S_IMODE(metadata.st_mode)))

    def discard(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def restore(self) -> None:
        for path, backup, mode in reversed(self.backups):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = backup.read_text(encoding="utf-8", errors="strict")
            _atomic_write(path, data, mode=mode)
        for path, mode in reversed(self.removed_directories):
            if not path.exists():
                path.mkdir(parents=True, exist_ok=False, mode=mode)
        for path in reversed(self.created_paths):
            try:
                if path.is_dir() and not path.is_symlink():
                    if not any(path.iterdir()):
                        path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(self.root, ignore_errors=True)


def _http_status(url: str) -> int | None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _safe_unit_fragment(value: str) -> str:
    result = "".join(character if character.isalnum() or character in "-_" else "-" for character in value)
    result = result.strip("-")
    return result[:48] or "operation"
