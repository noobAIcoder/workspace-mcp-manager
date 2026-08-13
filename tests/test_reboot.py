from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ErrorCode
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.reboot import RebootService, reboot_bridge_main
from workspace_mcp_manager.registry import InstanceRegistry

from tests.helpers import sample_instance


BOOT_A = "11111111-1111-4111-8111-111111111111"
BOOT_B = "22222222-2222-4222-8222-222222222222"


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


def desired_for(root: Path, *, instance_id: str = "qual", running: bool = True) -> DesiredInstance:
    raw = sample_instance()
    raw["instance_id"] = instance_id
    raw["workspace_path"] = str(root / f"workspace-{instance_id}")
    raw["mcp"]["binary"] = "/usr/bin/true"
    raw["mcp"]["port"] = 17000 + len(instance_id)
    raw["mcp"]["external_roots"] = []
    raw["tunnel"]["binary"] = "/usr/bin/true"
    raw["tunnel"]["id"] = f"tunnel_{instance_id}123"
    raw["tunnel"]["profile"] = f"workspace-mcp-{instance_id}"
    raw["tunnel"]["health_port"] = 18000 + len(instance_id)
    raw["tunnel"]["env_file"] = str(root / "runtime.env")
    raw["access"] = {"read_only": [], "read_write": []}
    raw["github"] = {"config_dir": None}
    raw["recovery"]["admission_guard_enabled"] = False
    raw["lifecycle"]["runtime"] = "running" if running else "stopped"
    return DesiredInstance.from_dict(raw)


def service_for(root: Path) -> tuple[RebootService, InstanceRegistry, Path]:
    paths = paths_for(root)
    registry = InstanceRegistry(paths.registry_dir)
    registry.create(desired_for(root, instance_id="qual", running=True))
    registry.create(desired_for(root, instance_id="stopped", running=False))
    bridge = paths.manager_executable.with_name("workspace-mcp-reboot")
    bridge.parent.mkdir(parents=True, exist_ok=True)
    bridge.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(bridge, 0o755)
    return RebootService(paths, registry, wsl_detector=lambda: False), registry, bridge


class RebootBridgeTests(unittest.TestCase):
    def test_bridge_accepts_only_exact_check_and_reboot_commands(self) -> None:
        calls: list[list[str]] = []

        def run(argv, **_kwargs):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch("workspace_mcp_manager.reboot.is_wsl", return_value=False), patch(
            "workspace_mcp_manager.reboot.subprocess.run", side_effect=run
        ):
            self.assertEqual(reboot_bridge_main(["--check"]), 0)
            self.assertEqual(reboot_bridge_main([]), 0)

        self.assertEqual(
            calls[0],
            ["/usr/bin/sudo", "-n", "-l", "/usr/bin/systemctl", "reboot"],
        )
        self.assertEqual(
            calls[1],
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "reboot"],
        )

        stderr = StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(reboot_bridge_main(["restart", "something.service"]), 2)
        self.assertIn("accepts only --check or no arguments", stderr.getvalue())

    def test_bridge_refuses_wsl_before_sudo(self) -> None:
        stderr = StringIO()
        with patch("workspace_mcp_manager.reboot.is_wsl", return_value=True), patch(
            "workspace_mcp_manager.reboot.subprocess.run"
        ) as run, redirect_stderr(stderr):
            self.assertEqual(reboot_bridge_main(["--check"]), 69)
        run.assert_not_called()
        self.assertIn("unsupported on WSL", stderr.getvalue())


