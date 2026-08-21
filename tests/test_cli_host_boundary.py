from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.cli import main
from workspace_mcp_manager.errors import ErrorCode, ManagerError
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
    def test_p7_1_session_continuity_uses_host_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_for(Path(directory))
            output = StringIO()
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(output):
                bridge_type.return_value.run_session_continuity.return_value = {
                    "ok": True,
                    "session_continuity_projection_version": 1,
                    "instance_id": "electrocad",
                    "result": "passed",
                }
                rc = main(["instance", "session-continuity", "electrocad"])
            self.assertEqual(rc, 0)
            bridge_type.return_value.run_session_continuity.assert_called_once_with("electrocad")
            self.assertEqual(json.loads(output.getvalue())["result"], "passed")

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

    def test_internal_runtime_semantic_false_is_transport_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            output = StringIO()
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli._run_runtime", return_value={"ok": False, "instance_id": "sample"}), \
                 redirect_stdout(output):
                rc = main(["_runtime", "status", "sample"])
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_internal_runtime_manager_error_is_structured_transport_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            output = StringIO()
            failure = ManagerError(
                ErrorCode.IO_ERROR,
                "synthetic runtime failure",
                {"unit": "tunnel-client-sample.service", "unit_status": {"ActiveState": "failed"}},
            )
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli._run_runtime", side_effect=failure), \
                 redirect_stdout(output):
                rc = main(["_runtime", "status", "sample"])
            self.assertEqual(rc, 0)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "IO_ERROR")
            self.assertEqual(payload["error"]["details"]["unit"], "tunnel-client-sample.service")
            self.assertEqual(payload["error"]["details"]["unit_status"]["ActiveState"], "failed")

    def test_service_runtime_manager_error_remains_process_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            error_output = StringIO()
            failure = ManagerError(
                ErrorCode.IO_ERROR,
                "synthetic tunnel runtime failure",
                {"profile": "workspace-mcp-sample"},
            )
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli._run_runtime", side_effect=failure), \
                 redirect_stderr(error_output):
                rc = main(["_runtime", "tunnel", "sample"])
            self.assertEqual(rc, 2)
            payload = json.loads(error_output.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "IO_ERROR")
            self.assertEqual(payload["error"]["details"]["profile"], "workspace-mcp-sample")

    def test_public_status_semantic_false_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            output = StringIO()
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(output):
                bridge_type.return_value.run_status.return_value = {"ok": False, "instance_id": "sample"}
                rc = main(["instance", "status", "sample"])
            self.assertEqual(rc, 1)
            self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_list_show_render_and_git_use_host_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type:
                bridge = bridge_type.return_value
                bridge.run_list.return_value = {"ok": True, "instances": []}
                bridge.run_show.return_value = {"ok": True, "instance_id": "sample"}
                bridge.run_render.return_value = {"ok": True, "instance_id": "sample", "resources": []}
                bridge.run_git.return_value = {"ok": True, "instance_id": "sample", "repository": {"present": True}}
                bridge.run_diagnose.return_value = {"ok": True, "instance_id": "sample", "snapshot": {}}
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["instance", "list"]), 0)
                    self.assertEqual(main(["instance", "show", "sample"]), 0)
                    self.assertEqual(main(["instance", "render", "sample"]), 0)
                    self.assertEqual(main(["instance", "git", "sample"]), 0)
                    self.assertEqual(main(["instance", "diagnose", "sample", "--since-seconds", "77"]), 0)
                bridge.run_list.assert_called_once_with()
                bridge.run_show.assert_called_once_with("sample")
                bridge.run_render.assert_called_once_with("sample", include_content=False)
                bridge.run_git.assert_called_once_with("sample")
                bridge.run_diagnose.assert_called_once_with("sample", since_seconds=77)

    def test_pm3_1_public_setup_projections_use_host_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            request = {
                "candidate_request_version": 1,
                "workspace_path": "/home/operator/repos/example",
                "field_edits": [],
                "access_edits": [],
            }
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(StringIO()):
                bridge = bridge_type.return_value
                bridge.run_discover.return_value = {"ok": True, "workspace_path": request["workspace_path"]}
                bridge.run_candidate.return_value = {"ok": True, "candidate_version": 1}
                bridge.run_ports.return_value = {"ok": True, "port_projection_version": 1}
                self.assertEqual(main(["instance", "discover", request["workspace_path"]]), 0)
                with patch("sys.stdin", StringIO(json.dumps(request))):
                    self.assertEqual(main(["instance", "candidate"]), 0)
                self.assertEqual(main(["host", "ports"]), 0)
            bridge.run_discover.assert_called_once_with(request["workspace_path"])
            bridge.run_candidate.assert_called_once_with(request)
            bridge.run_ports.assert_called_once_with()

    def test_pm3_1_1_status_verify_use_host_bridge_but_configure_is_foreground_direct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 patch("workspace_mcp_manager.cli.GithubAccessService") as github_type, \
                 redirect_stdout(StringIO()):
                bridge = bridge_type.return_value
                bridge.run_github_access.return_value = {"ok": True, "github_access_projection_version": 1}
                github_type.return_value.configure.return_value = {
                    "ok": True,
                    "instance_id": "sample",
                    "operation": {"outcome": "succeeded", "reason_code": None},
                }
                self.assertEqual(main(["instance", "github-access", "status", "sample"]), 0)
                self.assertEqual(main(["instance", "github-access", "verify", "sample"]), 0)
                self.assertEqual(main(["instance", "github-access", "configure", "sample"]), 0)
            self.assertEqual(
                bridge.run_github_access.call_args_list,
                [unittest.mock.call("status", "sample"), unittest.mock.call("verify", "sample")],
            )
            github_type.return_value.configure.assert_called_once_with("sample")

    def test_public_reboot_commands_use_host_bridge_and_reason_stdin_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(StringIO()):
                bridge = bridge_type.return_value
                bridge.run_reboot.return_value = {"ok": False, "state": "authorization_failed"}
                bridge.run_reboot_check.return_value = {"ok": False, "state": "authorization_failed"}
                self.assertEqual(main(["host", "reboot", "--reason", "maintenance"]), 1)
                self.assertEqual(main(["host", "reboot-check"]), 1)
            bridge.run_reboot.assert_called_once_with(reason="maintenance")
            bridge.run_reboot_check.assert_called_once_with()

    def test_public_host_tools_use_generic_host_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(StringIO()):
                bridge = bridge_type.return_value
                bridge.run_tooling.return_value = {"ok": True}
                self.assertEqual(main(["host", "tools", "audit"]), 0)
                self.assertEqual(main(["host", "tools", "agents", "audit"]), 0)
                self.assertEqual(
                    main(
                        [
                            "host",
                            "tools",
                            "codex",
                            "--node-root",
                            "/home/operator/.nvm/versions/node/v24.15.0",
                            "--version",
                            "0.147.0",
                        ]
                    ),
                    0,
                )
                self.assertEqual(main(["host", "tools", "gh"]), 0)
            self.assertEqual(
                bridge.run_tooling.call_args_list,
                [
                    unittest.mock.call(["_runtime", "tools-audit"], unit_fragment="tools-audit"),
                    unittest.mock.call(
                        ["_runtime", "tools-agents", "audit"],
                        unit_fragment="tools-agents-audit",
                    ),
                    unittest.mock.call(
                        [
                            "_runtime",
                            "tools-codex",
                            "/home/operator/.nvm/versions/node/v24.15.0",
                            "--version",
                            "0.147.0",
                        ],
                        unit_fragment="tools-codex",
                    ),
                    unittest.mock.call(["_runtime", "tools-gh"], unit_fragment="tools-gh"),
                ],
            )

    def test_public_cleanup_commands_use_host_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(StringIO()):
                bridge = bridge_type.return_value
                bridge.run_cleanup.return_value = {"ok": True, "action": "audit", "candidates": []}
                self.assertEqual(main(["cleanup", "audit"]), 0)
                bridge.run_cleanup.return_value = {"ok": True, "action": "apply", "final": {"state": "clean"}}
                self.assertEqual(main(["cleanup", "execute", "legacy-qual"]), 0)
            self.assertEqual(
                bridge.run_cleanup.call_args_list,
                [
                    unittest.mock.call("audit", instance_id=None),
                    unittest.mock.call("execute", instance_id="legacy-qual"),
                ],
            )

    def test_public_codex_commands_use_host_bridge_and_prompt_stdin_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli.HostExecutionBridge") as bridge_type, \
                 redirect_stdout(StringIO()):
                bridge = bridge_type.return_value
                bridge.run_codex_start.return_value = {"ok": True, "job_id": "job"}
                bridge.run_codex_status.return_value = {"ok": True, "job_id": "job", "state": "running"}
                bridge.run_codex_output.return_value = {"ok": True, "job_id": "job", "content": "x"}
                bridge.run_codex_cancel.return_value = {"ok": True, "job_id": "job", "state": "cancelled"}
                self.assertEqual(main(["codex", "start", "sample", "--mode", "read", "--prompt", "inspect"]), 0)
                self.assertEqual(main(["codex", "status", "sample", "job"]), 0)
                self.assertEqual(main(["codex", "output", "sample", "job", "--limit-bytes", "9"]), 0)
                self.assertEqual(main(["codex", "cancel", "sample", "job"]), 0)
            bridge.run_codex_start.assert_called_once_with("read", "sample", prompt="inspect")
            bridge.run_codex_status.assert_called_once_with("sample", "job")
            bridge.run_codex_output.assert_called_once_with("sample", "job", limit_bytes=9)
            bridge.run_codex_cancel.assert_called_once_with("sample", "job")

    def test_codex_worker_runtime_error_remains_process_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            output = StringIO()
            failure = ManagerError(ErrorCode.IO_ERROR, "synthetic codex worker failure")
            with patch("workspace_mcp_manager.cli.ManagerPaths.for_current_user", return_value=paths), \
                 patch("workspace_mcp_manager.cli._run_runtime", side_effect=failure), \
                 redirect_stderr(output):
                rc = main(["_runtime", "codex-worker", "sample", "20260813T010203Z-abcdef123456"])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "IO_ERROR")


if __name__ == "__main__":
    unittest.main()
