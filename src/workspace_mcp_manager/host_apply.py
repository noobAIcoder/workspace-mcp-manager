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

from .access import stale_applied_access_resources
from .domain import DeploymentTarget, DesiredInstance, LifecycleIntent, RuntimeTarget
from .errors import ErrorCode, ManagerError
from .generation import GeneratedBundle, GeneratedResource, ResourceGenerator, ResourceKind
from .paths import ManagerPaths
from .planning import HostResourceObserver, ObservationStatus, PlanOperation, ReconciliationPlan, ReconciliationPlanner
from .redaction import redact_object, redact_text, sanitized_subprocess_env
from .registry import InstanceRegistry


JOURNAL_SCHEMA_VERSION = 1
APPLIED_SCHEMA_VERSION = 1
DEFAULT_READINESS_TIMEOUT_SECONDS = 30.0
RUNTIME_STATE_FILES = frozenset({
    "mcp.log",
    "tunnel.log",
    "tunnel-supervisor.log",
    "admission-guard.log",
})


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
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    safe_env = dict(env) if env is not None else sanitized_subprocess_env(os.environ)
    io_kwargs: dict[str, Any]
    if input_text is None:
        io_kwargs = {"stdin": subprocess.DEVNULL}
    else:
        io_kwargs = {"input": input_text}
    try:
        completed = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            env=safe_env,
            **io_kwargs,
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
        completed = _run(["systemctl", "--user", "daemon-reload"], check=False)
        if completed.returncode != 0:
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "systemd user daemon-reload failed",
                {
                    "exit_code": completed.returncode,
                    "detail": redact_text((completed.stderr or completed.stdout).strip())[-2000:],
                },
            )

    def _unit_action(self, action: str, unit: str) -> None:
        completed = _run(["systemctl", "--user", action, unit], check=False)
        if completed.returncode == 0:
            return
        raise ManagerError(
            ErrorCode.IO_ERROR,
            f"systemctl --user {action} failed for {unit}",
            {
                "action": action,
                "unit": unit,
                "exit_code": completed.returncode,
                "detail": redact_text((completed.stderr or completed.stdout).strip())[-2000:],
                "unit_status": self.unit_snapshot(unit),
                "journal": self.journal_tail(unit, lines=80),
            },
        )

    def enable(self, unit: str) -> None:
        self._unit_action("enable", unit)

    def disable(self, unit: str) -> None:
        self._unit_action("disable", unit)

    def start(self, unit: str) -> None:
        self._unit_action("start", unit)

    def stop(self, unit: str) -> None:
        self._unit_action("stop", unit)

    def restart(self, unit: str) -> None:
        self._unit_action("restart", unit)

    def unit_snapshot(self, unit: str) -> dict[str, Any]:
        completed = _run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "-p",
                "LoadState",
                "-p",
                "UnitFileState",
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "MainPID",
                "-p",
                "ExecMainCode",
                "-p",
                "ExecMainStatus",
            ],
            check=False,
        )
        values: dict[str, Any] = {"unit": unit, "query_exit_code": completed.returncode}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        if completed.returncode != 0 and completed.stderr.strip():
            values["error"] = redact_text(completed.stderr.strip())[-1000:]
        return values

    def journal_tail(self, unit: str, *, lines: int = 100) -> str:
        bounded_lines = max(1, min(int(lines), 500))
        completed = _run(
            [
                "journalctl",
                "--user",
                "-u",
                unit,
                "--no-pager",
                "-n",
                str(bounded_lines),
                "-o",
                "short-iso",
            ],
            check=False,
        )
        text = completed.stdout if completed.stdout else completed.stderr
        return redact_text(text.strip())[-20000:]

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

    def preflight_access_namespace(
        self,
        desired: DesiredInstance,
        *,
        target_root: Path | None = None,
    ) -> None:
        folders = [(folder, True) for folder in desired.access.read_only] + [
            (folder, False) for folder in desired.access.read_write
        ]
        if not folders:
            return
        @contextmanager
        def target_directory():
            if target_root is not None:
                if target_root.is_symlink() or not target_root.is_dir():
                    raise ManagerError(
                        ErrorCode.RECONCILIATION_CONFLICT,
                        "access namespace target root is not a directory",
                        {"path": str(target_root)},
                    )
                yield target_root
                return
            staging_parent = self.state_root / "staging"
            staging_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(
                prefix=f"access-preflight-{desired.instance_id.value}-",
                dir=staging_parent,
            ) as directory:
                root = Path(directory)
                for folder, _read_only in folders:
                    (root / folder.alias).mkdir(mode=0o700)
                yield root

        with target_directory() as root:
            argv = [
                "systemd-run",
                "--user",
                "--wait",
                "--collect",
                "--pipe",
                "--quiet",
                f"--unit=workspace-mcp-manager-access-preflight-{desired.instance_id.value}-{uuid.uuid4().hex[:8]}",
                "--property=Type=exec",
                "--property=PrivateUsers=true",
            ]
            for folder, read_only in folders:
                target = root / folder.alias
                if target.is_symlink() or not target.is_dir():
                    raise ManagerError(
                        ErrorCode.RECONCILIATION_CONFLICT,
                        "access namespace target mountpoint is not a directory",
                        {"alias": folder.alias, "path": str(target)},
                    )
                directive = "BindReadOnlyPaths" if read_only else "BindPaths"
                argv.append(f"--property={directive}={folder.path}:{target}:rbind")
            argv.append("/usr/bin/true")
            completed = _run(argv, timeout=30.0, check=False)
            if completed.returncode != 0:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    "access namespace preflight failed",
                    {
                        "instance_id": desired.instance_id.value,
                        "exit_code": completed.returncode,
                        "detail": redact_text((completed.stderr or completed.stdout).strip())[-2000:],
                    },
                )


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

    def _supporting_failed_transaction(
        self,
        instance_id: str,
        *,
        state_dir: Path,
        owner_path: Path,
    ) -> Path | None:
        transaction_dir = self.journal_root / instance_id
        if not transaction_dir.is_dir():
            return None
        try:
            candidates = sorted(transaction_dir.glob("*.json"), reverse=True)
        except OSError:
            return None
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            if payload.get("status") != "failed" or payload.get("instance_id") != instance_id:
                continue
            executed = payload.get("executed")
            if not isinstance(executed, list):
                continue
            created_targets = {
                str(item.get("target"))
                for item in executed
                if isinstance(item, Mapping) and item.get("operation") == "CREATE"
            }
            if str(state_dir) in created_targets and str(owner_path) in created_targets:
                return path
        return None

    def _recover_failed_first_apply_state(
        self,
        instance_id: str,
        bundle: GeneratedBundle,
    ) -> dict[str, Any] | None:
        by_id = {resource.resource_id: resource for resource in bundle.resources}
        state_resource = by_id.get("state-dir")
        owner_resource = by_id.get("state-owner")
        if state_resource is None or owner_resource is None or owner_resource.content is None:
            return None
        state_dir = Path(state_resource.path)
        owner_path = Path(owner_resource.path)
        if (self.applied_root / f"{instance_id}.json").exists():
            return None
        if not state_dir.exists() or owner_path.exists():
            return None
        if state_dir.is_symlink() or not state_dir.is_dir():
            return None
        try:
            metadata = state_dir.stat()
        except OSError:
            return None
        if stat.S_IMODE(metadata.st_mode) != state_resource.mode:
            return None
        supporting = self._supporting_failed_transaction(
            instance_id, state_dir=state_dir, owner_path=owner_path
        )
        if supporting is None:
            return None
        try:
            entries = sorted(state_dir.iterdir(), key=lambda child: child.name)
        except OSError:
            return None
        for child in entries:
            if child.name not in RUNTIME_STATE_FILES:
                return None
            if child.is_symlink() or not child.is_file():
                return None

        recovery_id = (
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
            f"recovery-{uuid.uuid4().hex[:8]}"
        )
        recovery_path = self._journal_path(instance_id, recovery_id)
        recovery: dict[str, Any] = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "transaction_id": recovery_id,
            "instance_id": instance_id,
            "action": "recover-failed-first-apply",
            "started_at": utc_now(),
            "status": "running",
            "supporting_failed_transaction": supporting.name,
            "retained_runtime_files": [child.name for child in entries],
            "plan": {
                "valid": True,
                "operations": [
                    {
                        "operation": "CREATE",
                        "target": str(owner_path),
                        "reason": "restore journal-proven ownership after failed first apply",
                        "resource_id": "state-owner",
                    }
                ],
            },
            "attempted": [],
            "executed": [],
        }
        self._write_journal(recovery_path, recovery)
        try:
            self._attempt(recovery, recovery_path, "CREATE", str(owner_path))
            _atomic_write(owner_path, owner_resource.content, mode=owner_resource.mode)
            self._record(recovery, recovery_path, "CREATE", str(owner_path))
        except Exception as exc:
            recovery["status"] = "failed"
            recovery["completed_at"] = utc_now()
            recovery["error"] = redact_text(str(exc))
            self._write_journal(recovery_path, recovery)
            raise ManagerError(
                ErrorCode.IO_ERROR,
                "failed to restore manager ownership for residual first-apply state",
                {"state_dir": str(state_dir), "recovery_journal": str(recovery_path)},
            ) from exc
        recovery["status"] = "succeeded"
        recovery["completed_at"] = utc_now()
        self._write_journal(recovery_path, recovery)
        return {
            "recovered": True,
            "state_dir": str(state_dir),
            "restored_owner": str(owner_path),
            "retained_runtime_files": [child.name for child in entries],
            "supporting_failed_transaction": supporting.name,
            "recovery_journal": str(recovery_path),
        }

    def apply(self, instance_id: str, *, force_restart: bool = False, action: str = "apply") -> dict[str, Any]:
        with self._instance_lock(instance_id):
            desired = self.registry.get(instance_id)
            bundle = self.generator.generate(desired)

            # P6 host-side verification gate that could not be run reliably from
            # the MCP Landlock context during P4/P5. Deletion does not need to
            # re-validate unit content that is about to be removed.
            if desired.lifecycle.deployment is DeploymentTarget.PRESENT:
                self.systemd.verify_generated_units(bundle)

            # First observation validates preconditions. A narrowly proven
            # residual from an earlier failed first apply may restore its
            # ownership marker before the authoritative pre-mutation plan is
            # computed. Recovery has its own journaled recovery plan.
            initial = self.planner.plan(desired)
            residual_recovery: dict[str, Any] | None = None
            if not initial.valid:
                residual_recovery = self._recover_failed_first_apply_state(
                    instance_id, bundle
                )
                if residual_recovery is not None:
                    initial = self.planner.plan(desired)
            if not initial.valid:
                raise self._conflict(initial)
            plan = self.planner.plan(desired)
            if not plan.valid:
                raise self._conflict(plan)
            if (
                desired.lifecycle.deployment is DeploymentTarget.PRESENT
                and (desired.access.read_only or desired.access.read_write)
            ):
                self.systemd.preflight_access_namespace(desired)

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
                "attempted": [],
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

                applied_path = self.applied_root / f"{instance_id}.json"
                self._attempt(journal, journal_path, "WRITE_APPLIED", str(applied_path))
                self._write_applied(desired, bundle, transaction_id)
                self._record(journal, journal_path, "WRITE_APPLIED", str(applied_path))
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
                    "residual_recovery": residual_recovery,
                }
            except Exception as exc:
                rollback_result = self._rollback(rollback)
                journal["status"] = "failed"
                journal["completed_at"] = utc_now()
                journal["error"] = redact_text(str(exc))
                journal["rollback"] = rollback_result
                self._write_journal(journal_path, journal)
                transaction_evidence = redact_object(
                    {
                        "transaction_id": transaction_id,
                        "journal": str(journal_path),
                        "attempted": journal.get("attempted", [])[-20:],
                        "executed": journal.get("executed", [])[-20:],
                        "rollback": rollback_result,
                    }
                )
                if isinstance(exc, ManagerError):
                    details = dict(exc.details)
                    details["transaction"] = transaction_evidence
                    raise ManagerError(exc.code, exc.message, details) from exc
                raise ManagerError(
                    ErrorCode.IO_ERROR,
                    f"apply failed: {redact_text(str(exc))}",
                    {"transaction": transaction_evidence},
                ) from exc

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
        for resource in stale_applied_access_resources(self.paths, desired, bundle):
            by_id.setdefault(resource.resource_id, resource)
        operations = list(plan.operations)
        present = desired.lifecycle.deployment is DeploymentTarget.PRESENT

        if present:
            file_ops = [item for item in operations if item.operation in {PlanOperation.CREATE, PlanOperation.UPDATE}]
            access_file_ops = [
                item
                for item in file_ops
                if (item.resource_id or "").startswith("access-")
            ]
            other_file_ops = [item for item in file_ops if item not in access_file_ops]
            self._apply_resource_operations(access_file_ops, by_id, rollback, journal, journal_path)
            if desired.access.read_only or desired.access.read_write:
                self.systemd.preflight_access_namespace(
                    desired,
                    target_root=Path(desired.workspace_path) / ".workspace-mcp-access",
                )
            self._apply_resource_operations(other_file_ops, by_id, rollback, journal, journal_path)
            unit_file_changed = any(
                item.resource_id in by_id
                and by_id[item.resource_id].kind is ResourceKind.SYSTEMD_UNIT
                for item in file_ops
            )
            if unit_file_changed:
                self._attempt(journal, journal_path, "DAEMON_RELOAD", "systemd --user")
                self.systemd.daemon_reload()
                self._record(journal, journal_path, "DAEMON_RELOAD", "systemd --user")

            for item in self._ordered_unit_ops(operations, {PlanOperation.ENABLE}, start_order=True):
                self._attempt(journal, journal_path, item.operation.value, item.target)
                self.systemd.enable(item.target)
                rollback.enabled_units.append(item.target)
                self._record(journal, journal_path, item.operation.value, item.target)

            for item in self._ordered_unit_ops(operations, {PlanOperation.START, PlanOperation.RESTART}, start_order=True):
                was_active = self.systemd.is_active(item.target)
                self._attempt(journal, journal_path, item.operation.value, item.target)
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
                    self._attempt(journal, journal_path, "FORCE_RESTART", unit)
                    self.systemd.restart(unit)
                    if not was_active:
                        rollback.started_units.append(unit)
                    self._record(journal, journal_path, "FORCE_RESTART", unit)

            remove_ops = [item for item in operations if item.operation is PlanOperation.REMOVE]
            if remove_ops:
                self._validate_destructive_operations(remove_ops, by_id)
                self._remove_resource_operations(remove_ops, by_id, rollback, journal, journal_path)
        else:
            # Re-verify destructive ownership and residual contents immediately
            # before the first stop/disable/remove mutation.
            self._validate_destructive_operations(operations, by_id)

            # Stop and disable before deleting any unit/profile/state resource.
            for item in self._ordered_unit_ops(operations, {PlanOperation.STOP}, start_order=False):
                self._attempt(journal, journal_path, item.operation.value, item.target)
                self.systemd.stop(item.target)
                self._record(journal, journal_path, item.operation.value, item.target)
            for item in self._ordered_unit_ops(operations, {PlanOperation.DISABLE}, start_order=False):
                self._attempt(journal, journal_path, item.operation.value, item.target)
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
                self._attempt(journal, journal_path, "DAEMON_RELOAD", "systemd --user")
                self.systemd.daemon_reload()
                self._record(journal, journal_path, "DAEMON_RELOAD", "systemd --user")

    def _attempt(self, journal: dict[str, Any], journal_path: Path, operation: str, target: str) -> None:
        journal.setdefault("attempted", []).append(
            {"operation": operation, "target": target, "at": utc_now()}
        )
        self._write_journal(journal_path, journal)

    def _record(self, journal: dict[str, Any], journal_path: Path, operation: str, target: str) -> None:
        journal.setdefault("executed", []).append(
            {"operation": operation, "target": target, "at": utc_now()}
        )
        self._write_journal(journal_path, journal)

    def _validate_destructive_operations(
        self,
        operations: Iterable[Any],
        by_id: Mapping[str, GeneratedResource],
    ) -> None:
        operations = list(operations)
        remove_items = [item for item in operations if item.operation is PlanOperation.REMOVE]
        remove_resource_ids = {item.resource_id for item in remove_items}

        for item in operations:
            if item.operation not in {PlanOperation.STOP, PlanOperation.DISABLE}:
                continue
            if item.resource_id not in remove_resource_ids:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    f"refusing to mutate unit without verified manager ownership: {item.target}",
                    {
                        "operation": item.operation.value,
                        "unit": item.target,
                        "resource_id": item.resource_id,
                    },
                )

        observer = HostResourceObserver()
        for item in remove_items:
            resource = by_id.get(item.resource_id or "")
            if resource is None:
                raise ManagerError(
                    ErrorCode.IO_ERROR,
                    f"plan references unknown generated resource: {item.resource_id}",
                )
            if getattr(resource, "remove_with_instance", True) is False:
                continue
            observation = observer.observe_resource(resource)
            if observation.status not in {ObservationStatus.EXACT, ObservationStatus.DIFFERENT}:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    f"refusing destructive operation without current ownership evidence: {resource.path}",
                    {
                        "resource_id": resource.resource_id,
                        "observation": observation.status.value,
                        "detail": observation.detail,
                    },
                )

        if not any(item.resource_id == "state-dir" for item in remove_items):
            return
        state_resource = by_id.get("state-dir")
        if state_resource is None:
            return
        state_dir = Path(state_resource.path)
        try:
            entries = list(state_dir.iterdir())
        except OSError as exc:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                f"cannot verify manager state directory before removal: {state_dir}",
                {"detail": redact_text(str(exc))},
            ) from exc
        for child in entries:
            if child.name == ".owner":
                if child.is_symlink() or not child.is_file():
                    raise ManagerError(
                        ErrorCode.RECONCILIATION_CONFLICT,
                        f"refusing removal with unexpected ownership-marker type: {child}",
                    )
                continue
            if child.name not in RUNTIME_STATE_FILES:
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    f"refusing removal with unknown manager-state content: {child}",
                )
            if child.is_symlink() or not child.is_file():
                raise ManagerError(
                    ErrorCode.RECONCILIATION_CONFLICT,
                    f"refusing removal with unexpected runtime-state type: {child}",
                )

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
            self._attempt(journal, journal_path, item.operation.value, str(path))
            if resource.kind is ResourceKind.DIRECTORY:
                if not path.exists():
                    path.mkdir(parents=True, exist_ok=False, mode=resource.mode)
                    rollback.created_paths.append(path)
                    if resource.resource_id == "state-dir":
                        rollback.created_state_dirs.append(path)
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

        # Files first, then deepest directories. Ownership and residual content
        # were re-verified as one batch immediately before destructive mutation.
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
            self._attempt(journal, journal_path, item.operation.value, str(path))
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
        for child in list(path.iterdir()):
            if child.name not in RUNTIME_STATE_FILES:
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
        details: dict[str, Any] = {"last_observation": last}
        if hasattr(self.systemd, "unit_snapshot"):
            details["unit_status"] = {
                mcp_unit: self.systemd.unit_snapshot(mcp_unit),
                tunnel_unit: self.systemd.unit_snapshot(tunnel_unit),
            }
        if hasattr(self.systemd, "journal_tail"):
            details["unit_journals"] = {
                mcp_unit: self.systemd.journal_tail(mcp_unit, lines=80),
                tunnel_unit: self.systemd.journal_tail(tunnel_unit, lines=80),
            }
        raise ManagerError(
            ErrorCode.RECONCILIATION_CONFLICT,
            "instance did not become ready before timeout",
            details,
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


class HostInstanceStatusService:
    """Read-only host-side status/log service for a declared instance."""

    def __init__(
        self,
        paths: ManagerPaths,
        registry: InstanceRegistry,
        *,
        generator: ResourceGenerator | None = None,
        planner: ReconciliationPlanner | None = None,
        systemd: SystemdUserAdapter | None = None,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.generator = generator or ResourceGenerator(paths)
        self.planner = planner or ReconciliationPlanner(paths, registry, generator=self.generator)
        self.systemd = systemd or SystemdUserAdapter(state_root=paths.state_root)

    def status(self, instance_id: str) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        bundle = self.generator.generate(desired)
        plan = self.planner.plan(desired)
        units: dict[str, Any] = {}
        for resource in bundle.resources:
            if resource.unit_name:
                units[resource.resource_id] = self.systemd.unit_snapshot(resource.unit_name)

        endpoints = {
            "mcp_discovery": {
                "url": f"http://{desired.mcp.host}:{desired.mcp.port}/.well-known/mcp.json",
            },
            "tunnel_health": {
                "url": f"http://{desired.tunnel.health_host}:{desired.tunnel.health_port}/healthz",
            },
            "tunnel_ready": {
                "url": f"http://{desired.tunnel.health_host}:{desired.tunnel.health_port}/readyz",
            },
        }
        for item in endpoints.values():
            item["http_status"] = _http_status(str(item["url"]))

        applied_path = self.paths.state_root / "applied" / f"{instance_id}.json"
        applied: dict[str, Any] | None = None
        if applied_path.is_file():
            try:
                decoded = json.loads(applied_path.read_text(encoding="utf-8", errors="strict"))
                if isinstance(decoded, dict):
                    applied = decoded
            except (OSError, UnicodeError, json.JSONDecodeError):
                applied = {"error": "applied state is unreadable"}

        transaction_dir = self.paths.state_root / "transactions" / instance_id
        transactions: list[str] = []
        if transaction_dir.is_dir():
            try:
                transactions = [path.name for path in sorted(transaction_dir.glob("*.json"))[-10:]]
            except OSError:
                transactions = []

        return {
            "ok": plan.valid,
            "instance_id": instance_id,
            "desired_fingerprint": desired.fingerprint(),
            "lifecycle": desired.lifecycle.to_dict(),
            "plan": plan.to_dict(),
            "units": units,
            "endpoints": endpoints,
            "applied": applied,
            "recent_transactions": transactions,
        }

    def logs(self, instance_id: str, *, lines: int = 100) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        bundle = self.generator.generate(desired)
        unit_logs: dict[str, str] = {}
        for resource in bundle.resources:
            if resource.unit_name:
                unit_logs[resource.unit_name] = self.systemd.journal_tail(resource.unit_name, lines=lines)

        state_dir = self.paths.state_root / "instances" / instance_id
        file_logs: dict[str, str] = {}
        for name in ("mcp.log", "tunnel.log", "tunnel-supervisor.log", "admission-guard.log"):
            path = state_dir / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                file_logs[name] = f"<unreadable: {exc}>"
                continue
            file_logs[name] = redact_text("\n".join(text.splitlines()[-max(1, min(lines, 500)):]))[-20000:]

        return {
            "ok": True,
            "instance_id": instance_id,
            "lines": max(1, min(lines, 500)),
            "journals": unit_logs,
            "files": file_logs,
        }


class HostExecutionBridge:
    """Dispatch manager host/registry operations through transient user-systemd workers.

    The bridge is the single boundary used by MCP-facing CLI operations that
    need access to the real account registry, generated systemd resources, or
    other manager-owned host state. Desired declarations contain no secrets, so
    registry create/update payloads are safely streamed over stdin rather than
    exposed as command-line arguments.
    """

    def __init__(self, paths: ManagerPaths) -> None:
        self.paths = paths

    def run_registry_write(self, action: str, desired: DesiredInstance) -> dict[str, Any]:
        if action not in {"create", "update"}:
            raise ManagerError(ErrorCode.IO_ERROR, f"unsupported registry action: {action}")
        return self._run_json(
            ["_runtime", "registry-write", action],
            input_text=desired.canonical_json() + "\n",
            unit_fragment=f"registry-{action}-{desired.instance_id.value}",
        )

    def run_list(self) -> dict[str, Any]:
        return self._run_json(["_runtime", "list"], unit_fragment="list")

    def run_show(self, instance_id: str) -> dict[str, Any]:
        return self._run_json(["_runtime", "show", instance_id], unit_fragment=f"show-{instance_id}")

    def run_render(self, instance_id: str, *, include_content: bool = False) -> dict[str, Any]:
        args = ["_runtime", "render", instance_id]
        if include_content:
            args.append("--include-content")
        return self._run_json(args, unit_fragment=f"render-{instance_id}")

    def run_lifecycle(self, action: str, instance_id: str) -> dict[str, Any]:
        return self._run_json(
            ["_runtime", "lifecycle", action, instance_id],
            unit_fragment=f"{action}-{instance_id}",
        )

    def run_plan(self, instance_id: str) -> dict[str, Any]:
        return self._run_json(["_runtime", "plan", instance_id], unit_fragment=f"plan-{instance_id}")

    def run_status(self, instance_id: str) -> dict[str, Any]:
        return self._run_json(["_runtime", "status", instance_id], unit_fragment=f"status-{instance_id}")

    def run_logs(self, instance_id: str, *, lines: int = 100) -> dict[str, Any]:
        return self._run_json(
            ["_runtime", "logs", instance_id, "--lines", str(lines)],
            unit_fragment=f"logs-{instance_id}",
        )

    def run_git(self, instance_id: str) -> dict[str, Any]:
        return self._run_json(["_runtime", "git", instance_id], unit_fragment=f"git-{instance_id}")

    def run_access(
        self,
        action: str,
        instance_id: str,
        *,
        alias: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"list", "add-ro", "add-rw", "remove"}:
            raise ManagerError(ErrorCode.IO_ERROR, f"unsupported access action: {action}")
        args = ["_runtime", "access", action, instance_id]
        if alias is not None:
            args.append(alias)
        if path is not None:
            args.append(path)
        return self._run_json(args, unit_fragment=f"access-{action}-{instance_id}")

    def _run_json(
        self,
        runtime_args: Sequence[str],
        *,
        input_text: str | None = None,
        unit_fragment: str | None = None,
    ) -> dict[str, Any]:
        if not self.paths.manager_executable.is_file() or not os.access(self.paths.manager_executable, os.X_OK):
            raise ManagerError(
                ErrorCode.IO_ERROR,
                f"manager executable is not installed/executable: {self.paths.manager_executable}",
            )
        fragment = unit_fragment or (runtime_args[-1] if runtime_args else "operation")
        unit_name = f"workspace-mcp-manager-host-{_safe_unit_fragment(fragment)}-{uuid.uuid4().hex[:8]}"
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
        completed = _run(argv, timeout=180.0, check=False, input_text=input_text)
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
        self.created_state_dirs: list[Path] = []
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
        preserve_owner_parents: set[Path] = set()
        for state_dir in self.created_state_dirs:
            if not state_dir.is_dir() or state_dir.is_symlink():
                continue
            try:
                entries = list(state_dir.iterdir())
            except OSError:
                preserve_owner_parents.add(state_dir)
                continue
            for child in entries:
                if child.name == ".owner":
                    continue
                if child.name not in RUNTIME_STATE_FILES or child.is_symlink() or not child.is_file():
                    preserve_owner_parents.add(state_dir)
                    continue
                try:
                    child.unlink()
                except OSError:
                    preserve_owner_parents.add(state_dir)

        for path in reversed(self.created_paths):
            try:
                if path.name == ".owner" and path.parent in preserve_owner_parents:
                    continue
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
