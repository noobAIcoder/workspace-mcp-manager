from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.tui import (
    CursesTui,
    EditorField,
    ManagerClient,
    ManagerInvocation,
    TuiError,
    available_editor_fields,
    clip_text,
    dashboard_snapshot,
    get_nested,
    parse_editor_value,
    project_v1_to_v2,
    set_nested,
)

from tests.helpers import sample_instance


class ManagerClientTests(unittest.TestCase):
    def test_argv_uses_public_manager_and_registry_override_without_shell(self) -> None:
        client = ManagerClient("/opt/bin/workspace-mcp-manager", registry_dir=Path("/tmp/registry"))
        self.assertEqual(
            client.argv("instance", "list"),
            (
                "/opt/bin/workspace-mcp-manager",
                "--registry-dir",
                "/tmp/registry",
                "instance",
                "list",
            ),
        )

        completed = subprocess.CompletedProcess(
            ["manager"],
            0,
            json.dumps({"ok": True, "instances": []}),
            "",
        )
        with patch("workspace_mcp_manager.tui.subprocess.run", return_value=completed) as run:
            payload = client.invoke("instance", "list", require_ok=True).payload
        self.assertTrue(payload["ok"])
        kwargs = run.call_args.kwargs
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_structured_manager_error_is_preserved(self) -> None:
        error = {
            "ok": False,
            "error": {"code": "CONFIG_INVALID", "message": "invalid declaration", "details": {}},
        }
        completed = subprocess.CompletedProcess(["manager"], 2, "", json.dumps(error))
        client = ManagerClient("manager")
        with patch("workspace_mcp_manager.tui.subprocess.run", return_value=completed):
            with self.assertRaises(TuiError) as caught:
                client.invoke("instance", "update", "/tmp/input.json", require_ok=True)
        self.assertEqual(caught.exception.payload, error)
        self.assertIn("invalid declaration", str(caught.exception))

    def test_lifecycle_and_access_are_public_cli_mappings(self) -> None:
        class RecordingClient(ManagerClient):
            def __init__(self) -> None:
                super().__init__("manager")
                self.calls: list[tuple[str, ...]] = []

            def invoke(self, *args: str, stdin_text=None, require_ok=False):  # type: ignore[override]
                self.calls.append(tuple(args))
                return ManagerInvocation(tuple(args), 0, {"ok": True})

        client = RecordingClient()
        client.lifecycle("restart", "sample")
        client.access_add("sample", mode="ro", alias="docs", path="/srv/docs")
        client.access_remove("sample", alias="docs")
        self.assertEqual(
            client.calls,
            [
                ("instance", "restart", "sample"),
                ("access", "add-ro", "sample", "docs", "/srv/docs"),
                ("access", "remove", "sample", "docs"),
            ],
        )

    def test_declaration_transport_validates_then_updates_and_deletes_temp_file(self) -> None:
        class RecordingClient(ManagerClient):
            def __init__(self) -> None:
                super().__init__("manager")
                self.calls: list[tuple[str, ...]] = []
                self.seen_text: str | None = None
                self.path: Path | None = None

            def invoke(self, *args: str, stdin_text=None, require_ok=False):  # type: ignore[override]
                self.calls.append(tuple(args))
                if len(args) == 3 and args[:2] in {("instance", "validate"), ("instance", "update")}:
                    self.path = Path(args[2])
                    self.assert_temp_exists()
                    self.seen_text = self.path.read_text(encoding="utf-8")
                return ManagerInvocation(tuple(args), 0, {"ok": True})

            def assert_temp_exists(self) -> None:
                assert self.path is not None and self.path.is_file()

        client = RecordingClient()
        desired = sample_instance()
        client.save_declaration("update", desired)
        self.assertEqual(client.calls[0][:2], ("instance", "validate"))
        self.assertEqual(client.calls[1][:2], ("instance", "update"))
        self.assertIsNotNone(client.seen_text)
        self.assertEqual(json.loads(client.seen_text or "{}"), desired)
        assert client.path is not None
        self.assertFalse(client.path.exists())


class TuiModelTests(unittest.TestCase):
    def test_v1_to_v2_projection_preserves_external_github_profile(self) -> None:
        raw = sample_instance()
        projected = project_v1_to_v2(raw)
        self.assertEqual(projected["config_version"], 2)
        self.assertEqual(projected["github"]["mode"], "external")
        self.assertEqual(projected["github"]["config_dir"], raw["github"]["config_dir"])
        self.assertIsNone(projected["git"]["identity"])
        self.assertIsNone(projected["git"]["remote"])
        self.assertEqual(projected["agent"], {"mode": "none", "ssh_auth_sock": None})

    def test_v1_to_v2_projection_uses_disabled_when_profile_absent(self) -> None:
        raw = sample_instance()
        raw["github"] = {"config_dir": None}
        projected = project_v1_to_v2(raw)
        self.assertEqual(projected["github"], {"mode": "disabled", "config_dir": None, "binary": None})

    def test_editor_helpers_handle_nested_json_and_choices(self) -> None:
        raw = project_v1_to_v2(sample_instance())
        identity_field = next(field for field in available_editor_fields(raw) if field.path == ("git", "identity"))
        identity = parse_editor_value(identity_field, '{"name":"A","email":"a@example.invalid"}')
        set_nested(raw, identity_field.path, identity)
        self.assertEqual(get_nested(raw, identity_field.path), identity)
        choice = EditorField("mode", ("github", "mode"), "choice", ("disabled", "managed"))
        self.assertEqual(parse_editor_value(choice, "managed"), "managed")
        with self.assertRaises(ValueError):
            parse_editor_value(choice, "external")

    def test_clip_text_is_small_terminal_safe(self) -> None:
        self.assertEqual(clip_text("abcdef", 0), "")
        self.assertEqual(clip_text("abcdef", 1), "a")
        self.assertEqual(clip_text("abcdef", 4), "abc…")
        self.assertEqual(clip_text("abc", 4), "abc")

    def test_dashboard_snapshot_is_bounded_plain_text(self) -> None:
        class FakeClient:
            def list_instances(self):
                return [
                    {
                        "instance_id": "sample",
                        "lifecycle": {"deployment": "present", "runtime": "running"},
                    }
                ]

            def show(self, instance_id):
                raw = sample_instance()
                return {"ok": True, "desired": raw}

            def view(self, command, instance_id):
                return {"ok": True, "valid": True, "operations": [{"operation": "NOOP"}]}

        text = dashboard_snapshot(FakeClient(), instance_id="sample")  # type: ignore[arg-type]
        self.assertIn("instances=1", text)
        self.assertIn("sample deployment=present runtime=running", text)
        self.assertIn("plan_valid=True non_noop=0", text)

    def test_put_clips_without_raising_on_tiny_screen(self) -> None:
        class TinyScreen:
            def getmaxyx(self):
                return (1, 1)

            def addnstr(self, *args, **kwargs):
                raise AssertionError("addnstr should not be called when no width is available")

        app = CursesTui(TinyScreen(), ManagerClient("manager"))
        app._put(0, 0, "hello")

    def test_source_has_no_private_runtime_or_direct_systemd_mutation(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src/workspace_mcp_manager/tui.py").read_text(encoding="utf-8")
        self.assertNotIn('"_runtime"', source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("systemd-run", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
