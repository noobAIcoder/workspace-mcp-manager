from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.session_continuity import (
    CLIENT_COMPATIBILITY_PROJECTION_VERSION,
    SESSION_CONTINUITY_PROJECTION_VERSION,
    SessionContinuityService,
)

from tests.helpers import sample_v2_instance


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


def desired(root: Path) -> DesiredInstance:
    workspace = root / "workspace"
    (workspace / "docs").mkdir(parents=True)
    raw = sample_v2_instance()
    raw["instance_id"] = "sample"
    raw["workspace_path"] = str(workspace)
    raw["lifecycle"] = {"deployment": "present", "runtime": "running"}
    raw["access"] = {"read_only": [], "read_write": []}
    raw["git"] = {"identity": None, "remote": None}
    raw["github"] = {"mode": "disabled", "config_dir": None, "binary": None}
    raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
    return DesiredInstance.from_dict(raw)


class SessionContinuityTests(unittest.TestCase):
    def test_projection_persists_only_session_fingerprint_and_pass_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired(root))
            service = SessionContinuityService(paths, registry)
            raw_session = "session-secret-shaped-control-id"
            fresh_session = "fresh-session-secret-shaped-control-id"
            initialize_count = 0

            def request(_url, payload, *, session_id=None, method="POST"):
                nonlocal initialize_count
                if method == "DELETE":
                    self.assertIn(session_id, {raw_session, fresh_session})
                    return {}, None, 204
                if payload and payload.get("method") == "initialize":
                    initialize_count += 1
                    return {
                        "result": {
                            "protocolVersion": "2025-11-25",
                            "serverInfo": {"name": "coding-tools-mcp", "version": "0.2.2"},
                        }
                    }, raw_session if initialize_count == 1 else fresh_session, 200
                return {}, None, 202

            def tool(_url, session_id, _request_id, name, arguments):
                if name == "set_default_cwd":
                    self.assertEqual(session_id, raw_session)
                    return {"ok": True, "default_cwd": "docs"}
                if name == "get_default_cwd":
                    return {"ok": True, "default_cwd": "docs" if session_id == raw_session else "."}
                if name == "exec_command":
                    self.assertEqual(session_id, raw_session)
                    return {
                        "ok": True,
                        "status": "running",
                        "session_id": "process-session",
                        "output_ref": "session:process-session:stdout",
                    }
                if name == "write_stdin":
                    self.assertEqual(session_id, raw_session)
                    return {"ok": True, "status": "running"}
                if name == "read_output":
                    self.assertEqual(session_id, raw_session)
                    return {"ok": True, "content": "p71-session-continuity\n"}
                if name == "kill_session":
                    self.assertEqual(session_id, raw_session)
                    return {"ok": True, "status": "terminated"}
                raise AssertionError((name, arguments))

            def tool_result(_url, session_id, _request_id, name, _arguments):
                self.assertEqual(session_id, fresh_session)
                self.assertIn(name, {"write_stdin", "read_output", "kill_session"})
                return {
                    "ok": False,
                    "error": {"code": "SESSION_NOT_FOUND", "message": "Session not found"},
                }, False

            with mock.patch.object(service, "_request", side_effect=request), \
                 mock.patch.object(service, "_tool", side_effect=tool), \
                 mock.patch.object(service, "_tool_result", side_effect=tool_result):
                payload = service.run("sample")
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["atomic_coding_ready"])
            self.assertEqual(
                payload["client_compatibility_projection_version"],
                CLIENT_COMPATIBILITY_PROJECTION_VERSION,
            )
            self.assertEqual(payload["session_continuity_projection_version"], SESSION_CONTINUITY_PROJECTION_VERSION)
            self.assertEqual(payload["default_cwd"], "passed")
            self.assertEqual(payload["command_session"], "passed")
            self.assertEqual(payload["cleanup"], "passed")
            self.assertEqual(payload["result"], "passed_with_limitations")
            self.assertEqual(payload["protocol_session_persistence"], "not_required_for_atomic_operations")
            self.assertEqual(payload["cross_session_state"]["default_cwd"], "session_scoped_optional")
            self.assertEqual(payload["cross_session_state"]["command_handles"], "session_scoped")
            self.assertFalse(payload["recommended_usage"]["cross_call_command_interaction"])
            self.assertEqual(len(payload["session_fingerprint"]), 12)
            self.assertNotIn(raw_session, json.dumps(payload, sort_keys=True))
            self.assertNotIn(fresh_session, json.dumps(payload, sort_keys=True))

    def test_missing_test_subdirectory_is_unavailable_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            item = desired(root)
            for child in (Path(item.workspace_path) / "docs",):
                child.rmdir()
            registry.create(item)
            service = SessionContinuityService(paths, registry)
            with mock.patch.object(service, "_request", side_effect=AssertionError("network")):
                payload = service.run("sample")
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["reason_code"], "NO_TEST_SUBDIRECTORY")


if __name__ == "__main__":
    unittest.main()
