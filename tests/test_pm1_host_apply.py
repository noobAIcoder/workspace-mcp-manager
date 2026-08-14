from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.host_apply import HostApplyService, _RollbackState
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import PlanItem, PlanOperation
from workspace_mcp_manager.registry import InstanceRegistry

from helpers import sample_v2_instance


@unittest.skipUnless(shutil.which("git"), "git is unavailable")
class Pm1HostApplyTests(unittest.TestCase):
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

    def test_repository_config_is_restored_by_transaction_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            self._git(repo, "init", "--quiet")
            self._git(repo, "config", "--local", "user.name", "Example Operator")
            self._git(repo, "config", "--local", "user.email", "operator@example.invalid")
            self._git(repo, "remote", "add", "origin", "git@github.com:owner/repo.git")
            config_path = repo / ".git/config"
            before = config_path.read_text(encoding="utf-8")

            raw = sample_v2_instance()
            raw["workspace_path"] = str(repo)
            raw["mcp"]["exec_path"] = "/usr/bin:/bin"
            raw["github"]["config_dir"] = str(root / ".config/workspace-mcp-manager/github/sample")
            raw["github"]["binary"] = "/bin/true"
            desired = DesiredInstance.from_dict(raw)
            paths = self._paths(root)
            service = HostApplyService(paths, InstanceRegistry(paths.registry_dir))
            operations = (
                PlanItem(PlanOperation.CREATE, "identity", "test", "dev-git-identity"),
                PlanItem(PlanOperation.CREATE, "transport", "test", "dev-git-transport"),
            )
            rollback = _RollbackState()
            journal_path = paths.state_root / "pm1-rollback.json"
            journal = {"attempted": [], "executed": []}
            service._write_journal(journal_path, journal)

            service._apply_development_operations(
                operations,
                desired,
                rollback,
                journal,
                journal_path,
            )
            self.assertNotEqual(config_path.read_text(encoding="utf-8"), before)
            rollback.restore()
            self.assertEqual(config_path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
