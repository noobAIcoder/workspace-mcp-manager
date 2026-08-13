from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.domain import DeploymentTarget, DesiredInstance, LifecycleIntent, RuntimeTarget
from workspace_mcp_manager.access import stale_applied_access_resources
from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.host_apply import (
    HostApplyService,
    HostExecutionBridge,
    HostLifecycleWorker,
    SystemdUserAdapter,
    _RollbackState,
)
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import PlanItem, PlanOperation, ReconciliationPlan
from workspace_mcp_manager.registry import InstanceRegistry

from tests.helpers import sample_instance


class FakeSystemd:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.active: set[str] = set()
        self.enabled: set[str] = set()

    def verify_generated_units(self, _bundle) -> None:
        self.calls.append(("verify", None))

    def preflight_access_namespace(self, _desired) -> None:
        self.calls.append(("access-preflight", None))

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
                bridge.run_access("add-ro", "qual", alias="docs", path="/srv/shared/docs")
                run_json.assert_called_with(
                    ["_runtime", "access", "add-ro", "qual", "docs", "/srv/shared/docs"],
                    unit_fragment="access-add-ro-qual",
                )

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
            service = HostApplyService(
                paths,
                registry,
                planner=StaticPlanner([conflict]),
                generator=ResourceGenerator(paths),
                systemd=FakeSystemd(),
            )
            with self.assertRaises(ManagerError):
                service.apply("qual")

    def test_failed_first_apply_residue_restores_owner_and_preserves_runtime_logs(self) -> None:
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
            mcp_log = state_dir / "mcp.log"
            tunnel_log = state_dir / "tunnel-supervisor.log"
            mcp_log.write_text("", encoding="utf-8")
            tunnel_log.write_text("manager log\n", encoding="utf-8")

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
            self.assertTrue(state_dir.is_dir())
            self.assertEqual(owner_path.read_text(encoding="utf-8"), by_id["state-owner"].content)
            self.assertTrue(mcp_log.is_file())
            self.assertEqual(tunnel_log.read_text(encoding="utf-8"), "manager log\n")
            recovery = json.loads(Path(result["recovery_journal"]).read_text(encoding="utf-8"))
            self.assertEqual(recovery["status"], "succeeded")
            self.assertEqual(recovery["attempted"][0]["operation"], "CREATE")
            self.assertEqual(recovery["attempted"][0]["target"], str(owner_path))
            self.assertEqual(recovery["executed"][0]["target"], str(owner_path))

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
            self.assertFalse(owner_path.exists())
            self.assertTrue((state_dir / "unexpected.txt").is_file())

    def test_failed_mutation_attempt_is_journaled_before_execution(self) -> None:
        class FailingStartSystemd(FakeSystemd):
            def start(self, unit: str) -> None:
                self.calls.append(("start", unit))
                raise ManagerError(ErrorCode.IO_ERROR, "synthetic start failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            generator = ResourceGenerator(paths)
            bundle = generator.generate(desired)
            systemd = FailingStartSystemd()
            service = HostApplyService(paths, registry, generator=generator, systemd=systemd)
            plan = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(
                    PlanItem(
                        PlanOperation.START,
                        "coding-tools-mcp-qual.service",
                        "synthetic start",
                        "mcp-unit",
                    ),
                ),
                warnings=(),
            )
            journal_path = service._journal_path("qual", "attempt-test")
            journal = {"status": "running", "attempted": [], "executed": []}
            service._write_journal(journal_path, journal)

            with self.assertRaises(ManagerError):
                service._execute(
                    plan,
                    bundle,
                    desired,
                    _RollbackState(),
                    force_restart=False,
                    journal=journal,
                    journal_path=journal_path,
                )

            persisted = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["attempted"][0]["operation"], "START")
            self.assertEqual(persisted["attempted"][0]["target"], "coding-tools-mcp-qual.service")
            self.assertEqual(persisted["executed"], [])

    def test_apply_failure_exposes_transaction_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            plan = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(),
                warnings=(),
            )
            service = HostApplyService(
                paths,
                registry,
                planner=StaticPlanner([plan, plan]),
                generator=ResourceGenerator(paths),
                systemd=FakeSystemd(),
            )
            failure = ManagerError(
                ErrorCode.IO_ERROR,
                "synthetic apply failure",
                {"unit_status": {"ActiveState": "failed"}},
            )
            with patch.object(service, "_execute", side_effect=failure):
                with self.assertRaises(ManagerError) as caught:
                    service.apply("qual")

            self.assertEqual(caught.exception.details["unit_status"]["ActiveState"], "failed")
            transaction = caught.exception.details["transaction"]
            self.assertTrue(Path(transaction["journal"]).is_file())
            persisted = json.loads(Path(transaction["journal"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "failed")
            self.assertIn("rollback", persisted)

    def test_destructive_unit_mutation_requires_owned_unit_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            desired = replace(
                desired,
                lifecycle=LifecycleIntent(DeploymentTarget.ABSENT, RuntimeTarget.STOPPED),
            )
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            generator = ResourceGenerator(paths)
            bundle = generator.generate(desired)
            systemd = FakeSystemd()
            systemd.active.add("coding-tools-mcp-qual.service")
            service = HostApplyService(paths, registry, generator=generator, systemd=systemd)
            plan = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(
                    PlanItem(
                        PlanOperation.STOP,
                        "coding-tools-mcp-qual.service",
                        "synthetic foreign loaded unit",
                        "mcp-unit",
                    ),
                ),
                warnings=(),
            )

            with self.assertRaises(ManagerError) as caught:
                service._execute(
                    plan,
                    bundle,
                    desired,
                    _RollbackState(),
                    force_restart=False,
                    journal={"attempted": [], "executed": []},
                    journal_path=service._journal_path("qual", "ownership-test"),
                )

            self.assertIn("without verified manager ownership", str(caught.exception))
            self.assertNotIn(("stop", "coding-tools-mcp-qual.service"), systemd.calls)

    def test_unknown_state_content_fails_before_destructive_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            desired = replace(
                desired,
                lifecycle=LifecycleIntent(DeploymentTarget.ABSENT, RuntimeTarget.STOPPED),
            )
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            generator = ResourceGenerator(paths)
            bundle = generator.generate(desired)
            by_id = {resource.resource_id: resource for resource in bundle.resources}

            state_dir = Path(by_id["state-dir"].path)
            state_owner = Path(by_id["state-owner"].path)
            state_dir.mkdir(parents=True, mode=0o700)
            state_owner.write_text(by_id["state-owner"].content or "", encoding="utf-8")
            os.chmod(state_owner, by_id["state-owner"].mode)
            (state_dir / "unexpected.txt").write_text("preserve", encoding="utf-8")

            mcp_resource = by_id["mcp-unit"]
            mcp_path = Path(mcp_resource.path)
            mcp_path.parent.mkdir(parents=True, exist_ok=True)
            mcp_path.write_text(mcp_resource.content or "", encoding="utf-8")
            os.chmod(mcp_path, mcp_resource.mode)

            systemd = FakeSystemd()
            systemd.active.add("coding-tools-mcp-qual.service")
            service = HostApplyService(paths, registry, generator=generator, systemd=systemd)
            plan = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(
                    PlanItem(PlanOperation.STOP, "coding-tools-mcp-qual.service", "stop", "mcp-unit"),
                    PlanItem(PlanOperation.REMOVE, str(mcp_path), "remove", "mcp-unit"),
                    PlanItem(PlanOperation.REMOVE, str(state_owner), "remove", "state-owner"),
                    PlanItem(PlanOperation.REMOVE, str(state_dir), "remove", "state-dir"),
                ),
                warnings=(),
            )

            with self.assertRaises(ManagerError) as caught:
                service._execute(
                    plan,
                    bundle,
                    desired,
                    _RollbackState(),
                    force_restart=False,
                    journal={"attempted": [], "executed": []},
                    journal_path=service._journal_path("qual", "unknown-state-test"),
                )

            self.assertIn("unknown manager-state content", str(caught.exception))
            self.assertNotIn(("stop", "coding-tools-mcp-qual.service"), systemd.calls)
            self.assertTrue(state_owner.is_file())
            self.assertTrue((state_dir / "unexpected.txt").is_file())

    def test_noop_apply_has_no_managed_resource_mutations(self) -> None:
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
            plan = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(),
                warnings=(),
            )
            service = HostApplyService(
                paths,
                registry,
                planner=StaticPlanner([plan, plan, plan]),
                generator=ResourceGenerator(paths),
                systemd=FakeSystemd(),
            )

            result = service.apply("qual")
            persisted = json.loads(Path(result["journal"]).read_text(encoding="utf-8"))
            attempted = [item["operation"] for item in persisted["attempted"]]
            executed = [item["operation"] for item in persisted["executed"]]
            self.assertEqual(attempted, ["WRITE_APPLIED"])
            self.assertEqual(executed, ["WRITE_APPLIED"])

    def test_access_apply_runs_namespace_preflight_before_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            raw = desired_for(root).to_dict()
            raw["lifecycle"] = {"deployment": "present", "runtime": "stopped"}
            raw["access"] = {
                "read_only": [{"alias": "docs", "path": str(root / "shared-ro")}],
                "read_write": [{"alias": "scratch", "path": str(root / "shared-rw")}],
            }
            (root / "shared-ro").mkdir()
            (root / "shared-rw").mkdir()
            desired = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            plan = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(),
                warnings=(),
            )
            systemd = FakeSystemd()
            service = HostApplyService(
                paths,
                registry,
                planner=StaticPlanner([plan, plan, plan]),
                generator=ResourceGenerator(paths),
                systemd=systemd,
            )

            result = service.apply("qual")

            self.assertTrue(result["ok"])
            self.assertEqual(systemd.calls[:2], [("verify", None), ("access-preflight", None)])

    def test_systemd_access_namespace_preflight_renders_ro_and_rw_binds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            raw = desired_for(root).to_dict()
            raw["access"] = {
                "read_only": [{"alias": "docs", "path": str(root / "shared-ro")}],
                "read_write": [{"alias": "scratch", "path": str(root / "shared-rw")}],
            }
            desired = DesiredInstance.from_dict(raw)
            adapter = SystemdUserAdapter(state_root=paths.state_root)
            from subprocess import CompletedProcess

            with patch("workspace_mcp_manager.host_apply._run") as run:
                run.return_value = CompletedProcess([], 0, "", "")
                adapter.preflight_access_namespace(desired)

            argv = run.call_args.args[0]
            self.assertIn("--property=PrivateUsers=true", argv)
            self.assertTrue(any(item.startswith("--property=BindReadOnlyPaths=") for item in argv))
            self.assertTrue(any(item.startswith("--property=BindPaths=") for item in argv))
            self.assertEqual(argv[-1], "/usr/bin/true")

    def test_present_apply_removes_stale_access_after_mcp_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            old_raw = desired.to_dict()
            old_raw["access"]["read_only"] = [
                {"alias": "docs", "path": str(root / "shared-ro")}
            ]
            old = DesiredInstance.from_dict(old_raw)
            generator = ResourceGenerator(paths)
            old_bundle = generator.generate(old)
            for resource in old_bundle.resources:
                if not resource.resource_id.startswith("access-"):
                    continue
                path = Path(resource.path)
                if resource.kind.value == "directory":
                    path.mkdir(parents=True, exist_ok=True, mode=resource.mode)
                    os.chmod(path, resource.mode)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(resource.content or "", encoding="utf-8")
                    os.chmod(path, resource.mode)

            applied_path = paths.state_root / "applied/qual.json"
            applied_path.parent.mkdir(parents=True)
            applied_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "instance_id": "qual",
                        "desired_fingerprint": old.fingerprint(),
                        "owned_resources": [
                            {
                                "resource_id": resource.resource_id,
                                "kind": resource.kind.value,
                                "path": resource.path,
                                "fingerprint": resource.fingerprint(),
                            }
                            for resource in old_bundle.resources
                        ],
                    }
                ),
                encoding="utf-8",
            )

            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            bundle = generator.generate(desired)
            stale = stale_applied_access_resources(paths, desired, bundle)
            plan = ReconciliationPlan(
                instance_id="qual",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(
                    PlanItem(
                        PlanOperation.RESTART,
                        "coding-tools-mcp-qual.service",
                        "access declaration changed",
                        "mcp-unit",
                    ),
                    *tuple(
                        PlanItem(
                            PlanOperation.REMOVE,
                            resource.path,
                            "obsolete access resource",
                            resource.resource_id,
                        )
                        for resource in stale
                    ),
                ),
                warnings=(),
            )
            systemd = FakeSystemd()
            systemd.active.add("coding-tools-mcp-qual.service")
            service = HostApplyService(paths, registry, generator=generator, systemd=systemd)
            journal_path = service._journal_path("qual", "stale-access-test")
            journal = {"status": "running", "attempted": [], "executed": []}
            service._write_journal(journal_path, journal)
            rollback = _RollbackState()

            service._execute(
                plan,
                bundle,
                desired,
                rollback,
                force_restart=False,
                journal=journal,
                journal_path=journal_path,
            )

            self.assertIn(("restart", "coding-tools-mcp-qual.service"), systemd.calls)
            self.assertFalse((Path(desired.workspace_path) / ".workspace-mcp-access").exists())
            persisted = json.loads(journal_path.read_text(encoding="utf-8"))
            executed = [item["operation"] for item in persisted["executed"]]
            self.assertLess(executed.index("RESTART"), executed.index("REMOVE"))
            rollback.discard()

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
