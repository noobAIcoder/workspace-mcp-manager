from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.git_diagnostics import GitDiagnosticService, ProbeResult, _remote_metadata
from workspace_mcp_manager.paths import ManagerPaths

from tests.helpers import sample_instance


def paths_for(root: Path) -> ManagerPaths:
    return ManagerPaths(
        account_home=root / "real-home",
        config_root=root / "real-home/.config/workspace-mcp-manager",
        registry_dir=root / "real-home/.config/workspace-mcp-manager/instances",
        state_root=root / "real-home/.local/state/workspace-mcp-manager",
        user_unit_dir=root / "real-home/.config/systemd/user",
        tunnel_profile_dir=root / "real-home/.config/workspace-mcp-manager/tunnel-profiles",
        manager_executable=root / "real-home/.local/bin/workspace-mcp-manager",
    )


def desired_for(root: Path) -> DesiredInstance:
    raw = sample_instance()
    raw["workspace_path"] = str(root / "workspace")
    raw["access"] = {"read_only": [], "read_write": []}
    raw["mcp"]["exec_path"] = "/custom/node/bin:/usr/bin:/bin"
    raw["github"] = {"config_dir": str(root / "real-home/.config/gh")}
    raw["recovery"]["admission_guard_enabled"] = False
    return DesiredInstance.from_dict(raw)


def result(argv, code: int = 0, stdout: str = "", stderr: str = "") -> ProbeResult:
    return ProbeResult(tuple(argv), code, stdout, stderr)


class GitDiagnosticTests(unittest.TestCase):
    def test_uses_real_home_declared_path_and_nonsecret_remote_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            service = GitDiagnosticService(paths_for(root))
            observed_envs: list[dict[str, str]] = []

            def fake_run(argv, env, **_kwargs):
                observed_envs.append(dict(env))
                command = tuple(argv)
                if command[-2:] == ("rev-parse", "--is-inside-work-tree"):
                    return result(command, stdout="true\n")
                if "status" in command:
                    return result(command, stdout="")
                if command[-1] == "remote":
                    return result(command, stdout="origin\n")
                if "get-url" in command:
                    return result(command, stdout="https://secret-user:secret-token@github.com/acme/repo.git\n")
                if "ls-remote" in command:
                    return result(command, stdout="deadbeef\tHEAD\n")
                if command[-1] == "user.name":
                    return result(command, stdout="Operator\n")
                if command[-1] == "user.email":
                    return result(command, stdout="operator@example.test\n")
                if command[-3:] == ("auth", "status"):
                    return result(command)
                raise AssertionError(command)

            with patch.object(service, "_which", side_effect=lambda name, _desired: f"/usr/bin/{name}"), \
                 patch.object(service, "_run", side_effect=fake_run):
                payload = service.run(desired)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["environment"]["home"], str(root / "real-home"))
            self.assertEqual(payload["environment"]["path"], desired.mcp.exec_path)
            self.assertEqual(payload["remote"]["transport"], "https")
            self.assertEqual(payload["remote"]["host"], "github.com")
            self.assertNotIn("secret-token", repr(payload))
            self.assertTrue(observed_envs)
            for env in observed_envs:
                self.assertEqual(env["HOME"], str(root / "real-home"))
                self.assertEqual(env["PATH"], desired.mcp.exec_path)
                self.assertEqual(env["GH_CONFIG_DIR"], str(root / "real-home/.config/gh"))
                self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_remote_metadata_never_returns_userinfo_or_repository_path(self) -> None:
        self.assertEqual(
            _remote_metadata("ssh://name:secret@example.test/org/private.git"),
            {"transport": "ssh", "host": "example.test"},
        )
        self.assertEqual(
            _remote_metadata("git@example.test:org/private.git"),
            {"transport": "ssh", "host": "example.test"},
        )

    def test_missing_remote_and_identity_are_warnings_not_manager_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            service = GitDiagnosticService(paths_for(root))

            def fake_run(argv, _env, **_kwargs):
                command = tuple(argv)
                if command[-2:] == ("rev-parse", "--is-inside-work-tree"):
                    return result(command, stdout="true\n")
                if "status" in command:
                    return result(command, stdout="")
                if command[-1] == "remote":
                    return result(command, stdout="")
                if command[-1] in {"user.name", "user.email"}:
                    return result(command, code=1)
                raise AssertionError(command)

            with patch.object(
                service,
                "_which",
                side_effect=lambda name, _desired: "/usr/bin/git" if name == "git" else None,
            ), patch.object(service, "_run", side_effect=fake_run):
                payload = service.run(desired)

            self.assertTrue(payload["ok"])
            self.assertFalse(payload["remote"]["configured"])
            statuses = {item["name"]: item["status"] for item in payload["checks"]}
            self.assertEqual(statuses["git-remote-access"], "WARN")
            self.assertEqual(statuses["git-identity"], "WARN")
            self.assertEqual(statuses["github-cli-auth"], "WARN")

    def test_configured_remote_failure_fails_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            service = GitDiagnosticService(paths_for(root))

            def fake_run(argv, _env, **_kwargs):
                command = tuple(argv)
                if command[-2:] == ("rev-parse", "--is-inside-work-tree"):
                    return result(command, stdout="true\n")
                if "status" in command:
                    return result(command, stdout="")
                if command[-1] == "remote":
                    return result(command, stdout="origin\n")
                if "get-url" in command:
                    return result(command, stdout="git@github.com:acme/repo.git\n")
                if "ls-remote" in command:
                    return result(command, code=128, stderr="authentication failed")
                if command[-1] in {"user.name", "user.email"}:
                    return result(command, code=1)
                if command[-2:] == ("auth", "status"):
                    return result(command)
                raise AssertionError(command)

            with patch.object(service, "_which", side_effect=lambda name, _desired: f"/usr/bin/{name}"), \
                 patch.object(service, "_run", side_effect=fake_run):
                payload = service.run(desired)

            self.assertFalse(payload["ok"])
            self.assertFalse(payload["remote"]["reachable"])
            self.assertEqual(payload["remote"]["exit_code"], 128)
            self.assertNotIn("authentication failed", repr(payload))


if __name__ == "__main__":
    unittest.main()
