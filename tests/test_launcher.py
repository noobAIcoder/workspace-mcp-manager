from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import HostResourceObserver, ObservationStatus

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


class LauncherTests(unittest.TestCase):
    def _launcher(self, root: Path):
        desired = DesiredInstance.from_dict(sample_instance())
        resources = ResourceGenerator(paths_for(root)).generate(desired).resources
        return next(item for item in resources if item.resource_id == "launcher")

    def test_launcher_resource_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = self._launcher(root)
            self.assertEqual(launcher.path, str(root / ".local/bin/sample-mcp"))
            self.assertEqual(launcher.mode, 0o755)
            self.assertEqual(launcher.ownership_token, "workspace-mcp-manager-instance=sample")
            self.assertTrue(launcher.remove_when_absent)
            text = launcher.content or ""
            self.assertIn("# workspace-mcp-manager-instance=sample", text)
            self.assertIn('instance "$command" "$INSTANCE" "$@"', text)
            self.assertIn('access "$action" "$INSTANCE" "$@"', text)
            self.assertIn('codex "$action" "$INSTANCE" "$@"', text)
            self.assertNotIn("WORKSPACE_MCP_CONFIG", text)
            self.assertNotIn("eval ", text)
            self.assertNotIn("_runtime", text)
            self.assertNotIn(" cleanup", text)

    def test_launcher_dispatches_only_supported_instance_families(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            paths.manager_executable.parent.mkdir(parents=True, exist_ok=True)
            paths.manager_executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
                encoding="utf-8",
            )
            os.chmod(paths.manager_executable, 0o755)
            launcher_resource = self._launcher(root)
            launcher = Path(launcher_resource.path)
            launcher.write_text(launcher_resource.content or "", encoding="utf-8")
            os.chmod(launcher, 0o755)

            status = subprocess.run(
                [str(launcher), "status", "--pretty"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(status.returncode, 0)
            self.assertEqual(
                status.stdout.splitlines(),
                ["--registry-dir", str(paths.registry_dir), "instance", "status", "sample", "--pretty"],
            )

            access = subprocess.run(
                [str(launcher), "access", "add-ro", "docs", "/srv/docs"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(access.returncode, 0)
            self.assertEqual(
                access.stdout.splitlines(),
                ["--registry-dir", str(paths.registry_dir), "access", "add-ro", "sample", "docs", "/srv/docs"],
            )

            codex = subprocess.run(
                [str(launcher), "codex", "status", "job-1"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(codex.returncode, 0)
            self.assertEqual(
                codex.stdout.splitlines(),
                ["--registry-dir", str(paths.registry_dir), "codex", "status", "sample", "job-1"],
            )

            rejected = subprocess.run(
                [str(launcher), "host"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("unsupported command", rejected.stderr)

    def test_foreign_same_named_launcher_is_not_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = self._launcher(root)
            path = Path(resource.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
            os.chmod(path, 0o755)
            observed = HostResourceObserver().observe_resource(resource)
            self.assertEqual(observed.status, ObservationStatus.FOREIGN)
            self.assertIn("ownership marker is absent", observed.detail)


if __name__ == "__main__":
    unittest.main()
