from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.distribution_runtime import (
    DistributionRuntimeError,
    executing_distribution_root,
    runtime_defaults,
)
from workspace_mcp_manager.paths import ManagerPaths


def paths_for(home: Path) -> ManagerPaths:
    config_root = home / ".config/workspace-mcp-manager"
    return ManagerPaths(
        account_home=home,
        config_root=config_root,
        registry_dir=config_root / "instances",
        state_root=home / ".local/state/workspace-mcp-manager",
        user_unit_dir=home / ".config/systemd/user",
        tunnel_profile_dir=config_root / "tunnel-profiles",
        manager_executable=home / ".local/bin/workspace-mcp-manager",
    )


def fake_release(root: Path, commit: str) -> tuple[Path, Path]:
    release = root / "lib/workspace-mcp-manager/releases" / f"dist-{commit}"
    module = release / "manager/src/workspace_mcp_manager/distribution_runtime.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture\n", encoding="utf-8")
    for relative in (
        "runtimes/python/bin/python3",
        "runtimes/coding-tools-mcp/bin/coding-tools-mcp",
        "runtimes/tunnel-client/tunnel-client",
    ):
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    (release / "install.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "distribution_id": release.name,
                "release_commit": commit,
            }
        ),
        encoding="utf-8",
    )
    return release, module


class DistributionRuntimeTests(unittest.TestCase):
    def test_tui_uses_executing_release_without_path_at_custom_prefix(self) -> None:
        from workspace_mcp_manager.tui import default_manager_executable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "custom-prefix"
            release, module = fake_release(root, "a" * 40)
            other, _ = fake_release(root, "b" * 40)
            (root / "lib/workspace-mcp-manager/current").symlink_to(other)
            manager = release / "bin/workspace-mcp-manager"
            manager.parent.mkdir()
            manager.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            manager.chmod(0o755)
            with patch("workspace_mcp_manager.tui.__file__", str(module.with_name("tui.py"))), patch.dict(
                os.environ, {"PATH": "", "WORKSPACE_MCP_MANAGER_BIN": ""}
            ), patch("sys.argv", ["unrelated-tui"]):
                self.assertEqual(default_manager_executable(), str(manager))

    def test_source_run_uses_explicit_host_tool_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            defaults = runtime_defaults(paths_for(home), module_file=Path(__file__))
            self.assertIsNone(defaults.distribution_root)
            self.assertEqual(defaults.coding_tools_mcp, home / ".local/bin/coding-tools-mcp")
            self.assertEqual(defaults.tunnel_client, home / ".local/bin/tunnel-client")

    def test_installed_execution_uses_own_release_specific_runtimes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, module = fake_release(root, "a" * 40)
            defaults = runtime_defaults(paths_for(root / "home"), module_file=module)
            self.assertEqual(defaults.distribution_root, release)
            self.assertEqual(defaults.coding_tools_mcp, release / "runtimes/coding-tools-mcp/bin/coding-tools-mcp")
            self.assertEqual(defaults.tunnel_client, release / "runtimes/tunnel-client/tunnel-client")
            self.assertNotIn("/current/", str(defaults.coding_tools_mcp))

    def test_current_movement_cannot_change_running_old_manager_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a, a_module = fake_release(root, "a" * 40)
            b, _b_module = fake_release(root, "b" * 40)
            manager_root = root / "lib/workspace-mcp-manager"
            current = manager_root / "current"
            current.symlink_to(Path("releases") / a.name)
            first = runtime_defaults(paths_for(root / "home"), module_file=a_module)
            current.unlink()
            current.symlink_to(Path("releases") / b.name)
            second = runtime_defaults(paths_for(root / "home"), module_file=a_module)
            self.assertEqual(first, second)
            self.assertEqual(second.distribution_root, a)

    def test_manager_only_installed_distribution_uses_external_runtimes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, module = fake_release(root, "d" * 40)
            for relative in ("runtimes/coding-tools-mcp/bin/coding-tools-mcp",
                             "runtimes/tunnel-client/tunnel-client"):
                (release / relative).unlink()
            uv = release / "tools/uv/uv"
            uv.parent.mkdir(parents=True)
            uv.write_text("# fixture\n", encoding="utf-8")
            (release / "install.json").write_text(json.dumps({
                "schema_version": 4, "distribution_profile": "manager-only",
                "distribution_id": release.name, "release_commit": "d" * 40,
                "components": {"python": {"version": "3.12"}, "uv": {"version": "0.12"}},
            }), encoding="utf-8")
            home = root / "home"
            mcp = home / ".local/opt/coding-tools-mcp/bin/coding-tools-mcp"
            tunnel = home / ".local/opt/tunnel-client/tunnel-client"
            for path in (mcp, tunnel):
                path.parent.mkdir(parents=True)
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)
            defaults = runtime_defaults(paths_for(home), module_file=module)
            self.assertEqual(defaults.distribution_root, release)
            self.assertEqual(defaults.coding_tools_mcp, mcp)
            self.assertEqual(defaults.tunnel_client, tunnel)
            self.assertFalse((release / "runtimes/tunnel-client").exists())

    def test_manager_only_missing_externals_does_not_break_tui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, module = fake_release(root, "e" * 40)
            for relative in ("runtimes/coding-tools-mcp/bin/coding-tools-mcp",
                             "runtimes/tunnel-client/tunnel-client"):
                (release / relative).unlink()
            uv = release / "tools/uv/uv"
            uv.parent.mkdir(parents=True)
            uv.write_text("# fixture\n", encoding="utf-8")
            (release / "install.json").write_text(json.dumps({
                "schema_version": 4, "distribution_profile": "manager-only",
                "distribution_id": release.name, "release_commit": "e" * 40,
                "components": {"python": {"version": "3.12"}, "uv": {"version": "0.12"}},
            }), encoding="utf-8")
            home = root / "home"
            with patch("workspace_mcp_manager.distribution_runtime.shutil.which", return_value=None):
                defaults = runtime_defaults(paths_for(home), module_file=module)
            self.assertEqual(defaults.coding_tools_mcp, home / ".local/opt/coding-tools-mcp/bin/coding-tools-mcp")
            self.assertEqual(defaults.tunnel_client, home / ".local/opt/tunnel-client/tunnel-client")

    def test_malformed_installed_projection_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, module = fake_release(root, "a" * 40)
            (release / "install.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(DistributionRuntimeError):
                executing_distribution_root(module_file=module)


if __name__ == "__main__":
    unittest.main()
