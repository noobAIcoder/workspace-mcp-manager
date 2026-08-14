from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .domain import DeploymentTarget, DesiredInstance, RuntimeTarget
from .generation import GeneratedBundle
from .paths import ManagerPaths
from .planning import HostResourceObserver, PlanOperation, ReconciliationPlan


PROJECTION_VERSION = 1
PLAN_FINGERPRINT_VERSION = 1
PLAN_EXECUTION_PRECONDITION_VERSION = 1
TEMPLATE_VERSION = 2


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def observed_plan_inputs(
    plan: ReconciliationPlan,
    bundle: GeneratedBundle,
    observer: HostResourceObserver,
) -> list[dict[str, Any]]:
    """Capture bounded host identities that can affect reconciliation freshness.

    Generated resources are re-observed after planning so two equally-shaped
    UPDATE plans against materially different current resources do not share a
    reviewed-plan fingerprint. Unit state is included for generated systemd
    resources because START/STOP/RESTART applicability depends on it.
    """

    inputs: list[dict[str, Any]] = []
    for resource in sorted(bundle.resources, key=lambda item: item.resource_id):
        observation = observer.observe_resource(resource)
        record: dict[str, Any] = {
            "resource_id": resource.resource_id,
            "status": observation.status.value,
            "detail": observation.detail,
        }
        if resource.unit_name:
            unit = observer.observe_unit(resource.unit_name)
            record["unit"] = {
                "loaded": unit.loaded,
                "enabled": unit.enabled,
                "active": unit.active,
                "detail": unit.detail,
            }
        inputs.append(record)
    return inputs


