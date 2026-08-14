from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.tui import (
    GenerationGate,
    ManagerClient,
    ManagerInvocation,
    MutationCoordinator,
    SETTINGS_FIELDS,
    TuiError,
    TuiOutcomeUnknown,
    apply_settings_values,
    clip_text,
    dashboard_snapshot,
    default_manager_executable,
    get_nested,
    project_v1_to_v2,
    semantic_plan_diff,
    set_nested,
)

from tests.helpers import sample_instance


class ManagerClientTests(unittest.TestCase):
    def test_cli_version_is_read_from_manager_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "workspace-mcp-manager"
            executable.write_text(
                "#!/usr/bin/env bash\nprintf 'workspace-mcp-manager 7.6.5\\n'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            client = ManagerClient(str(executable))
            self.assertEqual(client.cli_version(), "7.6.5")

    def test_argv_uses_public_manager_and_registry_override(self) -> None:
        client = ManagerClient("/opt/bin/workspace-mcp-manager", registry_dir=Path("/tmp/registry"))
        self.assertEqual(
            client.argv("instance", "summaries"),
            (
                "/opt/bin/workspace-mcp-manager",
                "--registry-dir",
                "/tmp/registry",
                "instance",
                "summaries",
            ),
        )

    def test_structured_manager_error_is_preserved(self) -> None:
        error = {
            "ok": False,
            "error": {"code": "STALE_STATE", "message": "stale declaration", "details": {}},
        }
        client = ManagerClient("manager")
        with patch.object(client, "_invoke_process", return_value=(1, "", json.dumps(error))):
            with self.assertRaises(TuiError) as caught:
                client.invoke("instance", "summary", "sample", require_ok=True)
        self.assertEqual(caught.exception.payload, error)
        self.assertIn("STALE_STATE", str(caught.exception))

    def test_public_mutation_mappings_include_pm3_preconditions(self) -> None:
        class RecordingClient(ManagerClient):
            def __init__(self) -> None:
                super().__init__("manager")
                self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

            def invoke(self, *args: str, **kwargs):  # type: ignore[override]
                self.calls.append((tuple(args), dict(kwargs)))
                return ManagerInvocation(tuple(args), 0, {"ok": True})

        client = RecordingClient()
        client.apply("sample", expected_plan_fingerprint="a" * 64)
        client.access_add(
            "sample",
            mode="ro",
            alias="docs",
            path="/srv/docs",
            expected_current_fingerprint="b" * 64,
        )
        client.access_update(
            "sample",
            existing_alias="docs",
            mode="rw",
            alias="models",
            path="/srv/models",
            expected_current_fingerprint="c" * 64,
        )
        client.access_remove(
            "sample",
            alias="models",
            expected_current_fingerprint="d" * 64,
        )
        self.assertEqual(
            [call[0] for call in client.calls],
            [
                ("instance", "apply", "sample", "--expected-plan-fingerprint", "a" * 64),
                ("access", "add-ro", "sample", "docs", "/srv/docs", "--expected-current-fingerprint", "b" * 64),
                (
                    "access",
                    "update",
                    "sample",
                    "docs",
                    "rw",
                    "models",
                    "/srv/models",
                    "--expected-current-fingerprint",
                    "c" * 64,
                ),
                ("access", "remove", "sample", "models", "--expected-current-fingerprint", "d" * 64),
            ],
        )
        self.assertTrue(all(call[1].get("mutation") is True for call in client.calls))

    def test_declaration_transport_validates_then_conditionally_updates_and_deletes_temp_file(self) -> None:
        class RecordingClient(ManagerClient):
            def __init__(self) -> None:
                super().__init__("manager")
                self.calls: list[tuple[str, ...]] = []
                self.paths: list[Path] = []

            def invoke(self, *args: str, **kwargs):  # type: ignore[override]
                self.calls.append(tuple(args))
                if len(args) >= 3 and args[:2] in {("instance", "validate"), ("instance", "update")}:
                    path = Path(args[2])
                    self.assert_candidate(path)
                    self.paths.append(path)
                return ManagerInvocation(tuple(args), 0, {"ok": True})

            @staticmethod
            def assert_candidate(path: Path) -> None:
                assert path.is_file()
                assert oct(path.stat().st_mode & 0o777) == "0o600"

        client = RecordingClient()
        desired = sample_instance()
        client.save_declaration("update", desired, expected_current_fingerprint="f" * 64)
        self.assertEqual(client.calls[0][:2], ("instance", "validate"))
        self.assertEqual(client.calls[1][-2:], ("--expected-current-fingerprint", "f" * 64))
        self.assertTrue(client.paths)
        self.assertTrue(all(not path.exists() for path in client.paths))

    def test_mutation_timeout_does_not_terminate_started_process(self) -> None:
        finished = threading.Event()

        class FakeProcess:
            stdin = None
            terminated = False
            killed = False
            calls = 0

            def wait(self, timeout=None):  # type: ignore[no-untyped-def]
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["manager"], timeout)
                finished.set()
                return 0

            def terminate(self) -> None:
                self.terminated = True

            def kill(self) -> None:
                self.killed = True

        fake = FakeProcess()
        client = ManagerClient("manager")
        with patch("workspace_mcp_manager.tui.subprocess.Popen", return_value=fake) as popen:
            with self.assertRaises(TuiOutcomeUnknown):
                client._invoke_process(
                    ("manager", "instance", "apply", "sample"),
                    stdin_text=None,
                    timeout=0.01,
                    mutation=True,
                )
        self.assertTrue(finished.wait(1.0))
        self.assertFalse(fake.terminated)
        self.assertFalse(fake.killed)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_output_capture_is_bounded(self) -> None:
        client = ManagerClient("manager", output_limit_bytes=8)
        with tempfile.TemporaryFile(mode="w+b") as handle:
            handle.write(b"0123456789")
            with self.assertRaisesRegex(TuiError, "exceeded"):
                client._bounded_file_text(handle, stream="stdout")

    def test_same_release_environment_manager_takes_precedence(self) -> None:
        with patch.dict(os.environ, {"WORKSPACE_MCP_MANAGER_BIN": "/release/bin/workspace-mcp-manager"}, clear=False):
            self.assertEqual(default_manager_executable(), "/release/bin/workspace-mcp-manager")


