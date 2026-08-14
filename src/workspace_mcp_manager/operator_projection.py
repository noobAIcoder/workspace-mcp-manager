from __future__ import annotations

from typing import Any

from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError
from .generation import ResourceGenerator
from .host_apply import HostInstanceStatusService
from .operator_contracts import (
    PROJECTION_VERSION,
    derive_operator_state,
    observed_plan_inputs,
    plan_fingerprint,
    semantic_plan_operations,
)
from .paths import ManagerPaths
from .planning import PlanOperation, ReconciliationPlanner
from .recovery_planning import project_recoverable_failed_first_apply
from .registry import InstanceRegistry


class OperatorProjectionService:
    """Manager-authoritative PM3 read projections and candidate previews."""

    def __init__(
        self,
        paths: ManagerPaths,
        registry: InstanceRegistry,
        *,
        status_service: HostInstanceStatusService | None = None,
        generator: ResourceGenerator | None = None,
        planner: ReconciliationPlanner | None = None,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.generator = generator or ResourceGenerator(paths)
        self.planner = planner or ReconciliationPlanner(paths, registry, generator=self.generator)
        self.status_service = status_service or HostInstanceStatusService(
            paths,
            registry,
            generator=self.generator,
            planner=self.planner,
        )

    @staticmethod
    def _access_summary(desired: DesiredInstance) -> dict[str, Any]:
        return {
            "read_only_count": len(desired.access.read_only),
            "read_write_count": len(desired.access.read_write),
            "read_only_aliases": [item.alias for item in desired.access.read_only],
            "read_write_aliases": [item.alias for item in desired.access.read_write],
        }

    @staticmethod
    def _git_summary(desired: DesiredInstance) -> dict[str, Any]:
        raw = desired.to_dict()
        github = raw.get("github") if isinstance(raw.get("github"), dict) else {}
        git = raw.get("git") if isinstance(raw.get("git"), dict) else {}
        identity = git.get("identity") if isinstance(git, dict) else None
        remote = git.get("remote") if isinstance(git, dict) else None
        agent = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
        if desired.config_version == 1:
            github_mode = "external" if github.get("config_dir") else "disabled"
        else:
            github_mode = github.get("mode", "disabled")
        return {
            "workspace": desired.workspace_path,
            "github_mode": github_mode,
            "identity_configured": isinstance(identity, dict),
            "remote_configured": isinstance(remote, dict),
            "remote_name": remote.get("name") if isinstance(remote, dict) else None,
            "remote_protocol": remote.get("protocol") if isinstance(remote, dict) else None,
            "agent_mode": agent.get("mode", "none") if desired.config_version >= 2 else "none",
            "authentication": "external",
            "authentication_managed_by_manager": False,
        }

    def _unavailable_summary(self, desired: DesiredInstance, exc: ManagerError) -> dict[str, Any]:
        desired_label = (
            "Absent"
            if desired.lifecycle.deployment.value == "absent"
            else (
                "Present + Running"
                if desired.lifecycle.runtime.value == "running"
                else "Present + Stopped"
            )
        )
        return {
            "ok": True,
            "projection_version": PROJECTION_VERSION,
            "instance_id": desired.instance_id.value,
            "workspace_path": desired.workspace_path,
            "config_version": desired.config_version,
            "declaration_fingerprint": desired.fingerprint(),
            "desired_lifecycle": desired_label,
            "runtime": "Unknown",
            "health": "Unknown",
            "tunnel": "Unknown",
            "reconciliation": "Unknown",
            "recovery": "Unknown",
            "plan_fingerprint": None,
            "access": self._access_summary(desired),
            "git": self._git_summary(desired),
            "attention_reasons": [f"Projection unavailable: {exc.message}"],
            "projection_error": exc.to_dict()["error"],
        }

    def summary(self, instance_id: str) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        try:
            status = self.status_service.status_for(desired)
        except ManagerError as exc:
            return self._unavailable_summary(desired, exc)
        state = derive_operator_state(desired, status)
        return {
            "ok": True,
            "projection_version": PROJECTION_VERSION,
            "instance_id": desired.instance_id.value,
            "workspace_path": desired.workspace_path,
            "config_version": desired.config_version,
            "declaration_fingerprint": desired.fingerprint(),
            "desired_lifecycle": state["desired"],
            "runtime": state["runtime"],
            "health": state["health"],
            "tunnel": state["tunnel"],
            "reconciliation": state["reconciliation"],
            "recovery": state["recovery"],
            "plan_fingerprint": status.get("plan_fingerprint"),
            "access": self._access_summary(desired),
            "git": self._git_summary(desired),
            "attention_reasons": state["attention_reasons"],
        }

    def summaries(self) -> dict[str, Any]:
        items = [self.summary(desired.instance_id.value) for desired in self.registry.list()]
        return {
            "ok": True,
            "projection_version": PROJECTION_VERSION,
            "instances": items,
            "counts": {
                "instances": len(items),
                "attention": sum(bool(item.get("attention_reasons")) for item in items),
                "pending": sum(item.get("reconciliation") == "Pending" for item in items),
                "blocked": sum(item.get("reconciliation") == "Blocked" for item in items),
                "failed": sum(item.get("health") == "Failed" for item in items),
                "degraded": sum(item.get("health") == "Degraded" for item in items),
            },
        }

    def preview(self, desired: DesiredInstance) -> dict[str, Any]:
        bundle = self.generator.generate(desired)
        plan = self.planner.plan(desired)
        plan = project_recoverable_failed_first_apply(self.paths, desired, bundle, plan)
        conflicts = [item.to_dict() for item in plan.operations if item.operation is PlanOperation.CONFLICT]
        current_fingerprint: str | None = None
        try:
            current_fingerprint = self.registry.get(desired.instance_id.value).fingerprint()
        except ManagerError as exc:
            if exc.code is not ErrorCode.INSTANCE_NOT_FOUND:
                raise
        fingerprint = plan_fingerprint(
            plan,
            bundle,
            observed_inputs=observed_plan_inputs(plan, bundle, self.planner.observer),
        )
        return {
            "ok": True,
            "projection_version": PROJECTION_VERSION,
            "instance_id": desired.instance_id.value,
            "effective_declaration": desired.to_dict(),
            "candidate_fingerprint": desired.fingerprint(),
            "current_authoritative_fingerprint": current_fingerprint,
            "validation": {"valid": True, "errors": []},
            "collision_result": {"clear": not conflicts, "conflicts": conflicts},
            "warnings": list(plan.warnings),
            "errors": [item["reason"] for item in conflicts],
            "proposed_plan": plan.to_dict(),
            "semantic_operations": semantic_plan_operations(plan),
            "plan_fingerprint": fingerprint,
            "mutates_state": False,
        }

    def review_plan(self, instance_id: str) -> dict[str, Any]:
        """Return the authoritative reconciliation review contract for PM3."""

        desired = self.registry.get(instance_id)
        bundle = self.generator.generate(desired)
        plan = self.planner.plan(desired)
        plan = project_recoverable_failed_first_apply(self.paths, desired, bundle, plan)
        return {
            "ok": plan.valid,
            **plan.to_dict(),
            "plan_fingerprint": plan_fingerprint(
                plan,
                bundle,
                observed_inputs=observed_plan_inputs(plan, bundle, self.planner.observer),
            ),
            "semantic_operations": semantic_plan_operations(plan),
        }
