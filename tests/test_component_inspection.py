from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.component_inspection import ComponentInventory
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.cli import build_parser


def paths_for(home: Path) -> ManagerPaths:
    return ManagerPaths(
        account_home=home,
        config_root=home / ".config/workspace-mcp-manager",
        registry_dir=home / ".config/workspace-mcp-manager/instances",
        state_root=home / ".local/state/workspace-mcp-manager",
        user_unit_dir=home / ".config/systemd/user",
        tunnel_profile_dir=home / ".config/workspace-mcp-manager/tunnel-profiles",
        manager_executable=home / ".local/bin/workspace-mcp-manager",
    )


class ComponentInspectionTests(unittest.TestCase):
    def test_missing_components_are_valid_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch("workspace_mcp_manager.component_inspection.shutil.which", return_value=None):
                with patch.object(ComponentInventory, "_latest", side_effect=AssertionError("network")):
                    inventory = ComponentInventory(paths_for(Path(temp))).inspect()
        self.assertTrue(inventory["ok"])
        self.assertFalse(inventory["components"]["coding-tools-mcp"]["installations"])
        self.assertFalse(inventory["components"]["tunnel-client"]["installations"])
        self.assertFalse(inventory["components"]["tunnel-client"]["upstream_checked"])

    def test_existing_instance_path_is_discovered_without_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            binary = home / "opt/tunnel-client/tunnel-client"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nprintf 'tunnel-client v0.0.20\\n'\n", encoding="utf-8")
            binary.chmod(0o755)
            paths = paths_for(home)
            paths.registry_dir.mkdir(parents=True)
            (paths.registry_dir / "example.json").write_text(
                json.dumps({"instance_id": "example", "tunnel": {"binary": str(binary)}}),
                encoding="utf-8",
            )
            with patch("workspace_mcp_manager.component_inspection.shutil.which", return_value=None):
                items = ComponentInventory(paths).inspect()["components"]["tunnel-client"]["installations"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["path"], str(binary))
        self.assertEqual(items[0]["version"], "v0.0.20")
        self.assertEqual(items[0]["used_by"], ["example"])
        self.assertEqual(items[0]["status"], "verified")

    def test_upstream_is_queried_only_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            inventory = ComponentInventory(paths_for(Path(temp)))
            with patch("workspace_mcp_manager.component_inspection.shutil.which", return_value=None):
                with patch.object(inventory, "_latest", return_value=("v0.0.20", None)) as latest:
                    inventory.inspect()
                    latest.assert_not_called()
                    result = inventory.inspect(check_upstream=True)
                    self.assertEqual(latest.call_count, 2)
        self.assertTrue(result["components"]["tunnel-client"]["upstream_checked"])
        self.assertEqual(result["components"]["tunnel-client"]["latest_stable"], "v0.0.20")

    def test_read_only_cli_flag(self) -> None:
        parser = build_parser()
        default = parser.parse_args(["host", "components"])
        upstream = parser.parse_args(["host", "components", "--check-updates"])
        self.assertFalse(default.check_updates)
        self.assertTrue(upstream.check_updates)


if __name__ == "__main__":
    unittest.main()
