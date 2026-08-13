from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.codex_jobs import CodexJobManager, MAX_OUTPUT_READ_BYTES
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry

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


def write_fake_codex(path: Path, *, version_exit: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/python3
import os
import pathlib
import sys

if '--version' in sys.argv:
    print('codex-cli test-1.0')
    raise SystemExit(VERSION_EXIT)

args = sys.argv[1:]
prompt = sys.stdin.read()
sandbox = args[args.index('-s') + 1]
workspace = args[args.index('-C') + 1]
last = pathlib.Path(args[args.index('--output-last-message') + 1])
last.write_text(f'last sandbox={sandbox} prompt={prompt}', encoding='utf-8')
print(f'sandbox={sandbox}')
print(f'workspace={workspace}')
print(f'home={os.environ.get("HOME")}')
print(f'codex_home={os.environ.get("CODEX_HOME")}')
print(f'path={os.environ.get("PATH")}')
print(f'prompt={prompt}')
""".replace("VERSION_EXIT", str(version_exit)),
        encoding="utf-8",
    )
    os.chmod(path, 0o755)


def desired_for(root: Path, codex: Path) -> DesiredInstance:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    raw = sample_instance()
    raw["instance_id"] = "qual"
    raw["workspace_path"] = str(workspace)
    raw["mcp"]["binary"] = "/usr/bin/true"
    raw["mcp"]["exec_path"] = f"{codex.parent}:/usr/bin:/bin"
    raw["mcp"]["external_roots"] = [str(codex.parent.parent)]
    raw["tunnel"]["binary"] = "/usr/bin/true"
    raw["tunnel"]["id"] = "tunnel_qual123"
    raw["tunnel"]["profile"] = "workspace-mcp-qual"
    raw["tunnel"]["env_file"] = str(root / "runtime.env")
    raw["access"] = {"read_only": [], "read_write": []}
    raw["github"] = {"config_dir": str(root / ".config/gh")}
    raw["recovery"]["admission_guard_enabled"] = False
    return DesiredInstance.from_dict(raw)


def manager_for(root: Path, *, version_exit: int = 0) -> tuple[CodexJobManager, DesiredInstance, Path]:
    codex = root / "toolchain/bin/codex"
    write_fake_codex(codex, version_exit=version_exit)
    paths = paths_for(root)
    paths.manager_executable.parent.mkdir(parents=True, exist_ok=True)
    paths.manager_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(paths.manager_executable, 0o755)
    desired = desired_for(root, codex)
    registry = InstanceRegistry(paths.registry_dir)
    registry.create(desired)
    return CodexJobManager(paths, registry), desired, codex


class CodexJobTests(unittest.TestCase):
    def _start_without_real_systemd(
        self,
        manager: CodexJobManager,
        instance_id: str,
        *,
        mode: str,
        prompt: str,
    ) -> tuple[dict, list[list[str]]]:
        real_run = subprocess.run
        calls: list[list[str]] = []

        def run(argv, *args, **kwargs):
            calls.append(list(argv))
            if argv[0] == "systemd-run":
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real_run(argv, *args, **kwargs)

        with patch("workspace_mcp_manager.codex_jobs.subprocess.run", side_effect=run):
            result = manager.start(instance_id, mode=mode, prompt=prompt)
        return result, calls

    def test_start_resolves_configured_path_persists_request_and_detaches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, desired, codex = manager_for(root)
            result, calls = self._start_without_real_systemd(
                manager,
                "qual",
                mode="read",
                prompt="inspect without changes",
            )

            self.assertEqual(result["codex_path"], str(codex))
            self.assertEqual(result["codex_version"], "codex-cli test-1.0")
            self.assertEqual(result["state"], "queued")
            preflight = calls[0]
            launch = next(call for call in calls if call[0] == "systemd-run")
            self.assertEqual(preflight, [str(codex), "--version"])
            self.assertIn("--no-block", launch)
            self.assertIn("--collect", launch)
            self.assertNotIn("--wait", launch)
            self.assertIn("codex-worker", launch)
            self.assertNotIn("inspect without changes", " ".join(launch))

            job_dir = manager.jobs_root / "qual" / "codex" / result["job_id"]
            request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["instance_id"], "qual")
            self.assertEqual(request["workspace_path"], desired.workspace_path)
            self.assertEqual(request["mode"], "read")
            self.assertEqual(request["sandbox"], "read-only")
            self.assertEqual(request["codex_path"], str(codex))
            self.assertEqual(request["account_home"], str(root))
            self.assertEqual(request["codex_home"], str(root / ".codex"))
            self.assertEqual(request["exec_path"], desired.mcp.exec_path)
            self.assertEqual(request["prompt"], "inspect without changes")
            self.assertEqual(stat_mode(job_dir), 0o700)
            self.assertEqual(stat_mode(job_dir / "request.json"), 0o600)
            self.assertEqual(stat_mode(job_dir / "stdout.log"), 0o600)
            self.assertEqual(stat_mode(job_dir / "stderr.log"), 0o600)

    def test_read_and_write_workers_use_expected_sandbox_and_real_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, desired, _codex = manager_for(root)
            for mode, sandbox in (("read", "read-only"), ("write", "workspace-write")):
                started, _ = self._start_without_real_systemd(
                    manager,
                    "qual",
                    mode=mode,
                    prompt=f"prompt-{mode}",
                )
                manager.run_worker("qual", started["job_id"])
                status = manager.status("qual", started["job_id"])
                output = manager.output("qual", started["job_id"], limit_bytes=4096)
                self.assertEqual(status["state"], "succeeded")
                self.assertEqual(status["result"]["exit_code"], 0)
                self.assertIn(f"last sandbox={sandbox}", status["result"]["last_message"])
                stdout = output["stdout"]["content"]
                self.assertIn(f"sandbox={sandbox}", stdout)
                self.assertIn(f"workspace={desired.workspace_path}", stdout)
                self.assertIn(f"home={root}", stdout)
                self.assertIn(f"codex_home={root / '.codex'}", stdout)
                self.assertIn(f"path={desired.mcp.exec_path}", stdout)
                self.assertIn(f"prompt=prompt-{mode}", stdout)

    def test_output_is_bounded_tail_for_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _desired, _codex = manager_for(root)
            started, _ = self._start_without_real_systemd(manager, "qual", mode="read", prompt="x")
            job_dir = manager.jobs_root / "qual" / "codex" / started["job_id"]
            (job_dir / "stdout.log").write_bytes(b"0123456789abcdef")
            (job_dir / "stderr.log").write_bytes(b"UVWXYZ")
            first = manager.output("qual", started["job_id"], limit_bytes=5)
            self.assertEqual(first["stdout"]["content"], "bcdef")
            self.assertEqual(first["stderr"]["content"], "VWXYZ")
            self.assertTrue(first["stdout"]["truncated"])
            with self.assertRaisesRegex(ManagerError, "limit is out of range"):
                manager.output("qual", started["job_id"], limit_bytes=MAX_OUTPUT_READ_BYTES + 1)

    def test_request_workspace_tamper_fails_binding_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _desired, _codex = manager_for(root)
            started, _ = self._start_without_real_systemd(manager, "qual", mode="read", prompt="x")
            request_path = manager.jobs_root / "qual" / "codex" / started["job_id"] / "request.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            request["workspace_path"] = "/tmp/other"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaises(ManagerError) as caught:
                manager.status("qual", started["job_id"])
            self.assertEqual(caught.exception.code, ErrorCode.RECONCILIATION_CONFLICT)

    def test_request_execution_authority_tamper_fails_binding_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _desired, _codex = manager_for(root)
            started, _ = self._start_without_real_systemd(manager, "qual", mode="read", prompt="x")
            request_path = manager.jobs_root / "qual" / "codex" / started["job_id"] / "request.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            request["exec_path"] = "/tmp/untrusted:/usr/bin"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaises(ManagerError) as caught:
                manager.status("qual", started["job_id"])
            self.assertEqual(caught.exception.code, ErrorCode.RECONCILIATION_CONFLICT)

    def test_activating_unit_is_not_misclassified_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _desired, _codex = manager_for(root)
            started, _ = self._start_without_real_systemd(manager, "qual", mode="read", prompt="x")
            with patch.object(
                manager,
                "_unit_snapshot",
                return_value={"LoadState": "loaded", "ActiveState": "activating", "SubState": "start"},
            ):
                status = manager.status("qual", started["job_id"])
            self.assertEqual(status["state"], "queued")
            job_dir = manager.jobs_root / "qual" / "codex" / started["job_id"]
            self.assertFalse((job_dir / "result.json").exists())

    def test_missing_unit_without_result_reconciles_to_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _desired, _codex = manager_for(root)
            started, _ = self._start_without_real_systemd(manager, "qual", mode="read", prompt="x")
            job_dir = manager.jobs_root / "qual" / "codex" / started["job_id"]
            status_path = job_dir / "status.json"
            persisted_status = json.loads(status_path.read_text(encoding="utf-8"))
            persisted_status["created_at"] = "2000-01-01T00:00:00+00:00"
            status_path.write_text(json.dumps(persisted_status), encoding="utf-8")
            with patch.object(
                manager,
                "_unit_snapshot",
                return_value={"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead"},
            ):
                status = manager.status("qual", started["job_id"])
            self.assertEqual(status["state"], "interrupted")
            self.assertEqual(status["result"]["state"], "interrupted")
            persisted = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["state"], "interrupted")

    def test_execution_environment_drops_manager_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, desired, _codex = manager_for(root)
            with patch.dict(
                os.environ,
                {"CONTROL_PLANE_API_KEY": "must-not-forward", "GITHUB_TOKEN": "must-not-forward"},
                clear=False,
            ):
                env = manager._execution_env(desired)
            self.assertNotIn("CONTROL_PLANE_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertEqual(env["HOME"], str(root))
            self.assertEqual(env["CODEX_HOME"], str(root / ".codex"))

    def test_cancel_stops_unit_and_persists_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _desired, _codex = manager_for(root)
            started, _ = self._start_without_real_systemd(manager, "qual", mode="write", prompt="x")
            def run_command(argv, *args, **kwargs):
                if argv[0:3] == ["systemctl", "--user", "show"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0\n",
                        "",
                    )
                if argv[0:3] == ["systemctl", "--user", "stop"]:
                    return subprocess.CompletedProcess(argv, 0, "", "")
                raise AssertionError(argv)

            with patch("workspace_mcp_manager.codex_jobs.subprocess.run", side_effect=run_command) as run:
                result = manager.cancel("qual", started["job_id"])
            self.assertEqual(result["state"], "cancelled")
            self.assertFalse(result["already_terminal"])
            self.assertEqual(run.call_args.args[0][0:3], ["systemctl", "--user", "stop"])
            status = manager.status("qual", started["job_id"])
            self.assertEqual(status["state"], "cancelled")
            self.assertEqual(status["result"]["state"], "cancelled")

    def test_broken_executable_fails_preflight_before_job_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _desired, _codex = manager_for(root, version_exit=127)
            with self.assertRaises(ManagerError) as caught:
                manager.start("qual", mode="read", prompt="x")
            self.assertEqual(caught.exception.code, ErrorCode.HOST_INSPECTION_FAILED)
            self.assertFalse((manager.jobs_root / "qual").exists())


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
