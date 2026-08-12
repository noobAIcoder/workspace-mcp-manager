from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.cli import main
from workspace_mcp_manager.paths import ManagerPaths

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


class CliHostBoundaryTests(unittest.TestCase):
    def test_create_persists_through_host_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            declaration = root / "instance.json"
            declaration.write_text(json.dumps(sample_instance()), encoding="utf-8")
            paths = paths_for(root)
            output = StringIO()
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(output):
                bridge_type.return_value.run_registry_write.return_value = {
                    "ok": True,
                    "action": "create",
                    "instance_id": "sample",
                }
                rc = main(["instance", "create", str(declaration)])
            self.assertEqual(rc, 0)
            bridge_type.return_value.run_registry_write.assert_called_once()
            action, desired = bridge_type.return_value.run_registry_write.call_args.args
            self.assertEqual(action, "create")
            self.assertEqual(desired.instance_id.value, "sample")
            self.assertEqual(json.loads(output.getvalue())["instance_id"], "sample")

    def test_list_show_and_render_use_host_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type:
                bridge = bridge_type.return_value
                bridge.run_list.return_value = {"ok": True, "instances": []}
                bridge.run_show.return_value = {"ok": True, "instance_id": "sample"}
                bridge.run_render.return_value = {"ok": True, "instance_id": "sample", "resources": []}
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["instance", "list"]), 0)
                    self.assertEqual(main(["instance", "show", "sample"]), 0)
                    self.assertEqual(main(["instance", "render", "sample"]), 0)
                bridge.run_list.assert_called_once_with()
                bridge.run_show.assert_called_once_with("sample")
                bridge.run_render.assert_called_once_with("sample", include_content=False)


if __name__ == "__main__":
    unittest.main()
