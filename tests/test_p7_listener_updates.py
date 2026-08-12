from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import (
    HostResourceObserver,
    ObservationStatus,
    PlanOperation,
    ReconciliationPlanner,
    ResourceObservation,
    UnitObservation,
)
from workspace_mcp_manager.registry import InstanceRegistry

from helpers import sample_instance


class ActiveOwnedMcpObserver(HostResourceObserver):
    def __init__(self, *, current_port: int, occupied_host: str, occupied_port: int) -> None:
        self.current_port = current_port
        self.occupied_host = occupied_host
        self.occupied_port = occupied_port

    def check_path(
        self,
        path: str,
        *,
        expected: str,
        executable: bool = False,
        writable: bool = False,
    ) -> ResourceObservation:
        return ResourceObservation(ObservationStatus.EXACT, "exact")

    def observe_resource(self, resource):  # type: ignore[override]
        if resource.resource_id == "mcp-unit":
            return ResourceObservation(ObservationStatus.DIFFERENT, "owned unit content changed")
        return ResourceObservation(ObservationStatus.EXACT, "exact")

    def observe_unit(self, unit_name: str) -> UnitObservation:
        if unit_name in {
            "coding-tools-mcp-sample.service",
            "tunnel-client-sample.service",
        }:
            return UnitObservation(True, True, True)
        return UnitObservation(True, False, False)

    def listeners(self) -> set[str]:
        return {f"{self.occupied_host}:{self.occupied_port}"}

    def managed_identities(self) -> list[dict[str, str]]:
        return [
            {
                "unit_name": "coding-tools-mcp-sample.service",
                "instance_id": "sample",
                "workspace_path": "/srv/workspaces/sample",
                "mcp_host": "127.0.0.1",
                "mcp_port": str(self.current_port),
            }
        ]


class P7ListenerUpdateRegressionTests(unittest.TestCase):
    def _paths(self, root: Path) -> ManagerPaths:
        return ManagerPaths(
            account_home=root,
            config_root=root / ".config/workspace-mcp-manager",
            registry_dir=root / "registry",
            state_root=root / ".local/state/workspace-mcp-manager",
            user_unit_dir=root / ".config/systemd/user",
            tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
            manager_executable=root / ".local/bin/workspace-mcp-manager",
        )

    def _desired(self, *, port: int) -> DesiredInstance:
        raw = sample_instance()
        raw["recovery"]["admission_guard_enabled"] = False
        raw["mcp"]["port"] = port
        return DesiredInstance.from_dict(raw)

    def test_managed_identity_extracts_current_mcp_host(self) -> None:
        def fake_run(argv, **kwargs):
            command = argv[2]
            if command == "list-unit-files":
                stdout = "coding-tools-mcp-sample.service enabled\n"
            elif command == "cat":
                stdout = (
                    "# workspace-mcp-manager-instance=sample\n"
                    "# workspace-mcp-manager-port=8765\n"
                )
            elif command == "show":
                stdout = (
                    "WorkingDirectory=/srv/workspaces/sample\n"
                    "ExecStart={ path=/opt/bin/coding-tools-mcp ; "
                    "argv[]=/opt/bin/coding-tools-mcp --workspace /srv/workspaces/sample "
                    "--host 127.0.0.1 --port 8765 --permission-mode trusted ; }\n"
                )
            else:
                raise AssertionError(argv)
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        with patch("workspace_mcp_manager.planning.subprocess.run", side_effect=fake_run):
            records = HostResourceObserver().managed_identities()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["instance_id"], "sample")
        self.assertEqual(records[0]["mcp_host"], "127.0.0.1")
        self.assertEqual(records[0]["mcp_port"], "8765")

    def test_active_owned_listener_allows_non_endpoint_mcp_unit_update(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = self._desired(port=8765)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            observer = ActiveOwnedMcpObserver(
                current_port=8765,
                occupied_host="127.0.0.1",
                occupied_port=8765,
            )

            plan = ReconciliationPlanner(paths, registry, observer=observer).plan(desired)

            self.assertTrue(plan.valid)
            self.assertTrue(
                any(
                    item.operation is PlanOperation.UPDATE and item.resource_id == "mcp-unit"
                    for item in plan.operations
                )
            )
            self.assertTrue(
                any(
                    item.operation is PlanOperation.RESTART and item.resource_id == "mcp-unit"
                    for item in plan.operations
                )
            )

    def test_active_owned_listener_does_not_mask_changed_endpoint_collision(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = self._desired(port=8766)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            observer = ActiveOwnedMcpObserver(
                current_port=8765,
                occupied_host="127.0.0.1",
                occupied_port=8766,
            )

            plan = ReconciliationPlanner(paths, registry, observer=observer).plan(desired)

            self.assertFalse(plan.valid)
            self.assertTrue(
                any(
                    item.operation is PlanOperation.CONFLICT
                    and item.resource_id == "mcp-unit"
                    and "MCP listener is already occupied" in item.reason
                    for item in plan.operations
                )
            )


if __name__ == "__main__":
    unittest.main()
