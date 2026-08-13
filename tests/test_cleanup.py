from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.cleanup import LegacyCleanupService
from workspace_mcp_manager.cli import main
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ManagerError
from workspace_mcp_manager.paths import ManagerPaths
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


def write_legacy(root: Path, iid: str = "legacy-qual") -> LegacyCleanupService:
    paths = paths_for(root)
    registry = InstanceRegistry(paths.registry_dir)
    service = LegacyCleanupService(paths, registry)

    service.legacy_config_dir.mkdir(parents=True, exist_ok=True)
    service.legacy_profile_dir.mkdir(parents=True, exist_ok=True)
    service.paths.user_unit_dir.mkdir(parents=True, exist_ok=True)
    service.legacy_launcher_dir.mkdir(parents=True, exist_ok=True)
    service.legacy_state_root.mkdir(parents=True, exist_ok=True)

    service._config_path(iid).write_text(
        "# Workspace MCP lifecycle configuration. Values are parsed as data, never sourced.\n"
        "CONFIG_VERSION=1\n"
        f"INSTANCE_ID={iid}\n"
        f"WORKSPACE_PATH={root / 'workspace'}\n",
        encoding="utf-8",
    )
    service._profile_path(iid).write_text(
        f"# workspace-mcp-instance={iid}\n"
        "config_version: 1\n"
        "control_plane:\n"
        "  api_key: \"env:CONTROL_PLANE_API_KEY\"\n",
        encoding="utf-8",
    )
    service._mcp_unit_path(iid).write_text(
        f"# workspace-mcp-instance={iid}\n"
        "[Service]\nExecStart=/usr/bin/sleep infinity\n",
        encoding="utf-8",
    )
    service._tunnel_unit_path(iid).write_text(
        f"# workspace-mcp-instance={iid}\n"
        "[Service]\nExecStart=/usr/bin/sleep infinity\n",
        encoding="utf-8",
    )
    launcher = service._launcher_path(iid)
    launcher.write_text(
        "#!/bin/sh\n"
        f"exec env WORKSPACE_MCP_CONFIG='{service._config_path(iid)}' "
        f"'{root / '.local/lib/workspace-mcp/workspace-mcp'}' \"$@\"\n",
        encoding="utf-8",
    )
    os.chmod(launcher, 0o755)
    state = service._state_path(iid)
    state.mkdir(parents=True, exist_ok=True)
    for name in ("mcp.log", "tunnel.log", "tunnel-supervisor.log"):
        (state / name).write_text(name + "\n", encoding="utf-8")
    return service


class LegacyCleanupAuditTests(unittest.TestCase):
    def test_discovers_and_audits_complete_legacy_instance_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = write_legacy(root)
            before = {
                p: p.read_bytes()
                for p in (
                    service._config_path("legacy-qual"),
                    service._profile_path("legacy-qual"),
                    service._mcp_unit_path("legacy-qual"),
                    service._tunnel_unit_path("legacy-qual"),
                    service._launcher_path("legacy-qual"),
                )
            }
            payload = service.audit()
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["read_only"])
            self.assertEqual([item["instance_id"] for item in payload["candidates"]], ["legacy-qual"])
            candidate = payload["candidates"][0]
            self.assertEqual(candidate["state"], "removable")
            self.assertEqual({item["ownership"] for item in candidate["resources"]}, {"legacy-owned"})
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)

    def test_manager_owned_unit_alone_is_not_discovered_as_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            paths.user_unit_dir.mkdir(parents=True, exist_ok=True)
            (paths.user_unit_dir / "coding-tools-mcp-manager-qual.service").write_text(
                "# workspace-mcp-manager-instance=manager-qual\n",
                encoding="utf-8",
            )
            service = LegacyCleanupService(paths, InstanceRegistry(paths.registry_dir))
            self.assertEqual(service.audit()["candidates"], [])

    def test_public_all_audit_filters_unproven_launcher_filename_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            launcher_dir = root / ".local/bin"
            launcher_dir.mkdir(parents=True, exist_ok=True)
            target = root / ".local/share/tool/bin/coding-tools-mcp"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("placeholder\n", encoding="utf-8")
            (launcher_dir / "coding-tools-mcp").symlink_to(target)
            output = StringIO()
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), redirect_stdout(output):
                self.assertEqual(main(["_runtime", "cleanup", "audit"]), 0)
            import json

            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["candidates"], [])

    def test_foreign_same_named_unit_is_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = write_legacy(root)
            service._mcp_unit_path("legacy-qual").write_text(
                "# workspace-mcp-manager-instance=legacy-qual\n",
                encoding="utf-8",
            )
            candidate = service.audit("legacy-qual")["candidates"][0]
            self.assertEqual(candidate["state"], "conflict")
            ownership = {item["kind"]: item["ownership"] for item in candidate["resources"]}
            self.assertEqual(ownership["legacy-mcp-unit"], "foreign")

    def test_foreign_launcher_is_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = write_legacy(Path(directory))
            service._launcher_path("legacy-qual").write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
            candidate = service.audit("legacy-qual")["candidates"][0]
            self.assertEqual(candidate["state"], "conflict")

    def test_unknown_state_residue_is_unsafe_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = write_legacy(Path(directory))
            (service._state_path("legacy-qual") / "mystery.db").write_text("x", encoding="utf-8")
            candidate = service.audit("legacy-qual")["candidates"][0]
            self.assertEqual(candidate["state"], "conflict")
            state = next(item for item in candidate["resources"] if item["kind"] == "legacy-state")
            self.assertEqual(state["ownership"], "unsafe")

    def test_registry_collision_blocks_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = write_legacy(root)
            raw = sample_instance()
            raw["instance_id"] = "legacy-qual"
            raw["workspace_path"] = str(root / "manager-workspace")
            raw["mcp"]["port"] = 19001
            raw["tunnel"]["health_port"] = 19002
            raw["tunnel"]["id"] = "tunnel_legacyqual123"
            raw["tunnel"]["profile"] = "workspace-mcp-manager-legacy-qual"
            raw["tunnel"]["env_file"] = str(root / "runtime.env")
            raw["mcp"]["external_roots"] = []
            raw["access"] = {"read_only": [], "read_write": []}
            raw["github"] = {"config_dir": None}
            service.registry.create(DesiredInstance.from_dict(raw))
            candidate = service.audit("legacy-qual")["candidates"][0]
            self.assertEqual(candidate["state"], "conflict")
            self.assertTrue(candidate["manager_registry_collision"])
            with self.assertRaises(ManagerError):
                service.apply("legacy-qual")


