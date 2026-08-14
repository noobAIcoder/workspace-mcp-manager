from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.access import AccessManager, stale_applied_access_resources
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ManagerError
from workspace_mcp_manager.generation import ResourceGenerator
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


def empty_access_desired(root: Path) -> DesiredInstance:
    raw = sample_instance()
    raw["instance_id"] = "qual"
    raw["workspace_path"] = str(root / "workspace")
    raw["access"] = {"read_only": [], "read_write": []}
    raw["recovery"]["admission_guard_enabled"] = False
    return DesiredInstance.from_dict(raw)


class ExactObserver(HostResourceObserver):
    def __init__(self, statuses: dict[str, ObservationStatus] | None = None) -> None:
        self.statuses = statuses or {}

    def check_path(self, path: str, *, expected: str, executable: bool = False, writable: bool = False):
        return ResourceObservation(ObservationStatus.EXACT, "exact")

    def observe_resource(self, resource):  # type: ignore[override]
        status = self.statuses.get(resource.resource_id, ObservationStatus.EXACT)
        return ResourceObservation(status, status.value)

    def observe_unit(self, unit_name: str) -> UnitObservation:
        return UnitObservation(True, True, True)

    def listeners(self) -> set[str]:
        return set()

    def managed_identities(self) -> list[dict[str, str]]:
        return []


def write_applied(paths: ManagerPaths, desired: DesiredInstance) -> None:
    bundle = ResourceGenerator(paths).generate(desired)
    path = paths.state_root / "applied" / f"{desired.instance_id.value}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "instance_id": desired.instance_id.value,
                "desired_fingerprint": desired.fingerprint(),
                "transaction_id": "test",
                "deployment": "present",
                "runtime": "running",
                "owned_resources": [
                    {
                        "resource_id": resource.resource_id,
                        "kind": resource.kind.value,
                        "path": resource.path,
                        "fingerprint": resource.fingerprint(),
                    }
                    for resource in bundle.resources
                ],
            }
        ),
        encoding="utf-8",
    )


class AccessTests(unittest.TestCase):
    def test_add_list_and_normalized_remove(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(empty_access_desired(root))
            manager = AccessManager(paths, registry)
            added = manager.add("qual", mode="ro", alias="Shared-Docs", path="/srv/shared/docs")
            self.assertTrue(added["apply_required"])
            self.assertEqual(added["entries"][0]["mode"], "ro")
            self.assertEqual(manager.list("qual")["entries"][0]["alias"], "Shared-Docs")
            removed = manager.remove("qual", alias="shared_docs")
            self.assertTrue(removed["apply_required"])
            self.assertEqual(removed["removed"]["alias"], "Shared-Docs")
            self.assertEqual(registry.get("qual").access.read_only, ())

    def test_duplicate_alias_failure_preserves_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(empty_access_desired(root))
            manager = AccessManager(paths, registry)
            manager.add("qual", mode="ro", alias="shared-docs", path="/srv/shared/docs")
            before = registry.get("qual").fingerprint()
            with self.assertRaisesRegex(ManagerError, "aliases collide"):
                manager.add("qual", mode="rw", alias="shared_docs", path="/srv/shared/other")
            self.assertEqual(registry.get("qual").fingerprint(), before)

    def test_remove_unknown_alias_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(empty_access_desired(root))
            with self.assertRaisesRegex(ManagerError, "not configured"):
                AccessManager(paths, registry).remove("qual", alias="missing")

    def test_stale_access_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            desired = empty_access_desired(root)
            registry.create(desired)
            with self.assertRaisesRegex(ManagerError, "stale"):
                AccessManager(paths, registry).add(
                    "qual",
                    mode="ro",
                    alias="docs",
                    path="/srv/docs",
                    expected_current_fingerprint="0" * 64,
                )
            self.assertEqual(registry.get("qual").fingerprint(), desired.fingerprint())

    def test_access_update_is_atomic_and_preserves_single_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(empty_access_desired(root))
            manager = AccessManager(paths, registry)
            added = manager.add("qual", mode="ro", alias="docs", path="/srv/docs")
            updated = manager.update(
                "qual",
                existing_alias="docs",
                mode="rw",
                alias="models",
                path="/srv/models",
                expected_current_fingerprint=added["desired_fingerprint"],
            )
            self.assertEqual(updated["action"], "update")
            self.assertEqual(
                [(item["mode"], item["alias"], item["path"]) for item in updated["entries"]],
                [("rw", "models", "/srv/models")],
            )

    def test_access_change_plans_unit_update_restart_and_mountpoint_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            raw = empty_access_desired(root).to_dict()
            raw["access"]["read_only"] = [{"alias": "docs", "path": "/srv/shared/docs"}]
            desired = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            observer = ExactObserver(
                {
                    "mcp-unit": ObservationStatus.DIFFERENT,
                    "access-root": ObservationStatus.ABSENT,
                    "access-owner": ObservationStatus.ABSENT,
                    "access-ignore": ObservationStatus.ABSENT,
                    "access-ro-docs": ObservationStatus.ABSENT,
                }
            )
            plan = ReconciliationPlanner(paths, registry, observer=observer).plan(desired)
            self.assertTrue(plan.valid)
            self.assertTrue(any(i.operation is PlanOperation.UPDATE and i.resource_id == "mcp-unit" for i in plan.operations))
            self.assertTrue(any(i.operation is PlanOperation.RESTART and i.resource_id == "mcp-unit" for i in plan.operations))
            self.assertTrue(any(i.operation is PlanOperation.CREATE and i.resource_id == "access-root" for i in plan.operations))

    def test_removed_access_is_planned_from_applied_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            current = empty_access_desired(root)
            old_raw = current.to_dict()
            old_raw["access"]["read_only"] = [{"alias": "docs", "path": "/srv/shared/docs"}]
            old = DesiredInstance.from_dict(old_raw)
            write_applied(paths, old)
            expected = {"access-root", "access-owner", "access-ignore", "access-ro-docs"}
            stale = stale_applied_access_resources(paths, current, ResourceGenerator(paths).generate(current))
            self.assertEqual({resource.resource_id for resource in stale}, expected)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(current)
            plan = ReconciliationPlanner(paths, registry, observer=ExactObserver()).plan(current)
            self.assertEqual({i.resource_id for i in plan.operations if i.operation is PlanOperation.REMOVE}, expected)

    def test_corrupt_applied_access_fingerprint_is_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            current = empty_access_desired(root)
            old_raw = current.to_dict()
            old_raw["access"]["read_only"] = [{"alias": "docs", "path": "/srv/shared/docs"}]
            old = DesiredInstance.from_dict(old_raw)
            write_applied(paths, old)
            applied_path = paths.state_root / "applied/qual.json"
            payload = json.loads(applied_path.read_text(encoding="utf-8"))
            for entry in payload["owned_resources"]:
                if entry["resource_id"] == "access-ro-docs":
                    entry["fingerprint"] = "0" * 64
            applied_path.write_text(json.dumps(payload), encoding="utf-8")
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(current)
            plan = ReconciliationPlanner(paths, registry, observer=ExactObserver()).plan(current)
            self.assertFalse(plan.valid)
            self.assertTrue(any(i.resource_id == "access-ownership" for i in plan.operations))


if __name__ == "__main__":
    unittest.main()

