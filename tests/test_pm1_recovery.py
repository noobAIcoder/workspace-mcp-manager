from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.admission_recovery import stale_applied_development_resources
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import (
    ObservationStatus,
    PlanOperation,
    ReconciliationPlanner,
    ResourceObservation,
    UnitObservation,
)
from workspace_mcp_manager.registry import InstanceRegistry

from helpers import sample_v2_instance


class ExactActiveObserver:
    def observe_resource(self, resource):
        return ResourceObservation(ObservationStatus.EXACT, "exact")

    def observe_unit(self, unit_name: str):
        return UnitObservation(loaded=True, enabled=True, active=True)


class Pm1RecoveryTests(unittest.TestCase):
    def _paths(self, root: Path) -> ManagerPaths:
        return ManagerPaths(
            account_home=root,
            config_root=root / ".config/workspace-mcp-manager",
            registry_dir=root / ".config/workspace-mcp-manager/instances",
            state_root=root / ".local/state/workspace-mcp-manager",
            user_unit_dir=root / ".config/systemd/user",
            tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
            manager_executable=root / ".local/bin/workspace-mcp-manager",
        )

    def _desired(self, root: Path) -> DesiredInstance:
        raw = sample_v2_instance()
        raw["github"]["config_dir"] = str(
            root / ".config/workspace-mcp-manager/github/sample"
        )
        raw["git"]["remote"]["protocol"] = "https-gh"
        return DesiredInstance.from_dict(raw)

    def test_obsolete_helper_and_agent_reconstruct_from_applied_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            initial = self._desired(root)
            initial_bundle = ResourceGenerator(paths).generate(initial)
            owned = []
            for resource in initial_bundle.resources:
                if resource.resource_id not in {"github-cli-helper", "ssh-agent-unit"}:
                    continue
                path = Path(resource.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(resource.content or "", encoding="utf-8")
                os.chmod(path, resource.mode)
                owned.append(
                    {
                        "resource_id": resource.resource_id,
                        "kind": resource.kind.value,
                        "path": resource.path,
                        "fingerprint": resource.fingerprint(),
                    }
                )
            applied = paths.state_root / "applied" / "sample.json"
            applied.parent.mkdir(parents=True)
            applied.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "instance_id": "sample",
                        "owned_resources": owned,
                    }
                ),
                encoding="utf-8",
            )

            raw = initial.to_dict()
            raw["git"]["remote"] = None
            raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
            current = DesiredInstance.from_dict(raw)
            current_bundle = ResourceGenerator(paths).generate(current)
            stale = stale_applied_development_resources(paths, current, current_bundle)
            self.assertEqual({resource.resource_id for resource in stale}, {"github-cli-helper", "ssh-agent-unit"})

    def test_modified_obsolete_resource_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            initial = self._desired(root)
            initial_bundle = ResourceGenerator(paths).generate(initial)
            helper = next(
                resource for resource in initial_bundle.resources if resource.resource_id == "github-cli-helper"
            )
            helper_path = Path(helper.path)
            helper_path.parent.mkdir(parents=True, exist_ok=True)
            helper_path.write_text((helper.content or "") + "# modified\n", encoding="utf-8")
            os.chmod(helper_path, helper.mode)
            applied = paths.state_root / "applied" / "sample.json"
            applied.parent.mkdir(parents=True)
            applied.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "instance_id": "sample",
                        "owned_resources": [
                            {
                                "resource_id": helper.resource_id,
                                "kind": helper.kind.value,
                                "path": helper.path,
                                "fingerprint": helper.fingerprint(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            raw = initial.to_dict()
            raw["git"]["remote"] = None
            current = DesiredInstance.from_dict(raw)
            with self.assertRaises(ManagerError) as caught:
                stale_applied_development_resources(paths, current, ResourceGenerator(paths).generate(current))
            self.assertEqual(caught.exception.code, ErrorCode.RECONCILIATION_CONFLICT)

    def test_obsolete_agent_plans_stop_disable_remove(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            initial = self._desired(root)
            initial_bundle = ResourceGenerator(paths).generate(initial)
            owned = []
            for resource in initial_bundle.resources:
                if resource.resource_id not in {"github-cli-helper", "ssh-agent-unit"}:
                    continue
                path = Path(resource.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(resource.content or "", encoding="utf-8")
                os.chmod(path, resource.mode)
                owned.append(
                    {
                        "resource_id": resource.resource_id,
                        "kind": resource.kind.value,
                        "path": resource.path,
                        "fingerprint": resource.fingerprint(),
                    }
                )
            applied = paths.state_root / "applied" / "sample.json"
            applied.parent.mkdir(parents=True)
            applied.write_text(
                json.dumps({"schema_version": 1, "instance_id": "sample", "owned_resources": owned}),
                encoding="utf-8",
            )
            raw = initial.to_dict()
            raw["git"]["remote"] = None
            raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
            current = DesiredInstance.from_dict(raw)
            planner = ReconciliationPlanner(
                paths,
                InstanceRegistry(paths.registry_dir),
                observer=ExactActiveObserver(),
            )
            operations = planner._stale_development_operations(
                current,
                ResourceGenerator(paths).generate(current),
            )
            agent_ops = [
                item.operation
                for item in operations
                if item.resource_id == "ssh-agent-unit"
            ]
            self.assertEqual(
                agent_ops,
                [PlanOperation.STOP, PlanOperation.DISABLE, PlanOperation.REMOVE],
            )


if __name__ == "__main__":
    unittest.main()
