from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.domain import DeploymentTarget, DesiredInstance, LifecycleIntent, RuntimeTarget
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.host_apply import HostExecutionBridge, HostLifecycleWorker
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import PlanOperation, ReconciliationPlan
from workspace_mcp_manager.registry import InstanceRegistry

from tests.helpers import sample_instance


class FakeSystemd:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.active: set[str] = set()
        self.enabled: set[str] = set()

    def verify_generated_units(self, _bundle) -> None:
        self.calls.append(("verify", None))

    def daemon_reload(self) -> None:
        self.calls.append(("daemon-reload", None))

    def enable(self, unit: str) -> None:
        self.enabled.add(unit)
        self.calls.append(("enable", unit))

    def disable(self, unit: str) -> None:
        self.enabled.discard(unit)
        self.calls.append(("disable", unit))

    def start(self, unit: str) -> None:
        self.active.add(unit)
        self.calls.append(("start", unit))

    def stop(self, unit: str) -> None:
        self.active.discard(unit)
        self.calls.append(("stop", unit))

    def restart(self, unit: str) -> None:
        self.active.add(unit)
        self.calls.append(("restart", unit))

    def is_active(self, unit: str) -> bool:
        return unit in self.active

    def is_enabled(self, unit: str) -> bool:
        return unit in self.enabled


class StaticPlanner:
    def __init__(self, plans: list[ReconciliationPlan]) -> None:
        self.plans = plans
        self.index = 0

    def plan(self, _desired):
        plan = self.plans[min(self.index, len(self.plans) - 1)]
        self.index += 1
        return plan


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
    raw["mcp"]["binary"] = "/usr/bin/true"
    raw["mcp"]["port"] = 17655
    raw["mcp"]["external_roots"] = []
    raw["tunnel"]["binary"] = "/usr/bin/true"
    raw["tunnel"]["id"] = "tunnel_qual123"
    raw["tunnel"]["profile"] = "workspace-mcp-qual"
    raw["tunnel"]["health_port"] = 17171
    raw["tunnel"]["env_file"] = str(root / "runtime.env")
    raw["access"] = {"read_only": [], "read_write": []}
    raw["github"] = {"config_dir": None}
    raw["recovery"]["admission_guard_enabled"] = False
    (root / "workspace").mkdir()
    (root / "runtime.env").write_text("CONTROL_PLANE_API_KEY=test-only\n", encoding="utf-8")
    return DesiredInstance.from_dict(raw)


class HostApplyTests(unittest.TestCase):
    def test_lifecycle_remove_persists_absent_desired_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            worker = HostLifecycleWorker(paths, registry)
            with patch.object(worker.apply_service, "apply", return_value={"ok": True, "action": "remove"}):
                result = worker.run("remove", "qual")
            self.assertTrue(result["ok"])
            updated = registry.get("qual")
            self.assertEqual(updated.lifecycle.deployment, DeploymentTarget.ABSENT)
            self.assertEqual(updated.lifecycle.runtime, RuntimeTarget.STOPPED)

    def test_restart_sets_running_and_forces_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            desired = replace(
                desired,
                lifecycle=LifecycleIntent(DeploymentTarget.PRESENT, RuntimeTarget.STOPPED),
            )
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            worker = HostLifecycleWorker(paths, registry)
            with patch.object(worker.apply_service, "apply", return_value={"ok": True}) as apply:
                worker.run("restart", "qual")
            apply.assert_called_once_with("qual", force_restart=True, action="restart")
            self.assertEqual(registry.get("qual").lifecycle.runtime, RuntimeTarget.RUNNING)

    def test_bridge_uses_transient_user_systemd_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            paths.manager_executable.parent.mkdir(parents=True)
            paths.manager_executable.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(paths.manager_executable, 0o755)
            bridge = HostExecutionBridge(paths)
            payload = {"ok": True, "instance_id": "qual"}
            with patch("workspace_mcp_manager.host_apply._run") as run:
                from subprocess import CompletedProcess

                run.return_value = CompletedProcess([], 0, json.dumps(payload) + "\n", "")
                result = bridge.run_lifecycle("apply", "qual")
            self.assertEqual(result, payload)
            argv = run.call_args.args[0]
            self.assertEqual(argv[0:3], ["systemd-run", "--user", "--wait"])
            self.assertIn("--collect", argv)
            self.assertIn("--pipe", argv)
            self.assertIn(str(paths.manager_executable), argv)
            self.assertEqual(argv[-4:], ["_runtime", "lifecycle", "apply", "qual"])

    def test_invalid_plan_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            conflict = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=False,
                operations=(),
                warnings=(),
            )
            from workspace_mcp_manager.host_apply import HostApplyService
            from workspace_mcp_manager.errors import ManagerError

            service = HostApplyService(
                paths,
                registry,
                planner=StaticPlanner([conflict]),
                generator=ResourceGenerator(paths),
                systemd=FakeSystemd(),
            )
            with self.assertRaises(ManagerError):
                service.apply("qual")


if __name__ == "__main__":
    unittest.main()
