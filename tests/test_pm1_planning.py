from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.development_environment import DevelopmentEnvironmentReconciler
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import PlanOperation, ReconciliationPlanner
from workspace_mcp_manager.registry import InstanceRegistry

from helpers import sample_v2_instance


@unittest.skipUnless(shutil.which("git"), "git is unavailable")
class Pm1PlanningTests(unittest.TestCase):
    def _paths(self, root: Path) -> ManagerPaths:
        return ManagerPaths(
            account_home=root,
            config_root=root / ".config/workspace-mcp-manager",
            registry_dir=root / ".config/workspace-mcp-manager/instances",
            state_root=root / ".local/state/workspace-mcp-manager",
            user_unit_dir=root / ".config/systemd/user",
            tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
            manager_executable=root / ".local/bin/workspace-mcp-manager",
        )

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            [shutil.which("git") or "git", "-C", str(repo), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

    def _desired(self, root: Path, repo: Path) -> DesiredInstance:
        raw = sample_v2_instance()
        raw["workspace_path"] = str(repo)
        raw["mcp"]["exec_path"] = "/usr/bin:/bin"
        raw["github"]["config_dir"] = str(root / ".config/workspace-mcp-manager/github/sample")
        raw["github"]["binary"] = "/bin/true"
        return DesiredInstance.from_dict(raw)

    def test_git_groups_converge_create_update_remove_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            self._git(repo, "init", "--quiet")
            self._git(repo, "config", "--local", "user.name", "Example Operator")
            self._git(repo, "config", "--local", "user.email", "operator@example.invalid")
            self._git(repo, "remote", "add", "origin", "git@github.com:owner/repo.git")

            paths = self._paths(root)
            desired = self._desired(root, repo)
            planner = ReconciliationPlanner(paths, InstanceRegistry(paths.registry_dir))
            operations = planner._development_git_operations(desired)
            self.assertEqual(
                [item.operation for item in operations],
                [PlanOperation.CREATE, PlanOperation.CREATE],
            )

            development = DevelopmentEnvironmentReconciler(paths)
            development.converge_identity(desired)
            development.converge_transport(desired)
            operations = planner._development_git_operations(desired)
            self.assertEqual(
                [item.operation for item in operations],
                [PlanOperation.NOOP, PlanOperation.NOOP],
            )

            changed_raw = desired.to_dict()
            changed_raw["git"]["identity"]["name"] = "Changed Operator"
            changed_raw["git"]["remote"]["protocol"] = "https-gh"
            changed = DesiredInstance.from_dict(changed_raw)
            operations = planner._development_git_operations(changed)
            self.assertEqual(
                [item.operation for item in operations],
                [PlanOperation.UPDATE, PlanOperation.UPDATE],
            )

            absent_raw = desired.to_dict()
            absent_raw["lifecycle"] = {"deployment": "absent", "runtime": "stopped"}
            absent = DesiredInstance.from_dict(absent_raw)
            operations = planner._development_git_operations(absent)
            self.assertEqual(
                [item.operation for item in operations],
                [PlanOperation.REMOVE, PlanOperation.REMOVE],
            )


if __name__ == "__main__":
    unittest.main()