class RebootServiceTests(unittest.TestCase):
    def _boot_patch(self, path: Path):
        return patch("workspace_mcp_manager.reboot.BOOT_ID_PATH", path)

    def test_checkpoint_is_fsynced_before_preflight_and_captures_only_running_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A + "\n", encoding="utf-8")
            service, _registry, _bridge = service_for(root)
            observed: list[dict] = []

            def run_bridge(_bridge, *, check_only):
                payload = json.loads(service.checkpoint_path.read_text(encoding="utf-8"))
                observed.append(payload)
                self.assertEqual(payload["state"], "prepared" if check_only else "requesting")
                return subprocess.CompletedProcess([], 0, "", "")

            with self._boot_patch(boot), patch.object(service, "_run_bridge", side_effect=run_bridge):
                result = service.request(reason="qualification")

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "request_accepted")
            self.assertEqual(len(observed), 2)
            checkpoint = json.loads(service.checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual([item["instance_id"] for item in checkpoint["expected_instances"]], ["qual"])
            self.assertEqual(checkpoint["boot_id_before"], BOOT_A)
            self.assertEqual(service.reboot_root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(service.checkpoint_path.stat().st_mode & 0o777, 0o600)

    def test_authorization_failure_is_persisted_without_reboot_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A, encoding="utf-8")
            service, _registry, _bridge = service_for(root)
            with self._boot_patch(boot), patch.object(
                service,
                "_run_bridge",
                return_value=subprocess.CompletedProcess([], 1, "", "permission denied"),
            ) as run:
                result = service.request(reason="test unavailable authorization")

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "authorization_failed")
            self.assertEqual(run.call_count, 1)
            persisted = json.loads(service.checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["state"], "authorization_failed")
            self.assertFalse(persisted["authorization"]["ok"])
            self.assertFalse(persisted["request_result"]["ok"])
            self.assertIsNone(persisted["boot_id_after"])

    def test_synchronous_request_failure_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A, encoding="utf-8")
            service, _registry, _bridge = service_for(root)
            calls = [
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 5, "", "synthetic request failure"),
            ]
            with self._boot_patch(boot), patch.object(service, "_run_bridge", side_effect=calls):
                result = service.request(reason="synthetic failure")

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "request_failed")
            persisted = json.loads(service.checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["state"], "request_failed")
            self.assertEqual(persisted["request_result"]["exit_code"], 5)
            self.assertIn("synthetic request failure", persisted["request_result"]["error"])

    def test_reason_is_redacted_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A, encoding="utf-8")
            service, _registry, _bridge = service_for(root)
            with self._boot_patch(boot), patch.object(
                service,
                "_run_bridge",
                return_value=subprocess.CompletedProcess([], 1, "", ""),
            ):
                service.request(reason="CONTROL_PLANE_API_KEY=super-secret")
            text = service.checkpoint_path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret", text)
            self.assertIn("<REDACTED>", text)

    def test_reboot_check_reports_unchanged_failed_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A, encoding="utf-8")
            service, _registry, _bridge = service_for(root)
            with self._boot_patch(boot), patch.object(
                service,
                "_run_bridge",
                return_value=subprocess.CompletedProcess([], 1, "", ""),
            ):
                service.request(reason="not authorized")
                result = service.check()
            self.assertFalse(result["ok"])
            self.assertEqual(result["action"], "reboot-check")
            self.assertEqual(result["state"], "authorization_failed")
            self.assertEqual(result["boot_id_before"], BOOT_A)
            self.assertIsNone(result["boot_id_after"])

    def test_reboot_check_reconciles_expected_instance_after_boot_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A, encoding="utf-8")
            service, _registry, _bridge = service_for(root)
            with self._boot_patch(boot), patch.object(
                service,
                "_run_bridge",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                ],
            ):
                service.request(reason="expected reboot")
                boot.write_text(BOOT_B, encoding="utf-8")
                with patch.object(service, "_unit_active", return_value=True), patch.object(
                    service,
                    "_http",
                    side_effect=[(200, "{}"), (200, "live"), (200, "ready")],
                ):
                    result = service.check()

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "reboot-check")
            self.assertEqual(result["state"], "reconciled")
            self.assertEqual(result["boot_id_after"], BOOT_B)
            self.assertTrue(result["reconciliation"]["instances"][0]["ok"])

    def test_desired_change_after_boot_is_not_accepted_as_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A, encoding="utf-8")
            service, registry, _bridge = service_for(root)
            with self._boot_patch(boot), patch.object(
                service,
                "_run_bridge",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                ],
            ):
                service.request(reason="expected reboot")
                current = registry.get("qual").to_dict()
                current["mcp"]["exec_path"] = "/opt/changed:/usr/bin:/bin"
                registry.update(DesiredInstance.from_dict(current))
                boot.write_text(BOOT_B, encoding="utf-8")
                result = service.check()

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "boot_observed_waiting_recovery")
            self.assertEqual(result["reconciliation"]["instances"][0]["state"], "desired_changed")

    def test_waiting_recovery_can_advance_to_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot-id"
            boot.write_text(BOOT_A, encoding="utf-8")
            service, _registry, _bridge = service_for(root)
            with self._boot_patch(boot), patch.object(
                service,
                "_run_bridge",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                ],
            ):
                service.request(reason="expected reboot")
                boot.write_text(BOOT_B, encoding="utf-8")
                with patch.object(service, "_unit_active", return_value=False), patch.object(
                    service, "_http", return_value=(None, None)
                ):
                    first = service.check()
                with patch.object(service, "_unit_active", return_value=True), patch.object(
                    service,
                    "_http",
                    side_effect=[(200, "{}"), (200, "live"), (200, "ready")],
                ):
                    second = service.check()

            self.assertEqual(first["state"], "boot_observed_waiting_recovery")
            self.assertFalse(first["ok"])
            self.assertEqual(second["state"], "reconciled")
            self.assertTrue(second["ok"])

    def test_no_checkpoint_is_successful_nonmutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _registry, _bridge = service_for(root)
            result = service.check()
            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "no_checkpoint")

    def test_wsl_reboot_is_rejected_before_checkpoint_or_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired_for(root, instance_id="qual", running=True))
            service = RebootService(paths, registry, wsl_detector=lambda: True)
            with patch.object(service, "_run_bridge") as run:
                with self.assertRaises(Exception) as caught:
                    service.request(reason="must not reboot WSL")
            run.assert_not_called()
            self.assertFalse(service.checkpoint_path.exists())
            self.assertEqual(getattr(caught.exception, "code", None), ErrorCode.FEATURE_NOT_IMPLEMENTED)


if __name__ == "__main__":
    unittest.main()
