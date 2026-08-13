from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.runtime import (
    ADMISSION_RECOVERY_STATE_FILE,
    SATURATION_BODY,
    _probe_admission,
    run_admission_guard,
)

from tests.helpers import sample_instance


class FakeResponse:
    def __init__(self, status: int, *, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self._body


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


def desired_for(root: Path, *, enabled: bool = True) -> DesiredInstance:
    raw = sample_instance()
    raw["instance_id"] = "qual"
    raw["workspace_path"] = str(root / "workspace")
    raw["recovery"] = {
        "admission_guard_enabled": enabled,
        "guard_interval_seconds": 30,
        "recovery_cooldown_seconds": 120,
    }
    return DesiredInstance.from_dict(raw)


def exact_observation() -> dict:
    return {
        "classification": "saturation",
        "http_status": 503,
        "exact_signature": True,
        "session_cleanup": None,
    }


class AdmissionRecoveryTests(unittest.TestCase):
    def test_healthy_probe_deletes_created_session(self) -> None:
        desired = desired_for(Path("/tmp"))
        calls = []

        def urlopen(request, timeout=0):
            calls.append((request, timeout))
            if len(calls) == 1:
                return FakeResponse(200, headers={"Mcp-Session-Id": "session-1"}, body=b"{}")
            return FakeResponse(200)

        with patch("workspace_mcp_manager.runtime.urllib.request.urlopen", side_effect=urlopen):
            result = _probe_admission(desired)

        self.assertEqual(result["classification"], "healthy")
        self.assertEqual(result["session_cleanup"], "deleted")
        self.assertFalse(result["exact_signature"])
        self.assertEqual(calls[0][0].get_method(), "POST")
        self.assertEqual(calls[1][0].get_method(), "DELETE")
        self.assertEqual(calls[1][0].headers["Mcp-session-id"], "session-1")

    def test_probe_matches_only_exact_503_signature(self) -> None:
        desired = desired_for(Path("/tmp"))
        exact = urllib.error.HTTPError(
            "http://127.0.0.1:8765/mcp",
            503,
            "busy",
            {},
            io.BytesIO((SATURATION_BODY + "\n").encode("utf-8")),
        )
        with patch("workspace_mcp_manager.runtime.urllib.request.urlopen", side_effect=exact):
            result = _probe_admission(desired)
        self.assertEqual(result["classification"], "saturation")
        self.assertTrue(result["exact_signature"])

        near = urllib.error.HTTPError(
            "http://127.0.0.1:8765/mcp",
            503,
            "busy",
            {},
            io.BytesIO((SATURATION_BODY + "!").encode("utf-8")),
        )
        with patch("workspace_mcp_manager.runtime.urllib.request.urlopen", side_effect=near):
            result = _probe_admission(desired)
        self.assertEqual(result["classification"], "http_error")
        self.assertFalse(result["exact_signature"])

    def test_probe_does_not_match_same_body_under_other_status(self) -> None:
        desired = desired_for(Path("/tmp"))
        failure = urllib.error.HTTPError(
            "http://127.0.0.1:8765/mcp",
            502,
            "bad gateway",
            {},
            io.BytesIO(SATURATION_BODY.encode("utf-8")),
        )
        with patch("workspace_mcp_manager.runtime.urllib.request.urlopen", side_effect=failure):
            result = _probe_admission(desired)
        self.assertEqual(result["http_status"], 502)
        self.assertFalse(result["exact_signature"])

    def test_exact_match_restarts_only_mcp_and_persists_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            paths = paths_for(root)
            now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
            completed = subprocess.CompletedProcess([], 0, "", "")
            with patch("workspace_mcp_manager.runtime._probe_admission", return_value=exact_observation()), \
                 patch("workspace_mcp_manager.runtime._restart_mcp", return_value=completed) as restart, \
                 patch("workspace_mcp_manager.runtime._utc_now", return_value=now):
                run_admission_guard(desired, paths)

            restart.assert_called_once_with(desired)
            state_path = paths.state_root / "instances/qual" / ADMISSION_RECOVERY_STATE_FILE
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["saturation_detection_count"], 1)
            self.assertEqual(state["restart_attempt_count"], 1)
            self.assertTrue(state["last_restart_result"]["ok"])
            self.assertEqual(state["last_restart_result"]["unit"], "coding-tools-mcp-qual.service")
            self.assertEqual(state["last_saturation_signature"], f"503 {SATURATION_BODY}")
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("response_body", state_path.read_text(encoding="utf-8"))

    def test_exact_match_inside_cooldown_does_not_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            paths = paths_for(root)
            state_dir = paths.state_root / "instances/qual"
            state_dir.mkdir(parents=True)
            now = datetime(2026, 8, 13, 9, 1, tzinfo=timezone.utc)
            state = {
                "schema_version": 1,
                "instance_id": "qual",
                "guard_interval_seconds": 30,
                "recovery_cooldown_seconds": 120,
                "saturation_detection_count": 1,
                "cooldown_suppression_count": 0,
                "restart_attempt_count": 1,
                "last_observation": None,
                "last_restart_attempt_at": (now - timedelta(seconds=30)).isoformat(),
                "last_restart_result": {"ok": True, "exit_code": 0},
                "cooldown_until": None,
            }
            (state_dir / ADMISSION_RECOVERY_STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
            with patch("workspace_mcp_manager.runtime._probe_admission", return_value=exact_observation()), \
                 patch("workspace_mcp_manager.runtime._restart_mcp") as restart, \
                 patch("workspace_mcp_manager.runtime._utc_now", return_value=now):
                run_admission_guard(desired, paths)
            restart.assert_not_called()
            persisted = json.loads((state_dir / ADMISSION_RECOVERY_STATE_FILE).read_text(encoding="utf-8"))
            self.assertEqual(persisted["restart_attempt_count"], 1)
            self.assertEqual(persisted["saturation_detection_count"], 2)
            self.assertEqual(persisted["cooldown_suppression_count"], 1)
            self.assertEqual(persisted["last_observation"]["classification"], "cooldown")

    def test_failed_restart_attempt_still_establishes_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            paths = paths_for(root)
            first = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
            failure = subprocess.CompletedProcess([], 1, "", "failed")
            with patch("workspace_mcp_manager.runtime._probe_admission", return_value=exact_observation()), \
                 patch("workspace_mcp_manager.runtime._restart_mcp", return_value=failure), \
                 patch("workspace_mcp_manager.runtime._utc_now", return_value=first):
                with self.assertRaises(ManagerError) as caught:
                    run_admission_guard(desired, paths)
            self.assertEqual(caught.exception.code, ErrorCode.IO_ERROR)

            second = first + timedelta(seconds=30)
            with patch("workspace_mcp_manager.runtime._probe_admission", return_value=exact_observation()), \
                 patch("workspace_mcp_manager.runtime._restart_mcp") as restart, \
                 patch("workspace_mcp_manager.runtime._utc_now", return_value=second):
                run_admission_guard(desired, paths)
            restart.assert_not_called()

    def test_future_restart_timestamp_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            paths = paths_for(root)
            state_dir = paths.state_root / "instances/qual"
            state_dir.mkdir(parents=True)
            now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
            state = {
                "schema_version": 1,
                "instance_id": "qual",
                "guard_interval_seconds": 30,
                "recovery_cooldown_seconds": 120,
                "saturation_detection_count": 0,
                "cooldown_suppression_count": 0,
                "restart_attempt_count": 1,
                "last_observation": None,
                "last_restart_attempt_at": (now + timedelta(seconds=1)).isoformat(),
                "last_restart_result": None,
                "cooldown_until": None,
            }
            (state_dir / ADMISSION_RECOVERY_STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
            with patch("workspace_mcp_manager.runtime._probe_admission", return_value=exact_observation()), \
                 patch("workspace_mcp_manager.runtime._restart_mcp") as restart, \
                 patch("workspace_mcp_manager.runtime._utc_now", return_value=now):
                with self.assertRaises(ManagerError) as caught:
                    run_admission_guard(desired, paths)
            self.assertEqual(caught.exception.code, ErrorCode.RECONCILIATION_CONFLICT)
            restart.assert_not_called()

    def test_nonmatch_and_disabled_guard_never_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = desired_for(root)
            observation = {
                "classification": "probe_error",
                "http_status": None,
                "exact_signature": False,
                "session_cleanup": None,
                "error_type": "TimeoutError",
            }
            with patch("workspace_mcp_manager.runtime._probe_admission", return_value=observation), \
                 patch("workspace_mcp_manager.runtime._restart_mcp") as restart:
                run_admission_guard(desired, paths)
            restart.assert_not_called()

            disabled = desired_for(root, enabled=False)
            with patch("workspace_mcp_manager.runtime._probe_admission") as probe, \
                 patch("workspace_mcp_manager.runtime._restart_mcp") as restart:
                run_admission_guard(disabled, paths)
            probe.assert_not_called()
            restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
