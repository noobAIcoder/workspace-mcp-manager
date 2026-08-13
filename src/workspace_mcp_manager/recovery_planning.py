from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any, Mapping

from .domain import DeploymentTarget, DesiredInstance
from .generation import GeneratedBundle
from .paths import ManagerPaths
from .planning import PlanItem, PlanOperation, ReconciliationPlan


RUNTIME_STATE_FILES = frozenset(
    {
        "mcp.log",
        "tunnel.log",
        "tunnel-supervisor.log",
        "admission-guard.log",
        "admission-recovery.json",
    }
)


def _supporting_failed_transaction(
    paths: ManagerPaths,
    instance_id: str,
    *,
    state_dir: Path,
    owner_path: Path,
) -> Path | None:
    transaction_dir = paths.state_root / "transactions" / instance_id
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


def _recoverable_residue(
    paths: ManagerPaths,
    desired: DesiredInstance,
    bundle: GeneratedBundle,
) -> dict[str, Any] | None:
    if desired.lifecycle.deployment is not DeploymentTarget.PRESENT:
        return None

    by_id = {resource.resource_id: resource for resource in bundle.resources}
    state_resource = by_id.get("state-dir")
    owner_resource = by_id.get("state-owner")
    if state_resource is None or owner_resource is None:
        return None

    instance_id = desired.instance_id.value
    state_dir = Path(state_resource.path)
    owner_path = Path(owner_resource.path)
    if (paths.state_root / "applied" / f"{instance_id}.json").exists():
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

    supporting = _supporting_failed_transaction(
        paths,
        instance_id,
        state_dir=state_dir,
        owner_path=owner_path,
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

    return {
        "state_dir": str(state_dir),
        "owner_path": str(owner_path),
        "supporting_failed_transaction": supporting.name,
        "runtime_files": [child.name for child in entries],
    }


def project_recoverable_failed_first_apply(
    paths: ManagerPaths,
    desired: DesiredInstance,
    bundle: GeneratedBundle,
    plan: ReconciliationPlan,
) -> ReconciliationPlan:
    """Project one journal-proven first-apply residue into a valid read-only plan.

    Generic resource observation remains fail-closed: an existing manager state
    directory without its ownership marker is FOREIGN. P6 recovery is the sole
    exception and is recognized only when a failed transaction proves that this
    manager created both the directory and marker, no applied state exists, and
    the residual directory contains only bounded runtime logs.

    This function never mutates host state. The subsequent lifecycle apply still
    performs recovery under the instance lock and recomputes a fresh plan before
    normal mutation.
    """

    if plan.valid:
        return plan
    residue = _recoverable_residue(paths, desired, bundle)
    if residue is None:
        return plan

    state_dir = residue["state_dir"]
    owner_path = residue["owner_path"]
    has_owner_create = any(
        item.operation is PlanOperation.CREATE
        and item.resource_id == "state-owner"
        and item.target == owner_path
        for item in plan.operations
    )
    if not has_owner_create:
        return plan

    replaced = False
    operations: list[PlanItem] = []
    for item in plan.operations:
        if (
            not replaced
            and item.operation is PlanOperation.CONFLICT
            and item.resource_id == "state-dir"
            and item.target == state_dir
            and "ownership marker is missing" in item.reason
        ):
            operations.append(
                PlanItem(
                    PlanOperation.NOOP,
                    state_dir,
                    "journal-proven failed-first-apply state will have ownership restored before normal apply mutation",
                    "state-dir",
                )
            )
            replaced = True
        else:
            operations.append(item)

    if not replaced:
        return plan

    valid = not any(item.operation is PlanOperation.CONFLICT for item in operations)
    warning = (
        "recoverable failed-first-apply residue detected; ownership restoration is supported by "
        f"transaction {residue['supporting_failed_transaction']} and preserves known runtime logs"
    )
    return ReconciliationPlan(
        instance_id=plan.instance_id,
        desired_fingerprint=plan.desired_fingerprint,
        valid=valid,
        operations=tuple(operations),
        warnings=tuple((*plan.warnings, warning)),
    )