class LegacyCleanupExecuteTests(unittest.TestCase):
    def test_execute_orders_systemd_then_files_and_preserves_shared_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = write_legacy(root)
            shared_binary = root / ".local/lib/workspace-mcp/workspace-mcp"
            shared_binary.parent.mkdir(parents=True, exist_ok=True)
            shared_binary.write_text("shared\n", encoding="utf-8")
            runtime_env = root / ".config/tunnel-client/runtime.env"
            runtime_env.write_text("CONTROL_PLANE_API_KEY=sentinel-secret\n", encoding="utf-8")
            shared_before = shared_binary.read_bytes()
            env_before = runtime_env.read_bytes()

            def observation(unit: str) -> dict[str, str]:
                fragment = (
                    service._tunnel_unit_path("legacy-qual")
                    if unit.startswith("tunnel-client-")
                    else service._mcp_unit_path("legacy-qual")
                )
                return {
                    "LoadState": "loaded",
                    "ActiveState": "active",
                    "UnitFileState": "enabled",
                    "FragmentPath": str(fragment),
                }

            calls: list[list[str]] = []

            def run(argv, **_kwargs):
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch.object(service, "_systemd_observation", side_effect=observation), patch(
                "workspace_mcp_manager.cleanup._run", side_effect=run
            ):
                result = service.apply("legacy-qual")

            self.assertTrue(result["ok"])
            self.assertEqual(result["final"]["state"], "clean")
            commands = [item[2:] for item in calls]
            self.assertEqual(commands[0], ["stop", "tunnel-client-legacy-qual.service"])
            self.assertEqual(commands[1], ["stop", "coding-tools-mcp-legacy-qual.service"])
            self.assertEqual(commands[2], ["disable", "tunnel-client-legacy-qual.service"])
            self.assertEqual(commands[3], ["disable", "coding-tools-mcp-legacy-qual.service"])
            self.assertEqual(commands[4], ["daemon-reload"])

            self.assertEqual(shared_binary.read_bytes(), shared_before)
            self.assertEqual(runtime_env.read_bytes(), env_before)
            self.assertTrue(shared_binary.exists())
            self.assertTrue(runtime_env.exists())
            for path in (
                service._config_path("legacy-qual"),
                service._profile_path("legacy-qual"),
                service._mcp_unit_path("legacy-qual"),
                service._tunnel_unit_path("legacy-qual"),
                service._launcher_path("legacy-qual"),
                service._state_path("legacy-qual"),
            ):
                self.assertFalse(path.exists())

    def test_execute_clean_target_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            service = LegacyCleanupService(paths, InstanceRegistry(paths.registry_dir))
            result = service.apply("legacy-qual")
            self.assertTrue(result["ok"])
            self.assertEqual(result["operations"], [])
            self.assertEqual(result["final"]["state"], "clean")

    def test_execute_fails_when_unit_fragment_is_foreign(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = write_legacy(root)
            with patch.object(
                service,
                "_systemd_observation",
                return_value={
                    "LoadState": "loaded",
                    "ActiveState": "active",
                    "UnitFileState": "enabled",
                    "FragmentPath": "/tmp/foreign.service",
                },
            ):
                with self.assertRaises(ManagerError):
                    service.apply("legacy-qual")
            self.assertTrue(service._config_path("legacy-qual").exists())


if __name__ == "__main__":
    unittest.main()
