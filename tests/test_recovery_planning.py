from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import PlanItem, PlanOperation, ReconciliationPlan
from workspace_mcp_manager.recovery_planning import project_recoverable_failed_first_apply

from tests.helpers import sample_instance


def paths_for(root: Path) -> ManagerPaths:
    return ManagerPaths(
        account_home=root,
        config_root=root / ".config/workspace-mcp-manager",
        registry_dir=root / ".config/workspace-mcp-manager/instances",
        state_root=root / ".local/state/workspace-mcp-manager",
        user_unit_dir=root / ".config/systemd/user",
        tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
        manager_executable=root / ".local/bin/workspace-mcp-manager",
    )


def desired_for(root: Path) -> DesiredInstance:
    raw = sample_instance()
    raw["instance_id"] = "qual"
    raw["workspace_path"] = str(root / "workspace")
    raw["mcp"]["external_roots"] = []
    raw["tunnel"]["profile"] = "workspace-mcp-manager-qual"
    raw["access"] = {"read_only": [], "read_write": []}
    raw["github"] = {"config_dir": None}
    raw["recovery"]["admission_guard_enabled"] = False
    return DesiredInstance.from_dict(raw)


def failed_plan(desired: DesiredInstance, bundle) -> ReconciliationPlan:
    by_id = {resource.resource_id: resource for resource in bundle.resources}
    state_dir = by_id["state-dir"].path
    owner_path = by_id["state-owner"].path
    return ReconciliationPlan(
        instance_id=desired.instance_id.value,
        desired_fingerprint=desired.fingerprint(),
        valid=False,
        operations=(
            PlanItem(
                PlanOperation.CONFLICT,
                state_dir,
                f"ownership marker is missing: {owner_path}",
                "state-dir",
            ),
            PlanItem(
                PlanOperation.CREATE,
                owner_path,
                "managed resource is absent",
                "state-owner",
            ),
            PlanItem(
                PlanOperation.CREATE,
                by_id["mcp-unit"].path,
                "managed resource is absent",
                "mcp-unit",
            ),
        ),
        warnings=(),
    )


def seed_failed_residue(root: Path, paths: ManagerPaths, bundle, *, unknown: bool = False) -> tuple[Path, Path]:
    by_id = {resource.resource_id: resource for resource in bundle.resources}
    state_dir = Path(by_id["state-dir"].path)
    owner_path = Path(by_id["state-owner"].path)
    state_dir.mkdir(parents=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    (state_dir / "mcp.log").write_text("", encoding="utf-8")
    (state_dir / "tunnel-supervisor.log").write_text("failed startup\n", encoding="utf-8")
    if unknown:
        (state_dir / "unexpected.txt").write_text("preserve\n", encoding="utf-8")

    transaction_dir = paths.state_root / "transactions" / "qual"
    transaction_dir.mkdir(parents=True)
    (transaction_dir / "failed.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "instance_id": "qual",
                "executed": [
                    {"operation": "CREATE", "target": str(state_dir)},
                    {"operation": "CREATE", "target": str(owner_path)},
                ],
            }
        ),
        encoding="utf-8",
    )
    return state_dir, owner_path


class FailedFirstApplyPlanRecoveryTests(unittest.TestCase):
    def test_journal_proven_residue_projects_to_valid_read_only_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            bundle = ResourceGenerator(paths).generate(desired)
            state_dir, owner_path = seed_failed_residue(root, paths, bundle)
            plan = failed_plan(desired, bundle)

            projected = project_recoverable_failed_first_apply(paths, desired, bundle, plan)

            self.assertTrue(projected.valid)
            state_item = next(item for item in projected.operations if item.resource_id == "state-dir")
            self.assertEqual(state_item.operation, PlanOperation.NOOP)
            self.assertTrue(any(item.resource_id == "state-owner" and item.operation is PlanOperation.CREATE for item in projected.operations))
            self.assertTrue(any("failed-first-apply residue" in warning for warning in projected.warnings))
            self.assertTrue(state_dir.is_dir())
            self.assertFalse(owner_path.exists(), "read-only plan projection must not restore ownership itself")
            self.assertTrue((state_dir / "mcp.log").is_file())

    def test_unknown_residual_content_stays_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            bundle = ResourceGenerator(paths).generate(desired)
            seed_failed_residue(root, paths, bundle, unknown=True)
            plan = failed_plan(desired, bundle)

            projected = project_recoverable_failed_first_apply(paths, desired, bundle, plan)

            self.assertFalse(projected.valid)
            self.assertTrue(any(item.operation is PlanOperation.CONFLICT for item in projected.operations))

    def test_missing_failed_transaction_evidence_stays_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            bundle = ResourceGenerator(paths).generate(desired)
            by_id = {resource.resource_id: resource for resource in bundle.resources}
            state_dir = Path(by_id["state-dir"].path)
            state_dir.mkdir(parents=True, mode=0o700)
            os.chmod(state_dir, 0o700)
            (state_dir / "mcp.log").write_text("", encoding="utf-8")
            plan = failed_plan(desired, bundle)

            projected = project_recoverable_failed_first_apply(paths, desired, bundle, plan)

            self.assertFalse(projected.valid)

    def test_existing_applied_state_disables_first_apply_recovery_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            bundle = ResourceGenerator(paths).generate(desired)
            seed_failed_residue(root, paths, bundle)
            applied_dir = paths.state_root / "applied"
            applied_dir.mkdir(parents=True)
            (applied_dir / "qual.json").write_text("{}\n", encoding="utf-8")
            plan = failed_plan(desired, bundle)

            projected = project_recoverable_failed_first_apply(paths, desired, bundle, plan)

            self.assertFalse(projected.valid)


if __name__ == "__main__":
    unittest.main()