class TuiModelTests(unittest.TestCase):
    def test_generation_gate_rejects_late_result(self) -> None:
        gate = GenerationGate()
        first = gate.next("dashboard")
        second = gate.next("dashboard")
        self.assertFalse(gate.accepts("dashboard", first))
        self.assertTrue(gate.accepts("dashboard", second))

    def test_mutation_coordinator_serializes_same_instance(self) -> None:
        coordinator = MutationCoordinator()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first() -> None:
            with coordinator.mutation("sample"):
                first_entered.set()
                release_first.wait(1.0)

        def second() -> None:
            first_entered.wait(1.0)
            with coordinator.mutation("sample"):
                second_entered.set()

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        t2.start()
        self.assertTrue(first_entered.wait(1.0))
        time.sleep(0.02)
        self.assertFalse(second_entered.is_set())
        release_first.set()
        t1.join(1.0)
        t2.join(1.0)
        self.assertTrue(second_entered.is_set())
        self.assertFalse(coordinator.any_active())

    def test_v1_to_v2_projection_preserves_external_github_profile(self) -> None:
        raw = sample_instance()
        projected = project_v1_to_v2(raw)
        self.assertEqual(projected["config_version"], 2)
        self.assertEqual(projected["github"]["mode"], "external")
        self.assertEqual(projected["github"]["config_dir"], raw["github"]["config_dir"])
        self.assertIsNone(projected["git"]["identity"])
        self.assertIsNone(projected["git"]["remote"])
        self.assertEqual(projected["agent"], {"mode": "none", "ssh_auth_sock": None})

    def test_settings_are_structured_and_never_offer_authentication_secrets(self) -> None:
        labels = " ".join(field.label.lower() for field in SETTINGS_FIELDS)
        for forbidden in ("api key", "token", "private key", "passphrase", "credential"):
            self.assertNotIn(forbidden, labels)
        draft = project_v1_to_v2(sample_instance())
        updated = apply_settings_values(
            draft,
            {
                ("lifecycle", "runtime"): "stopped",
                ("mcp", "port"): "7777",
                ("github", "mode"): "disabled",
                ("recovery", "admission_guard_enabled"): True,
            },
        )
        self.assertEqual(get_nested(updated, ("lifecycle", "runtime")), "stopped")
        self.assertEqual(get_nested(updated, ("mcp", "port")), 7777)
        self.assertEqual(get_nested(updated, ("github", "mode")), "disabled")
        self.assertTrue(get_nested(updated, ("recovery", "admission_guard_enabled")))

    def test_nested_helpers_are_deterministic(self) -> None:
        raw: dict[str, object] = {"a": {"b": 1}}
        set_nested(raw, ("a", "b"), 2)
        self.assertEqual(get_nested(raw, ("a", "b")), 2)

    def test_semantic_plan_diff_never_requires_raw_json(self) -> None:
        old = {"semantic_operations": [{"operation": "UPDATE", "description": "Update MCP service"}]}
        new = {"semantic_operations": [{"operation": "RESTART", "description": "Restart MCP service"}]}
        self.assertEqual(
            semantic_plan_diff(old, new),
            ["- Update MCP service", "+ Restart MCP service"],
        )

    def test_clip_text_is_small_terminal_safe(self) -> None:
        self.assertEqual(clip_text("abcdef", 0), "")
        self.assertEqual(clip_text("abcdef", 1), "a")
        self.assertEqual(clip_text("abcdef", 4), "abc…")
        self.assertEqual(clip_text("abc", 4), "abc")

    def test_dashboard_snapshot_preserves_pm2_external_contract(self) -> None:
        class FakeClient:
            def list_instances(self):
                return [
                    {
                        "instance_id": "sample",
                        "lifecycle": {"deployment": "present", "runtime": "running"},
                    }
                ]

            def show(self, instance_id):
                return {"ok": True, "desired": sample_instance()}

            def plan(self, instance_id):
                return {"ok": True, "valid": True, "operations": [{"operation": "NOOP"}]}

        text = dashboard_snapshot(FakeClient(), instance_id="sample")  # type: ignore[arg-type]
        self.assertIn("instances=1", text)
        self.assertIn("- sample deployment=present runtime=running", text)
        self.assertIn("instance=sample", text)
        self.assertIn("plan_valid=True non_noop=0", text)
        self.assertLessEqual(len(text.encode("utf-8")), 64 * 1024)

    def test_source_has_no_private_runtime_curses_or_direct_host_mutation(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src/workspace_mcp_manager/tui.py").read_text(encoding="utf-8")
        self.assertNotIn('"_runtime"', source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("systemd-run", source)
        self.assertNotIn("journalctl", source)
        self.assertNotIn("import curses", source)
        self.assertNotIn("from textual", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
