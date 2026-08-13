from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.generation import GeneratedResource, ResourceGenerator, ResourceKind
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


class FakeObserver(HostResourceObserver):
    def __init__(
        self,
        statuses: dict[str, ObservationStatus] | None = None,
        identities: list[dict[str, str]] | None = None,
        units: dict[str, UnitObservation] | None = None,
    ) -> None:
        self.statuses = statuses or {}
        self.identities = identities or []
        self.units = units or {}

    def observe_resource(self, resource):  # type: ignore[override]
        status = self.statuses.get(resource.resource_id, ObservationStatus.ABSENT)
        return ResourceObservation(status, status.value)

    def check_path(
        self,
        path: str,
        *,
        expected: str,
        executable: bool = False,
        writable: bool = False,
    ) -> ResourceObservation:
        return ResourceObservation(ObservationStatus.EXACT, "exact")

    def observe_unit(self, unit_name: str) -> UnitObservation:
        return self.units.get(unit_name, UnitObservation(False, False, False))

    def listeners(self) -> set[str]:
        return set()

    def managed_identities(self) -> list[dict[str, str]]:
        return self.identities


class PlanningTests(unittest.TestCase):
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

    def test_absent_resources_plan_create_enable_start(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            raw = sample_instance()
            raw["recovery"]["admission_guard_enabled"] = False
            desired = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            planner = ReconciliationPlanner(
                paths,
                registry,
                generator=ResourceGenerator(paths),
                observer=FakeObserver(),
            )
            plan = planner.plan(desired)
            operations = [item.operation for item in plan.operations]
            self.assertTrue(plan.valid)
            self.assertIn(PlanOperation.CREATE, operations)
            self.assertIn(PlanOperation.ENABLE, operations)
            self.assertIn(PlanOperation.START, operations)

    def test_admission_guard_runtime_is_available_in_p11(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = DesiredInstance.from_dict(sample_instance())
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            plan = ReconciliationPlanner(paths, registry, observer=FakeObserver()).plan(desired)
            self.assertTrue(plan.valid)
            self.assertFalse(any(item.resource_id == "admission-guard-runtime" for item in plan.operations))

    def test_disabling_applied_guard_plans_stop_disable_and_remove(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            enabled = DesiredInstance.from_dict(sample_instance())
            generator = ResourceGenerator(paths)
            guard_resources = [
                resource
                for resource in generator.generate(enabled).resources
                if resource.resource_id in {"admission-guard-unit", "admission-guard-timer"}
            ]
            paths.user_unit_dir.mkdir(parents=True, exist_ok=True)
            for resource in guard_resources:
                Path(resource.path).write_text(resource.content or "", encoding="utf-8")
                Path(resource.path).chmod(resource.mode)
            applied = paths.state_root / "applied/sample.json"
            applied.parent.mkdir(parents=True, exist_ok=True)
            applied.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "instance_id": "sample",
                        "owned_resources": [
                            {
                                "resource_id": resource.resource_id,
                                "kind": resource.kind.value,
                                "path": resource.path,
                                "fingerprint": resource.fingerprint(),
                            }
                            for resource in guard_resources
                        ],
                    }
                ),
                encoding="utf-8",
            )

            raw = sample_instance()
            raw["recovery"]["admission_guard_enabled"] = False
            disabled = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(disabled)
            statuses = {resource.resource_id: ObservationStatus.EXACT for resource in guard_resources}
            units = {
                resource.unit_name: UnitObservation(True, True, True)
                for resource in guard_resources
                if resource.unit_name
            }
            plan = ReconciliationPlanner(
                paths,
                registry,
                generator=generator,
                observer=FakeObserver(statuses=statuses, units=units),
            ).plan(disabled)
            self.assertTrue(plan.valid)
            for resource in guard_resources:
                operations = {
                    item.operation
                    for item in plan.operations
                    if item.resource_id == resource.resource_id
                }
                self.assertEqual(
                    operations,
                    {PlanOperation.STOP, PlanOperation.DISABLE, PlanOperation.REMOVE},
                )

    def test_modified_applied_guard_fails_closed_on_disable(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            enabled = DesiredInstance.from_dict(sample_instance())
            generator = ResourceGenerator(paths)
            resource = next(
                item
                for item in generator.generate(enabled).resources
                if item.resource_id == "admission-guard-unit"
            )
            path = Path(resource.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((resource.content or "") + "# modified\n", encoding="utf-8")
            path.chmod(resource.mode)
            applied = paths.state_root / "applied/sample.json"
            applied.parent.mkdir(parents=True, exist_ok=True)
            applied.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "instance_id": "sample",
                        "owned_resources": [
                            {
                                "resource_id": resource.resource_id,
                                "kind": resource.kind.value,
                                "path": resource.path,
                                "fingerprint": resource.fingerprint(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            raw = sample_instance()
            raw["recovery"]["admission_guard_enabled"] = False
            disabled = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(disabled)
            plan = ReconciliationPlanner(paths, registry, generator=generator, observer=FakeObserver()).plan(disabled)
            self.assertFalse(plan.valid)
            self.assertTrue(
                any(item.resource_id == "admission-guard-ownership" for item in plan.operations)
            )

    def test_foreign_resource_is_conflict(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = DesiredInstance.from_dict(sample_instance())
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            planner = ReconciliationPlanner(
                paths,
                registry,
                observer=FakeObserver({"mcp-unit": ObservationStatus.FOREIGN}),
            )
            plan = planner.plan(desired)
            self.assertFalse(plan.valid)
            self.assertTrue(any(item.operation is PlanOperation.CONFLICT for item in plan.operations))

    def test_registry_endpoint_collision_is_conflict(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            registry = InstanceRegistry(paths.registry_dir)
            first_raw = sample_instance()
            first = DesiredInstance.from_dict(first_raw)
            registry.create(first)
            second_raw = sample_instance()
            second_raw["instance_id"] = "second"
            second_raw["workspace_path"] = "/srv/workspaces/second"
            second_raw["tunnel"]["id"] = "tunnel_second"
            second_raw["tunnel"]["profile"] = "workspace-mcp-second"
            second = DesiredInstance.from_dict(second_raw)
            registry.create(second)
            planner = ReconciliationPlanner(paths, registry, observer=FakeObserver())
            plan = planner.plan(second)
            self.assertFalse(plan.valid)
            self.assertTrue(any("MCP endpoint collides" in item.reason for item in plan.operations))

    def test_unverified_resource_is_conflict_not_false_absence(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = DesiredInstance.from_dict(sample_instance())
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            planner = ReconciliationPlanner(
                paths,
                registry,
                observer=FakeObserver({"tunnel-profile": ObservationStatus.UNVERIFIED}),
            )
            plan = planner.plan(desired)
            self.assertFalse(plan.valid)
            conflicts = [item for item in plan.operations if item.operation is PlanOperation.CONFLICT]
            self.assertTrue(any(item.resource_id == "tunnel-profile" for item in conflicts))

    def test_failed_host_precondition_invalidates_plan(self) -> None:
        class MissingManagerObserver(FakeObserver):
            def check_path(
                self,
                path: str,
                *,
                expected: str,
                executable: bool = False,
                writable: bool = False,
            ) -> ResourceObservation:
                if path.endswith("workspace-mcp-manager"):
                    return ResourceObservation(ObservationStatus.ABSENT, "path does not exist")
                return super().check_path(
                    path,
                    expected=expected,
                    executable=executable,
                    writable=writable,
                )

        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = DesiredInstance.from_dict(sample_instance())
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            plan = ReconciliationPlanner(
                paths,
                registry,
                observer=MissingManagerObserver(),
            ).plan(desired)
            self.assertFalse(plan.valid)
            self.assertTrue(
                any(
                    "manager runtime executable precondition failed" in item.reason
                    for item in plan.operations
                )
            )

    def test_existing_legacy_tunnel_id_collision_is_conflict(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = DesiredInstance.from_dict(sample_instance())
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            observer = FakeObserver(
                identities=[
                    {
                        "unit_name": "tunnel-client-legacy.service",
                        "tunnel_id": desired.tunnel.id,
                    }
                ]
            )
            plan = ReconciliationPlanner(paths, registry, observer=observer).plan(desired)
            self.assertFalse(plan.valid)
            self.assertTrue(
                any(
                    "tunnel ID collides with existing managed unit tunnel-client-legacy.service" in item.reason
                    for item in plan.operations
                )
            )

    def test_incomplete_existing_tunnel_identity_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            desired = DesiredInstance.from_dict(sample_instance())
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            observer = FakeObserver(identities=[{"unit_name": "tunnel-client-legacy.service"}])
            plan = ReconciliationPlanner(paths, registry, observer=observer).plan(desired)
            self.assertFalse(plan.valid)
            self.assertTrue(
                any(
                    "collision-sensitive identity metadata cannot be fully verified" in item.reason
                    for item in plan.operations
                )
            )

    def test_owned_directory_requires_positive_marker_evidence(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "owned"
            directory.mkdir(mode=0o700)
            resource = GeneratedResource(
                "owned-dir",
                ResourceKind.DIRECTORY,
                str(directory),
                0o700,
                ownership_token="workspace-mcp-manager-instance=sample",
                ownership_marker_path=str(directory / ".owner"),
            )
            observation = HostResourceObserver().observe_resource(resource)
            self.assertEqual(observation.status, ObservationStatus.FOREIGN)
            self.assertIn("ownership marker is missing", observation.detail)

    def test_absent_plan_stops_before_removal_and_retains_shared_directory(self) -> None:
        class ActiveOwnedObserver(FakeObserver):
            def observe_resource(self, resource):  # type: ignore[override]
                return ResourceObservation(ObservationStatus.EXACT, "exact")

            def observe_unit(self, unit_name: str) -> UnitObservation:
                return UnitObservation(True, True, True)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            raw = sample_instance()
            raw["lifecycle"] = {"deployment": "absent", "runtime": "stopped"}
            desired = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            plan = ReconciliationPlanner(paths, registry, observer=ActiveOwnedObserver()).plan(desired)
            self.assertTrue(plan.valid)
            operations = list(plan.operations)
            first_remove = min(
                index for index, item in enumerate(operations) if item.operation is PlanOperation.REMOVE
            )
            last_service_change = max(
                index
                for index, item in enumerate(operations)
                if item.operation in {PlanOperation.STOP, PlanOperation.DISABLE}
            )
            self.assertLess(last_service_change, first_remove)
            tunnel_stop = next(
                index
                for index, item in enumerate(operations)
                if item.operation is PlanOperation.STOP and item.target == "tunnel-client-sample.service"
            )
            mcp_stop = next(
                index
                for index, item in enumerate(operations)
                if item.operation is PlanOperation.STOP and item.target == "coding-tools-mcp-sample.service"
            )
            self.assertLess(tunnel_stop, mcp_stop)
            profile_dir = str(paths.tunnel_profile_dir)
            self.assertFalse(
                any(item.operation is PlanOperation.REMOVE and item.target == profile_dir for item in operations)
            )
            launcher = str(paths.account_home / ".local/bin/sample-mcp")
            self.assertTrue(
                any(item.operation is PlanOperation.REMOVE and item.target == launcher for item in operations)
            )


if __name__ == "__main__":
    unittest.main()
