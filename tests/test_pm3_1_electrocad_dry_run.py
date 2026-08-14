from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workspace_mcp_manager.development_environment import (
    REMOTE_OWNER_KEY,
    DevObservationStatus,
    DevelopmentEnvironmentReconciler,
)
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.endpoint_projection import ListenerObservation, ListenerState
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.setup_projection import PortProjectionService, SetupProjectionService


def _paths(root: Path) -> ManagerPaths:
    return ManagerPaths(
        account_home=root,
        config_root=root / ".config/workspace-mcp-manager",
        registry_dir=root / ".config/workspace-mcp-manager/instances",
        state_root=root / ".local/state/workspace-mcp-manager",
        user_unit_dir=root / ".config/systemd/user",
        tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
        manager_executable=root / ".local/bin/workspace-mcp-manager",
    )


@unittest.skipUnless(shutil.which("git"), "git is unavailable")
class ElectroCadDryRunTests(unittest.TestCase):
    """Dry-run the PM3.1 candidate path against ElectroCAD's Git transport shape."""

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

    def test_https_raw_remote_rewritten_to_ssh_dry_runs_without_git_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repos/ElectroCAD"
            repo.mkdir(parents=True)
            self._git(repo, "init", "--quiet")
            raw_url = "https://github.com/noobAIcoder/ElectroCAD.git"
            self._git(repo, "remote", "add", "origin", raw_url)
            (root / ".gitconfig").write_text(
                '[url "git@github.com:"]\n\tinsteadOf = https://github.com/\n',
                encoding="utf-8",
            )

            socket_path = root / "ssh-agent.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as agent_socket:
                agent_socket.bind(str(socket_path))
                paths = _paths(root)
                registry = InstanceRegistry(paths.registry_dir)
                ports = PortProjectionService(
                    registry,
                    listener_observation=ListenerObservation(ListenerState.AVAILABLE, ()),
                )
                request = {
                    "candidate_request_version": 1,
                    "workspace_path": str(repo),
                    "field_edits": [
                        {"path": "/instance_id", "operation": "set", "value": "electrocad"},
                        {
                            "path": "/tunnel/id",
                            "operation": "set",
                            "value": "tunnel_ElectroCADDryRun",
                        },
                    ],
                    "access_edits": [],
                }
                with mock.patch.dict(os.environ, {"SSH_AUTH_SOCK": str(socket_path)}):
                    candidate = SetupProjectionService(paths, registry, ports=ports).candidate(request)

            desired = DesiredInstance.from_dict(candidate["effective_declaration"])
            self.assertIsNotNone(desired.git.remote)
            assert desired.git.remote is not None
            self.assertEqual(desired.git.remote.protocol.value, "ssh")
            self.assertEqual(candidate["discovery"]["local_git"]["remote"]["transport"], "ssh")

            observation = DevelopmentEnvironmentReconciler(paths).observe_transport(desired)
            self.assertEqual(observation.status, DevObservationStatus.ABSENT)
            self.assertNotIn("differs from desired state", observation.detail)

            # The dry run must not mutate the repository or claim ownership.
            self.assertEqual(self._git(repo, "config", "--local", "--get", "remote.origin.url"), raw_url)
            self.assertEqual(
                self._git(repo, "config", "--local", "--get", REMOTE_OWNER_KEY, check=False),
                "",
            )


if __name__ == "__main__":
    unittest.main()
