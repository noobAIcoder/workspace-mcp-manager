from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.lifecycle import HostApplyService, HostLifecycleWorker
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import PlanItem, PlanOperation, ReconciliationPlan
from workspace_mcp_manager.registry import InstanceRegistry

from tests.helpers import sample_instance


class FakeSystemd:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.active: set[str] = set()

    def stop(self, unit: str) -> None:
        self.active.discard(unit)
        self.calls.append(("stop", unit))

    def is_active(self, unit: str) -> bool:
        return unit in self.active

    def daemon_reload(self) -> None:
        self.calls.append(("daemon-reload", None))

    def enable(self, unit: str) -> None:
        self.calls.append(("enable", unit))

    def start(self, unit: str) -> None:
        self.active.add(unit)
        self.calls.append(("start", unit))

    def restart(self, unit: str) -> None:
        self.active.add(unit)
        self.calls.append(("restart", unit))


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


def stopped_desired() -> DesiredInstance:
    raw = sample_instance()
    raw["lifecycle"] = {"deployment": "present", "runtime": "stopped"}
    raw["access"] = {"read_only": [], "read_write": []}
    raw["github"] = {"config_dir": None}
    raw["recovery"]["admission_guard_enabled"] = False
    return DesiredInstance.from_dict(raw)


class LifecycleStopTests(unittest.TestCase):
    def test_present_stopped_executes_stop_tunnel_before_mcp_inside_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = stopped_desired()
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            generator = ResourceGenerator(paths)
            bundle = generator.generate(desired)
            systemd = FakeSystemd()
            systemd.active.update(
                {
                    "coding-tools-mcp-sample.service",
                    "tunnel-client-sample.service",
                }
            )
            service = HostApplyService(paths, registry, generator=generator, systemd=systemd)
            plan = ReconciliationPlan(
                instance_id="sample",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(
                    PlanItem(
                        PlanOperation.STOP,
                        "coding-tools-mcp-sample.service",
                        "runtime target is stopped",
                        "mcp-unit",
                    ),
                    PlanItem(
                        PlanOperation.STOP,
                        "tunnel-client-sample.service",
                        "runtime target is stopped",
                        "tunnel-unit",
                    ),
                ),
                warnings=(),
            )
            journal_path = service._journal_path("sample", "present-stop-test")
            journal = {"status": "running", "attempted": [], "executed": []}
            service._write_journal(journal_path, journal)

            from workspace_mcp_manager.host_apply import _RollbackState

            service._execute(
                plan,
                bundle,
                desired,
                _RollbackState(),
                force_restart=False,
                journal=journal,
                journal_path=journal_path,
            )

            stop_calls = [call for call in systemd.calls if call[0] == "stop"]
            self.assertEqual(
                stop_calls,
                [
                    ("stop", "tunnel-client-sample.service"),
                    ("stop", "coding-tools-mcp-sample.service"),
                ],
            )
            self.assertEqual(systemd.active, set())
            persisted = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["target"] for item in persisted["attempted"]],
                ["tunnel-client-sample.service", "coding-tools-mcp-sample.service"],
            )
            self.assertEqual(
                [item["target"] for item in persisted["executed"]],
                ["tunnel-client-sample.service", "coding-tools-mcp-sample.service"],
            )

    def test_lifecycle_worker_uses_complete_apply_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(stopped_desired())
            worker = HostLifecycleWorker(paths, registry)
            self.assertIsInstance(worker.apply_service, HostApplyService)


if __name__ == "__main__":
    unittest.main()
