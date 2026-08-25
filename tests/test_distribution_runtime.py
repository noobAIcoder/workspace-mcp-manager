from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

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

    def test_malformed_installed_projection_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, module = fake_release(root, "a" * 40)
            (release / "install.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(DistributionRuntimeError):
                executing_distribution_root(module_file=module)


if __name__ == "__main__":
    unittest.main()