def plan_fingerprint(
    plan: ReconciliationPlan,
    bundle: GeneratedBundle,
    *,
    observed_inputs: list[dict[str, Any]] | None = None,
) -> str:
    payload = {
        "contract_version": PLAN_FINGERPRINT_VERSION,
        "execution_precondition_version": PLAN_EXECUTION_PRECONDITION_VERSION,
        "instance_id": plan.instance_id,
        "desired_fingerprint": plan.desired_fingerprint,
        "valid": plan.valid,
        "operations": [
            {
                "operation": item.operation.value,
                "target": item.target,
                "resource_id": item.resource_id,
            }
            for item in plan.operations
        ],
        "desired_resources": [
            {
                "resource_id": resource.resource_id,
                "kind": resource.kind.value,
                "path": resource.path,
                "fingerprint": resource.fingerprint(),
            }
            for resource in sorted(bundle.resources, key=lambda resource: resource.resource_id)
        ],
        "observed_inputs": observed_inputs or [],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _resource_label(resource_id: str | None, target: str) -> str:
    if resource_id is None:
        return target
    if resource_id.startswith("access-ro-"):
        return f'RO access "{resource_id.removeprefix("access-ro-")}"'
    if resource_id.startswith("access-rw-"):
        return f'RW access "{resource_id.removeprefix("access-rw-")}"'
    labels = {
        "mcp-unit": "MCP service",
        "tunnel-unit": "Tunnel service",
        "admission-guard-unit": "recovery guard service",
        "state-dir": "instance state directory",
        "state-owner": "instance ownership marker",
        "access-root": "access root",
        "access-owner": "access ownership marker",
        "access-ignore": "access ignore policy",
        "dev-git-identity": "Git identity",
        "dev-git-transport": "Git transport",
    }
    return labels.get(resource_id, resource_id.replace("-", " "))


def semantic_plan_operations(plan: ReconciliationPlan) -> list[dict[str, Any]]:
    verbs = {
        PlanOperation.CREATE: "Add",
        PlanOperation.UPDATE: "Update",
        PlanOperation.ENABLE: "Enable",
        PlanOperation.DISABLE: "Disable",
        PlanOperation.START: "Start",
        PlanOperation.STOP: "Stop",
        PlanOperation.RESTART: "Restart",
        PlanOperation.REMOVE: "Remove",
        PlanOperation.NOOP: "No change",
        PlanOperation.CONFLICT: "Blocked",
    }
    result: list[dict[str, Any]] = []
    for item in plan.operations:
        label = _resource_label(item.resource_id, item.target)
        result.append(
            {
                "operation": item.operation.value,
                "resource_id": item.resource_id,
                "target": item.target,
                "description": f"{verbs[item.operation]} {label}",
            }
        )
    return result


def operator_template(paths: ManagerPaths) -> dict[str, Any]:
    home = paths.account_home
    defaults = {
        "config_version": 2,
        "instance_id": "",
        "workspace_path": "",
        "lifecycle": {"deployment": "present", "runtime": "stopped"},
        "mcp": {
            "binary": str(home / ".local/bin/coding-tools-mcp"),
            "host": "127.0.0.1",
            "port": None,
            "permission_mode": "trusted",
            "shell_env_inherit": "all",
            "exec_path": f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
            "external_roots": [],
        },
        "tunnel": {
            "binary": str(home / ".local/bin/tunnel-client"),
            "id": "",
            "profile": "",
            "health_host": "127.0.0.1",
            "health_port": None,
            "env_file": str(home / ".config/tunnel-client/runtime.env"),
        },
        "access": {"read_only": [], "read_write": []},
        "github": {"mode": "disabled", "config_dir": None, "binary": None},
        "git": {"identity": None, "remote": None},
        "agent": {"mode": "none", "ssh_auth_sock": None},
        "recovery": {
            "admission_guard_enabled": False,
            "guard_interval_seconds": 30,
            "recovery_cooldown_seconds": 120,
        },
    }
    return {
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "config_version": 2,
        "defaults": defaults,
        "required_operator_fields": [
            "instance_id",
            "workspace_path",
            "mcp.port",
            "tunnel.id",
            "tunnel.health_port",
        ],
    }


def _unit_runtime(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "Unknown"
    if value.get("query_exit_code") not in {0, "0"}:
        return "Unknown"
    load = str(value.get("LoadState", ""))
    active = str(value.get("ActiveState", ""))
    sub = str(value.get("SubState", ""))
    if load in {"not-found", "masked"}:
        return "Stopped"
    if active == "failed":
        return "Failed"
    if active == "active" and sub not in {"dead", "failed"}:
        return "Running"
    if active == "inactive" or sub == "dead":
        return "Stopped"
    return "Unknown"


def _probe_state(value: Any, *, ready: bool = False) -> str:
    if not isinstance(value, Mapping):
        return "Unknown"
    status = value.get("http_status")
    if isinstance(status, bool):
        status = None
    if isinstance(status, int) and 200 <= status < 300:
        return "Ready" if ready else "Healthy"
    if isinstance(status, int):
        return "Not ready" if ready else "Failed"
    return "Unknown"


def _reconciliation(status: Mapping[str, Any]) -> str:
    plan = status.get("plan")
    if not isinstance(plan, Mapping):
        return "Unknown"
    if plan.get("valid") is False:
        return "Blocked"
    if plan.get("valid") is not True:
        return "Unknown"
    operations = plan.get("operations")
    if not isinstance(operations, list):
        return "Unknown"
    return "Applied" if all(isinstance(item, Mapping) and item.get("operation") == "NOOP" for item in operations) else "Pending"


def _has_residual(status: Mapping[str, Any]) -> bool:
    plan = status.get("plan")
    if isinstance(plan, Mapping) and isinstance(plan.get("operations"), list):
        if any(isinstance(item, Mapping) and item.get("operation") != "NOOP" for item in plan["operations"]):
            return True
    units = status.get("units")
    if isinstance(units, Mapping):
        for item in units.values():
            if _unit_runtime(item) in {"Running", "Failed"}:
                return True
    return False


def _recovery(status: Mapping[str, Any], *, absent_clean: bool) -> str:
    if absent_clean:
        return "Not applicable"
    applied = status.get("applied")
    if isinstance(applied, Mapping) and applied.get("error"):
        return "Required"
    if "recent_transaction_evidence" not in status:
        return "Unknown"
    transactions = status.get("recent_transaction_evidence")
    if not isinstance(transactions, list):
        return "Unknown"
    if transactions:
        latest = transactions[-1]
        if not isinstance(latest, Mapping):
            return "Unknown"
        if latest.get("error"):
            return "Required"
        state = latest.get("status")
        if state == "running":
            return "In progress"
        if state == "failed":
            return "Required"
        if state not in {"succeeded", None}:
            return "Unknown"
    return "Clean"


def derive_operator_state(desired: DesiredInstance, status: Mapping[str, Any]) -> dict[str, Any]:
    desired_absent = desired.lifecycle.deployment is DeploymentTarget.ABSENT
    desired_running = (
        desired.lifecycle.deployment is DeploymentTarget.PRESENT
        and desired.lifecycle.runtime is RuntimeTarget.RUNNING
    )
    desired_stopped = (
        desired.lifecycle.deployment is DeploymentTarget.PRESENT
        and desired.lifecycle.runtime is RuntimeTarget.STOPPED
    )
    desired_label = (
        "Absent"
        if desired_absent
        else ("Present + Running" if desired_running else "Present + Stopped")
    )

    residual = _has_residual(status)
    absent_clean = desired_absent and not residual and _reconciliation(status) == "Applied"
    units = status.get("units") if isinstance(status.get("units"), Mapping) else {}
    runtime = "Not applicable" if absent_clean else _unit_runtime(units.get("mcp-unit"))

    endpoints = status.get("endpoints") if isinstance(status.get("endpoints"), Mapping) else {}
    if absent_clean or (desired_stopped and runtime == "Stopped"):
        health = "Not applicable"
        tunnel = "Not applicable"
    else:
        if runtime == "Failed":
            health = "Failed"
        elif runtime == "Stopped" and desired_running:
            health = "Degraded"
        elif runtime == "Running":
            health = _probe_state(endpoints.get("mcp_discovery"))
        else:
            health = "Unknown"
        tunnel = _probe_state(endpoints.get("tunnel_ready"), ready=True)

    reconciliation = _reconciliation(status)
    recovery = _recovery(status, absent_clean=absent_clean)
    attention: list[str] = []
    if desired_running and runtime != "Running":
        attention.append(f"Expected running; observed {runtime.lower()}")
    if desired_stopped and runtime == "Running":
        attention.append("Unexpected runtime: desired stopped but observed running")
    if desired_absent and residual:
        attention.append("Residual runtime/resources remain for absent deployment")
    if desired_running and health in {"Degraded", "Failed", "Unknown"}:
        attention.append(f"MCP health is {health.lower()}")
    if desired_running and tunnel != "Ready":
        attention.append(f"Tunnel is {tunnel.lower()}")
    if reconciliation == "Pending":
        attention.append("Pending reconciliation")
    elif reconciliation == "Blocked":
        attention.append("Reconciliation blocked")
    elif reconciliation == "Unknown":
        attention.append("Reconciliation state unknown")
    if recovery == "Required":
        attention.append("Recovery required")
    elif recovery == "In progress":
        attention.append("Recovery in progress")
    elif recovery == "Unknown":
        attention.append("Recovery state unknown")

    return {
        "desired": desired_label,
        "runtime": runtime,
        "health": health,
        "tunnel": tunnel,
        "reconciliation": reconciliation,
        "recovery": recovery,
        "attention_reasons": attention,
    }
