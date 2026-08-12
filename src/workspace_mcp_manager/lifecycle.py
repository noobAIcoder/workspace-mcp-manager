from __future__ import annotations

from pathlib import Path
from typing import Any

from .domain import DeploymentTarget, DesiredInstance
from .generation import GeneratedBundle
from .host_apply import (
    HostApplyService as BaseHostApplyService,
    HostLifecycleWorker as BaseHostLifecycleWorker,
    _RollbackState,
)
from .paths import ManagerPaths
from .planning import PlanOperation, ReconciliationPlan
from .registry import InstanceRegistry


class HostApplyService(BaseHostApplyService):
    """P6 lifecycle reconciler with runtime-only stop support.

    The base P6 reconciler handles STOP while removing an absent deployment but
    historically skipped STOP operations for a deployment that remains present
    with ``runtime=stopped``. Lifecycle commands use this specialization so
    runtime-only stops execute inside the same locked, freshly planned,
    journaled apply transaction as every other P6 mutation.
    """

    def _execute(
        self,
        plan: ReconciliationPlan,
        bundle: GeneratedBundle,
        desired: DesiredInstance,
        rollback: _RollbackState,
        *,
        force_restart: bool,
        journal: dict[str, Any],
        journal_path: Path,
    ) -> None:
        if desired.lifecycle.deployment is DeploymentTarget.PRESENT:
            for item in self._ordered_unit_ops(
                plan.operations,
                {PlanOperation.STOP},
                start_order=False,
            ):
                self._attempt(journal, journal_path, item.operation.value, item.target)
                self.systemd.stop(item.target)
                self._record(journal, journal_path, item.operation.value, item.target)

        super()._execute(
            plan,
            bundle,
            desired,
            rollback,
            force_restart=force_restart,
            journal=journal,
            journal_path=journal_path,
        )


class HostLifecycleWorker(BaseHostLifecycleWorker):
    """Lifecycle worker wired to the complete P6 lifecycle reconciler."""

    def __init__(self, paths: ManagerPaths, registry: InstanceRegistry) -> None:
        self.paths = paths
        self.registry = registry
        self.apply_service = HostApplyService(paths, registry)
