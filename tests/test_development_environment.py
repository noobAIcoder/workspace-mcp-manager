from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.development_environment import (
    CORE_SSH_COMMAND_KEY,
    HTTPS_HELPER_KEY,
    IDENTITY_OWNER_KEY,
    REMOTE_NAME_KEY,
    REMOTE_ORIGINAL_URL_KEY,
    REMOTE_OWNER_KEY,
    DevObservationStatus,
    DevelopmentEnvironmentReconciler,
    github_cli_helper_path,
    validate_pm1_host_preconditions,
)
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.paths import ManagerPaths

from helpers import sample_v2_instance


@unittest.skipUnless(shutil.which("git"), "git is unavailable")
class DevelopmentEnvironmentTests(unittest.TestCase):
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

    def _git(self, repo: Path, *args: str, check: bool = True) -> str:
        completed = subprocess.run(
            [shutil.which("git") or "git", "-C", str(repo), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            self.fail(completed.stderr or completed.stdout)
        return completed.stdout.rstrip("\n")

    def _desired(self, root: Path, repo: Path, *, protocol: str = "ssh") -> DesiredInstance:
        raw = sample_v2_instance()
        raw["workspace_path"] = str(repo)
        raw["mcp"]["exec_path"] = "/usr/bin:/bin"
        raw["github"]["config_dir"] = str(
            root / ".config/workspace-mcp-manager/github/sample"
        )
        raw["github"]["binary"] = "/bin/true"
        raw["git"]["remote"]["protocol"] = protocol
        return DesiredInstance.from_dict(raw)

    def _init_repo(self, repo: Path) -> None:
        repo.mkdir()
        self._git(repo, "init", "--quiet")

    def test_identity_and_transport_are_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            self._init_repo(repo)
            self._git(repo, "config", "--local", "user.name", "Example Operator")
            self._git(repo, "config", "--local", "user.email", "operator@example.invalid")
            self._git(repo, "remote", "add", "origin", "git@github.com:owner/repo.git")

            desired = self._desired(root, repo)
            reconciler = DevelopmentEnvironmentReconciler(self._paths(root))
            self.assertEqual(reconciler.observe_identity(desired).status, DevObservationStatus.ABSENT)
            self.assertEqual(reconciler.observe_transport(desired).status, DevObservationStatus.ABSENT)

            reconciler.converge_identity(desired)
            reconciler.converge_transport(desired)
            self.assertEqual(reconciler.observe_identity(desired).status, DevObservationStatus.EXACT)
            self.assertEqual(reconciler.observe_transport(desired).status, DevObservationStatus.EXACT)
            self.assertEqual(self._git(repo, "config", "--local", "--get", IDENTITY_OWNER_KEY), "sample")
            self.assertEqual(self._git(repo, "config", "--local", "--get", REMOTE_OWNER_KEY), "sample")
            self.assertEqual(self._git(repo, "config", "--local", "--get", REMOTE_NAME_KEY), "origin")
            self.assertEqual(
                self._git(repo, "config", "--local", "--get", REMOTE_ORIGINAL_URL_KEY),
                "git@github.com:owner/repo.git",
            )
            self.assertIn("IdentityAgent=", self._git(repo, "config", "--local", "--get", CORE_SSH_COMMAND_KEY))

            https_raw = desired.to_dict()
            https_raw["git"]["remote"]["protocol"] = "https-gh"
            https = DesiredInstance.from_dict(https_raw)
            self.assertEqual(reconciler.observe_transport(https).status, DevObservationStatus.DIFFERENT)
            reconciler.converge_transport(https)
            self.assertEqual(
                self._git(repo, "config", "--local", "--get", "remote.origin.url"),
                "https://github.com/owner/repo.git",
            )
            self.assertEqual(
                self._git(repo, "config", "--local", "--get", HTTPS_HELPER_KEY),
                str(github_cli_helper_path(self._paths(root), https)),
            )
            self.assertEqual(self._git(repo, "config", "--local", "--get", CORE_SSH_COMMAND_KEY, check=False), "")

            changed_raw = https.to_dict()
            changed_raw["git"]["identity"]["name"] = "Changed Operator"
            changed = DesiredInstance.from_dict(changed_raw)
            self.assertEqual(reconciler.observe_identity(changed).status, DevObservationStatus.DIFFERENT)
            reconciler.converge_identity(changed)

            absent_raw = changed.to_dict()
            absent_raw["lifecycle"] = {"deployment": "absent", "runtime": "stopped"}
            absent = DesiredInstance.from_dict(absent_raw)
            reconciler.converge_identity(absent)
            reconciler.converge_transport(absent)
            self.assertEqual(self._git(repo, "config", "--local", "--get", "user.name"), "Example Operator")
            self.assertEqual(self._git(repo, "config", "--local", "--get", "user.email"), "operator@example.invalid")
            self.assertEqual(
                self._git(repo, "config", "--local", "--get", "remote.origin.url"),
                "git@github.com:owner/repo.git",
            )
            self.assertEqual(self._git(repo, "config", "--local", "--get", IDENTITY_OWNER_KEY, check=False), "")
            self.assertEqual(self._git(repo, "config", "--local", "--get", REMOTE_OWNER_KEY, check=False), "")

    def test_created_identity_is_removed_on_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            self._init_repo(repo)
            desired = self._desired(root, repo)
            reconciler = DevelopmentEnvironmentReconciler(self._paths(root))
            reconciler.converge_identity(desired)
            absent_raw = desired.to_dict()
            absent_raw["lifecycle"] = {"deployment": "absent", "runtime": "stopped"}
            reconciler.converge_identity(DesiredInstance.from_dict(absent_raw))
            self.assertEqual(self._git(repo, "config", "--local", "--get", "user.name", check=False), "")
            self.assertEqual(self._git(repo, "config", "--local", "--get", "user.email", check=False), "")

    def test_foreign_identity_and_transport_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            self._init_repo(repo)
            self._git(repo, "config", "--local", "user.name", "Someone Else")
            self._git(repo, "config", "--local", "user.email", "other@example.invalid")
            self._git(repo, "remote", "add", "origin", "https://github.com/owner/repo.git")
            desired = self._desired(root, repo, protocol="ssh")
            reconciler = DevelopmentEnvironmentReconciler(self._paths(root))
            self.assertEqual(reconciler.observe_identity(desired).status, DevObservationStatus.FOREIGN)
            self.assertEqual(reconciler.observe_transport(desired).status, DevObservationStatus.FOREIGN)

    def test_external_agent_precondition_requires_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            self._init_repo(repo)
            socket_path = root / "agent.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                raw = self._desired(root, repo).to_dict()
                raw["agent"] = {"mode": "external", "ssh_auth_sock": str(socket_path)}
                desired = DesiredInstance.from_dict(raw)
                problems = validate_pm1_host_preconditions(self._paths(root), desired)
                self.assertFalse(any("SSH_AUTH_SOCK" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
