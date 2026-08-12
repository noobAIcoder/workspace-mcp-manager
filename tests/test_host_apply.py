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
from workspace_mcp_manager.host_apply import (
    HostApplyService,
    HostExecutionBridge,
    HostLifecycleWorker,
    SystemdUserAdapter,
    _RollbackState,
)
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import PlanOperation, ReconciliationPlan
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.errors import ManagerError

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

    def test_bridge_registry_operations_use_host_worker_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            paths.manager_executable.parent.mkdir(parents=True)
            paths.manager_executable.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(paths.manager_executable, 0o755)
            desired = desired_for(root)
            bridge = HostExecutionBridge(paths)
            payload = {"ok": True, "instance_id": "qual"}
            with patch("workspace_mcp_manager.host_apply._run") as run:
                from subprocess import CompletedProcess

                run.return_value = CompletedProcess([], 0, json.dumps(payload) + "\n", "")
                result = bridge.run_registry_write("create", desired)
            self.assertEqual(result, payload)
            argv = run.call_args.args[0]
            self.assertEqual(argv[-3:], ["_runtime", "registry-write", "create"])
            self.assertEqual(run.call_args.kwargs["input_text"], desired.canonical_json() + "\n")
            self.assertNotIn(desired.canonical_json(), " ".join(argv))

    def test_bridge_registry_queries_use_host_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            paths.manager_executable.parent.mkdir(parents=True)
            paths.manager_executable.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(paths.manager_executable, 0o755)
            bridge = HostExecutionBridge(paths)
            with patch.object(bridge, "_run_json", return_value={"ok": True}) as run_json:
                bridge.run_list()
                run_json.assert_called_with(["_runtime", "list"], unit_fragment="list")
                bridge.run_show("qual")
                run_json.assert_called_with(["_runtime", "show", "qual"], unit_fragment="show-qual")
                bridge.run_render("qual", include_content=True)
                run_json.assert_called_with(
                    ["_runtime", "render", "qual", "--include-content"],
                    unit_fragment="render-qual",
                )

    def test_bridge_status_and_logs_use_host_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            paths.manager_executable.parent.mkdir(parents=True)
            paths.manager_executable.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(paths.manager_executable, 0o755)
            bridge = HostExecutionBridge(paths)
            with patch.object(bridge, "_run_json", return_value={"ok": True}) as run_json:
                bridge.run_status("qual")
                run_json.assert_called_with(["_runtime", "status", "qual"], unit_fragment="status-qual")
                bridge.run_logs("qual", lines=77)
                run_json.assert_called_with(["_runtime", "logs", "qual", "--lines", "77"], unit_fragment="logs-qual")

    def test_systemd_start_failure_contains_unit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = SystemdUserAdapter(state_root=Path(directory))
            from subprocess import CompletedProcess
            failure = CompletedProcess([], 1, "", "start failed")
            with patch("workspace_mcp_manager.host_apply._run", return_value=failure), \
                 patch.object(adapter, "unit_snapshot", return_value={"ActiveState": "failed"}), \
                 patch.object(adapter, "journal_tail", return_value="journal evidence"):
                with self.assertRaises(ManagerError) as caught:
                    adapter.start("tunnel-client-qual.service")
            details = caught.exception.details
            self.assertEqual(details["unit"], "tunnel-client-qual.service")
            self.assertEqual(details["unit_status"]["ActiveState"], "failed")
            self.assertIn("journal evidence", details["journal"])

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

    def test_failed_first_apply_residue_is_recovered_with_transaction_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            generator = ResourceGenerator(paths)
            bundle = generator.generate(desired)
            by_id = {resource.resource_id: resource for resource in bundle.resources}
            state_dir = Path(by_id["state-dir"].path)
            owner_path = Path(by_id["state-owner"].path)
            state_dir.mkdir(parents=True, mode=0o700)
            (state_dir / "mcp.log").write_text("", encoding="utf-8")
            (state_dir / "tunnel-supervisor.log").write_text("manager log\n", encoding="utf-8")

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

            service = HostApplyService(paths, registry, generator=generator, systemd=FakeSystemd())
            result = service._recover_failed_first_apply_state("qual", bundle)

            self.assertIsNotNone(result)
            self.assertTrue(result["recovered"])
            self.assertFalse(state_dir.exists())
            self.assertTrue(Path(result["recovery_journal"]).is_file())

    def test_failed_first_apply_residue_with_unknown_content_is_not_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            generator = ResourceGenerator(paths)
            bundle = generator.generate(desired)
            by_id = {resource.resource_id: resource for resource in bundle.resources}
            state_dir = Path(by_id["state-dir"].path)
            owner_path = Path(by_id["state-owner"].path)
            state_dir.mkdir(parents=True, mode=0o700)
            (state_dir / "unexpected.txt").write_text("do not remove", encoding="utf-8")

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

            service = HostApplyService(paths, registry, generator=generator, systemd=FakeSystemd())
            result = service._recover_failed_first_apply_state("qual", bundle)

            self.assertIsNone(result)
            self.assertTrue((state_dir / "unexpected.txt").is_file())

    def test_rollback_removes_known_logs_before_new_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir()
            owner = state_dir / ".owner"
            owner.write_text("workspace-mcp-manager-instance=qual\n", encoding="utf-8")
            (state_dir / "mcp.log").write_text("", encoding="utf-8")
            (state_dir / "tunnel-supervisor.log").write_text("log\n", encoding="utf-8")
            rollback = _RollbackState()
            rollback.created_paths.extend([state_dir, owner])
            rollback.created_state_dirs.append(state_dir)

            rollback.restore()

            self.assertFalse(state_dir.exists())

    def test_rollback_preserves_owner_when_unknown_state_content_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir()
            owner = state_dir / ".owner"
            owner.write_text("workspace-mcp-manager-instance=qual\n", encoding="utf-8")
            (state_dir / "mcp.log").write_text("", encoding="utf-8")
            (state_dir / "unexpected.txt").write_text("preserve", encoding="utf-8")
            rollback = _RollbackState()
            rollback.created_paths.extend([state_dir, owner])
            rollback.created_state_dirs.append(state_dir)

            rollback.restore()

            self.assertTrue(state_dir.is_dir())
            self.assertTrue(owner.is_file())
            self.assertTrue((state_dir / "unexpected.txt").is_file())
            self.assertFalse((state_dir / "mcp.log").exists())


if __name__ == "__main__":
    unittest.main()
