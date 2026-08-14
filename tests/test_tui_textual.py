from __future__ import annotations

import asyncio
import copy
import os
import threading
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

from workspace_mcp_manager.tui import TuiError, TuiOutcomeUnknown

from tests.helpers import sample_v2_instance

try:
    from textual.widgets import Button, DataTable, Input, Select, TabbedContent

    from workspace_mcp_manager.tui_textual import (
        AccessModal,
        ConfirmModal,
        DashboardScreen,
        Input as ManagerInput,
        InstanceScreen,
        LogsScreen,
        ReviewChangesScreen,
        SettingsScreen,
        build_app,
    )
    from workspace_mcp_manager.tui import SETTINGS_FIELDS

    TEXTUAL_AVAILABLE = True
except ModuleNotFoundError:
    TEXTUAL_AVAILABLE = False


def stale_error(message: str = "stale state") -> TuiError:
    return TuiError(
        f"STALE_STATE: {message}",
        payload={
            "ok": False,
            "error": {"code": "STALE_STATE", "message": message, "details": {}},
        },
    )


class FakeClient:
    def __init__(self) -> None:
        self.desired = sample_v2_instance()
        self.desired["instance_id"] = "manager-qual"
        self.desired["workspace_path"] = "/home/operator/repos/manager-qual"
        self.plan_version = "a" * 64
        self.apply_error: TuiError | None = None
        self.lifecycle_error: TuiError | None = None
        self.save_error: TuiError | None = None
        self.access_error: TuiError | None = None
        self.lifecycle_started = threading.Event()
        self.lifecycle_release: threading.Event | None = None
        self.uncertain_release = threading.Event()
        self.apply_calls: list[str] = []
        self.lifecycle_calls: list[str] = []
        self.save_calls = 0
        self.access_updates = 0
        self.access_add_calls: list[dict[str, Any]] = []
        self.health = "Healthy"
        self.attention_reasons = ["Pending reconciliation"]
        self.log_categories: list[str] = []
        self.candidate_calls: list[Mapping[str, Any]] = []

    def cli_version(self) -> str:
        return "9.8.7-test"

    def summaries(self) -> Mapping[str, Any]:
        return {
            "ok": True,
            "instances": [self.summary("manager-qual")],
            "counts": {"instances": 1, "attention": 1, "pending": 1, "blocked": 0},
        }

    def host_overview(self) -> Mapping[str, Any]:
        return {"ok": True, "inspect": {"platform": "WSL2"}, "components": {"manager": "present"}}

    def host_doctor(self) -> Mapping[str, Any]:
        return {"ok": True, "doctor": {"status": "healthy"}}

    def summary(self, instance_id: str) -> Mapping[str, Any]:
        return {
            "ok": True,
            "projection_version": 1,
            "instance_id": instance_id,
            "workspace_path": self.desired["workspace_path"],
            "declaration_fingerprint": "d" * 64,
            "desired_lifecycle": "Present + Running",
            "runtime": "Running",
            "health": self.health,
            "tunnel": "Ready",
            "reconciliation": "Pending",
            "recovery": "Clean",
            "plan_fingerprint": self.plan_version,
            "access": {"read_only_count": 1, "read_write_count": 1},
            "git": {
                "workspace": self.desired["workspace_path"],
                "github_mode": "external",
                "identity_configured": True,
                "remote_configured": True,
                "remote_protocol": "ssh",
                "agent_mode": "external",
            },
            "attention_reasons": list(self.attention_reasons),
        }

    def show(self, instance_id: str) -> Mapping[str, Any]:
        return {"ok": True, "instance_id": instance_id, "fingerprint": "d" * 64, "desired": self.desired}

    def access_list(self, instance_id: str) -> Mapping[str, Any]:
        return {
            "ok": True,
            "entries": [
                {"mode": "ro", "alias": "docs", "path": "/srv/docs"},
                {"mode": "rw", "alias": "models", "path": "/srv/models"},
            ],
        }

    def git(self, instance_id: str) -> Mapping[str, Any]:
        return {"ok": True, "repository": True, "remote": "origin", "transport": "ssh"}

    def plan(self, instance_id: str) -> Mapping[str, Any]:
        return {
            "ok": True,
            "valid": True,
            "instance_id": instance_id,
            "desired_fingerprint": "d" * 64,
            "plan_fingerprint": self.plan_version,
            "operations": [{"operation": "UPDATE", "target": "mcp", "reason": "pending"}],
            "semantic_operations": [
                {"operation": "UPDATE", "description": f"Update MCP service {self.plan_version[0]}"}
            ],
            "warnings": [],
        }

    def apply(self, instance_id: str, *, expected_plan_fingerprint: str) -> Mapping[str, Any]:
        self.apply_calls.append(expected_plan_fingerprint)
        if self.apply_error is not None:
            error = self.apply_error
            self.apply_error = None
            self.plan_version = "b" * 64
            raise error
        return {"ok": True}

    def lifecycle(self, action: str, instance_id: str) -> Mapping[str, Any]:
        self.lifecycle_calls.append(action)
        self.lifecycle_started.set()
        if self.lifecycle_release is not None:
            self.lifecycle_release.wait(2.0)
        if self.lifecycle_error is not None:
            error = self.lifecycle_error
            self.lifecycle_error = None
            raise error
        return {"ok": True}

    def wait_for_uncertain_mutation(self, instance_id: str, timeout: float | None = None) -> bool:
        if timeout == 0:
            return self.uncertain_release.is_set()
        return self.uncertain_release.wait(timeout)

    def diagnose(self, instance_id: str) -> Mapping[str, Any]:
        return {"ok": True, "snapshot": {"services": {}, "probes": {}, "classifications": []}}

    def logs(self, instance_id: str, *, category: str = "all", lines: int = 100) -> Mapping[str, Any]:
        self.log_categories.append(category)
        return {"ok": True, "journals": {"mcp": "ready"}, "files": {}, "recovery": {}}

    def template(self) -> Mapping[str, Any]:
        template = sample_v2_instance()
        template["instance_id"] = ""
        template["workspace_path"] = ""
        template["mcp"]["port"] = None
        template["tunnel"]["id"] = ""
        template["tunnel"]["profile"] = ""
        template["tunnel"]["health_port"] = None
        return {"ok": True, "defaults": template}

    def candidate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.candidate_calls.append(copy.deepcopy(dict(request)))
        workspace = str(request.get("workspace_path") or "/home/operator/repos/new")
        existing = "manager-qual" if workspace == self.desired["workspace_path"] else None
        suggested = Path(workspace).name.lower().replace("_", "-") or "workspace"
        if existing:
            desired = copy.deepcopy(self.desired)
        else:
            desired = sample_v2_instance()
            desired["instance_id"] = suggested
            desired["workspace_path"] = workspace
            desired["lifecycle"] = {"deployment": "present", "runtime": "stopped"}
            desired["mcp"]["port"] = 7654
            desired["mcp"]["permission_mode"] = "trusted"
            desired["tunnel"]["id"] = ""
            desired["tunnel"]["health_port"] = 7070
            desired["tunnel"]["profile"] = f"workspace-mcp-{suggested}"
            desired["access"] = {"read_only": [], "read_write": []}
            desired["github"] = {"mode": "disabled", "config_dir": None, "binary": None}
            desired["git"] = {"identity": None, "remote": None}
            desired["agent"] = {"mode": "none", "ssh_auth_sock": None}

        source = request.get("copy_source_instance_id")
        if source == "manager-qual":
            desired["mcp"]["permission_mode"] = self.desired["mcp"]["permission_mode"]
            desired["access"] = copy.deepcopy(self.desired["access"])

        for edit in request.get("field_edits", []):
            if not isinstance(edit, Mapping):
                continue
            path = str(edit.get("path") or "")
            operation = edit.get("operation")
            value = edit.get("value") if operation == "set" else None
            targets = {
                "/instance_id": (desired, "instance_id"),
                "/lifecycle/runtime": (desired["lifecycle"], "runtime"),
                "/mcp/permission_mode": (desired["mcp"], "permission_mode"),
                "/mcp/port": (desired["mcp"], "port"),
                "/mcp/shell_env_inherit": (desired["mcp"], "shell_env_inherit"),
                "/tunnel/id": (desired["tunnel"], "id"),
                "/tunnel/health_port": (desired["tunnel"], "health_port"),
                "/github/mode": (desired["github"], "mode"),
                "/github/config_dir": (desired["github"], "config_dir"),
                "/github/binary": (desired["github"], "binary"),
                "/git/identity": (desired["git"], "identity"),
                "/git/remote": (desired["git"], "remote"),
                "/agent/mode": (desired["agent"], "mode"),
                "/agent/ssh_auth_sock": (desired["agent"], "ssh_auth_sock"),
                "/recovery/admission_guard_enabled": (desired["recovery"], "admission_guard_enabled"),
                "/recovery/guard_interval_seconds": (desired["recovery"], "guard_interval_seconds"),
                "/recovery/recovery_cooldown_seconds": (desired["recovery"], "recovery_cooldown_seconds"),
            }
            target = targets.get(path)
            if target is not None:
                target[0][target[1]] = value
        if desired["github"].get("mode") == "disabled":
            desired["github"]["config_dir"] = None
        if desired["agent"].get("mode") != "external":
            desired["agent"]["ssh_auth_sock"] = None
        if not existing:
            desired["tunnel"]["profile"] = f"workspace-mcp-{desired['instance_id']}"

        for edit in request.get("access_edits", []):
            if not isinstance(edit, Mapping):
                continue
            mode = "read_only" if edit.get("mode") == "ro" else "read_write"
            if edit.get("operation") == "add":
                path = str(edit.get("path") or "")
                alias = str(edit.get("alias") or Path(path).name.lower().replace(" ", "-"))
                desired["access"][mode].append({"alias": alias, "path": path})
            elif edit.get("operation") == "remove":
                alias = edit.get("target_alias")
                for group in ("read_only", "read_write"):
                    desired["access"][group] = [item for item in desired["access"][group] if item.get("alias") != alias]

        unresolved = [] if desired["tunnel"]["id"] else ["tunnel.id"]
        provenance = [
            {"path": "/instance_id", "source": "manager_derived", "status": "effective", "reason": "suggestion"},
            {"path": "/workspace_path", "source": "workspace_discovery", "status": "effective", "reason": "canonical"},
            {"path": "/mcp/permission_mode", "source": "template_default", "status": "effective", "reason": "default"},
            {"path": "/mcp/port", "source": "manager_recommendation", "status": "effective", "reason": "port"},
            {"path": "/tunnel/health_port", "source": "manager_recommendation", "status": "effective", "reason": "port"},
            {"path": "/tunnel/profile", "source": "manager_derived", "status": "effective", "reason": "profile"},
        ]
        for edit in request.get("field_edits", []):
            if isinstance(edit, Mapping):
                for item in provenance:
                    if item["path"] == edit.get("path"):
                        item["source"] = "operator_override"
        return {
            "ok": True,
            "candidate_version": 1,
            "candidate_input_fingerprint": "i" * 64,
            "candidate_fingerprint": "c" * 64,
            "effective_declaration": desired,
            "field_provenance": provenance,
            "discovery_fingerprint": "f" * 64,
            "copy_source": source,
            "copy_source_fingerprint": "d" * 64 if source else None,
            "port_projection": {
                "port_projection_version": 1,
                "listener_observation": {"state": "available", "reason": None},
                "endpoints": [
                    {"port": 7656, "purpose": "MCP", "instance_id": "manager-qual", "state": "declared + listening"},
                    {"port": 7071, "purpose": "Tunnel health", "instance_id": "manager-qual", "state": "declared + listening"},
                ],
                "collision_state": "clear",
                "conflicts": [],
            },
            "warnings": [],
            "unresolved_required_operator_fields": unresolved,
            "existing_instance_id": existing,
            "discovery": {
                "workspace_path": workspace,
                "workspace_identity": workspace,
                "repository_detected": True,
                "repository_root": workspace,
                "local_git": {
                    "identity": {
                        "local": {"name": "Example User", "email": "example@example.invalid"},
                        "effective": {"name": "Example User", "email": "example@example.invalid", "scope": "repository-local"},
                        "classification": "adoptable",
                    },
                    "remote": {
                        "selected": "origin",
                        "host": "github.com",
                        "repository": "noobAIcoder/example",
                        "transport": "ssh",
                        "classification": "adoptable",
                    },
                },
                "authentication_status": {
                    "authentication_status_version": 1,
                    "providers": [
                        {
                            "provider": "github-cli",
                            "status": "unavailable",
                            "reason": "GitHub CLI authentication unavailable",
                            "setup_action": {"label": "Setup", "url": "https://github.com/settings/tokens"},
                        }
                    ],
                },
            },
        }

    def preview_declaration(self, desired: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "ok": True,
            "instance_id": desired["instance_id"],
            "candidate_fingerprint": "c" * 64,
            "validation": {"valid": True, "errors": []},
            "collision_result": {"state": "clear", "clear": True, "conflicts": []},
            "warnings": [],
            "errors": [],
            "plan_fingerprint": self.plan_version,
            "semantic_operations": [{"operation": "CREATE", "description": "Add MCP service"}],
        }

    def save_declaration(
        self,
        action: str,
        desired: Mapping[str, Any],
        *,
        expected_current_fingerprint: str | None = None,
    ) -> Mapping[str, Any]:
        self.save_calls += 1
        if self.save_error is not None:
            raise self.save_error
        self.desired = dict(desired)
        return {"ok": True}

    def access_update(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self.access_updates += 1
        if self.access_error is not None:
            raise self.access_error
        return {"ok": True}

    def access_add(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self.access_add_calls.append(dict(kwargs))
        return {"ok": True}

    def access_remove(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        return {"ok": True}


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual Pilot tests run in the isolated PM3 frontend runtime")
class TextualPilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_to_instance_and_80x24_render(self) -> None:
        client = FakeClient()
        app = build_app(client)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, DashboardScreen)
            self.assertEqual(app.screen.query_one("#instances", DataTable).row_count, 1)
            await pilot.click("#open")
            await pilot.pause()
            self.assertIsInstance(app.screen, InstanceScreen)
            self.assertIn("Present + Running", str(app.screen.query_one("#instance-header").render()))

    async def test_smaller_terminal_degrades_without_exception(self) -> None:
        client = FakeClient()
        app = build_app(client)
        async with app.run_test(size=(50, 14)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, DashboardScreen)
            self.assertEqual(app.screen.query_one("#instances", DataTable).row_count, 1)

    async def test_destructive_remove_can_be_cancelled(self) -> None:
        client = FakeClient()
        app = build_app(client)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(InstanceScreen("manager-qual"))
            await pilot.pause()
            await pilot.click("#remove")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmModal)
            await pilot.click("#cancel")
            await pilot.pause()
            self.assertIsInstance(app.screen, InstanceScreen)
            self.assertEqual(client.lifecycle_calls, [])

    async def test_invalid_structured_settings_preserve_screen_and_draft(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(SettingsScreen("manager-qual", client.desired, "d" * 64))
            await pilot.pause()
            port = app.screen.query_one("#setting-2", Input)
            port.value = "not-a-port"
            await pilot.click("#save")
            await pilot.pause()
            self.assertIsInstance(app.screen, SettingsScreen)
            self.assertEqual(port.value, "not-a-port")
            self.assertIn("integer", str(app.screen.query_one("#screen-status").render()).lower())

    async def test_stale_settings_rejection_preserves_operator_draft(self) -> None:
        client = FakeClient()
        client.save_error = stale_error("settings changed elsewhere")
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(SettingsScreen("manager-qual", client.desired, "d" * 64))
            await pilot.pause()
            port = app.screen.query_one("#setting-2", Input)
            port.value = "7788"
            await pilot.click("#save")
            for _ in range(6):
                await pilot.pause()
            self.assertIsInstance(app.screen, SettingsScreen)
            self.assertEqual(port.value, "7788")
            self.assertIn("stale", str(app.screen.query_one("#screen-status").render()).lower())

    async def test_stale_access_edit_reopens_preserved_draft(self) -> None:
        client = FakeClient()
        client.access_error = stale_error("access changed elsewhere")
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(InstanceScreen("manager-qual"))
            for _ in range(3):
                await pilot.pause()
            app.screen.query_one(TabbedContent).active = "access"
            await pilot.pause()
            await pilot.click("#access-edit")
            await pilot.pause()
            self.assertIsInstance(app.screen, AccessModal)
            self.assertEqual(app.screen.query_one("#access-mode", Select).value, "ro")
            app.screen.query_one("#access-path", Input).value = "/srv/draft-models"
            app.screen.query_one("#save", Button).press()
            await asyncio.sleep(0.2)
            for _ in range(6):
                await pilot.pause()
            self.assertEqual(
                client.access_updates,
                1,
                f"screen={app.screen!r} access_error={getattr(app.screen, 'query_one', None)}",
            )
            self.assertIsInstance(app.screen, AccessModal)
            self.assertEqual(app.screen.query_one("#access-path", Input).value, "/srv/draft-models")

    async def test_stale_review_is_reloaded_and_not_silently_applied(self) -> None:
        client = FakeClient()
        client.apply_error = stale_error("reviewed plan changed")
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ReviewChangesScreen("manager-qual"))
            for _ in range(3):
                await pilot.pause()
            self.assertEqual(app.screen.plan_payload["plan_fingerprint"], "a" * 64)
            await pilot.click("#apply")
            for _ in range(8):
                await pilot.pause()
            self.assertIsInstance(app.screen, ReviewChangesScreen)
            self.assertEqual(client.apply_calls, ["a" * 64])
            self.assertEqual(app.screen.plan_payload["plan_fingerprint"], "b" * 64)

    async def test_busy_state_disables_conflicting_mutations(self) -> None:
        client = FakeClient()
        client.lifecycle_release = threading.Event()
        app = build_app(client)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(InstanceScreen("manager-qual"))
            await pilot.pause()
            await pilot.click("#start")
            self.assertTrue(await asyncio.to_thread(client.lifecycle_started.wait, 1.0))
            self.assertTrue(app.screen.query_one("#start", Button).disabled)
            self.assertTrue(app.screen.query_one("#restart", Button).disabled)
            client.lifecycle_release.set()
            for _ in range(5):
                await pilot.pause()
            self.assertFalse(app.screen.query_one("#start", Button).disabled)

    async def test_uncertain_mutation_requires_authoritative_resolution(self) -> None:
        client = FakeClient()
        client.lifecycle_error = TuiOutcomeUnknown("Outcome unknown")
        app = build_app(client)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(InstanceScreen("manager-qual"))
            await pilot.pause()
            await pilot.click("#restart")
            for _ in range(3):
                await pilot.pause()
            self.assertTrue(app.screen.query_one("#restart", Button).disabled)
            self.assertIn("outcome unknown", str(app.screen.query_one("#screen-status").render()).lower())
            client.uncertain_release.set()
            for _ in range(8):
                await pilot.pause()
            self.assertFalse(app.screen.query_one("#restart", Button).disabled)
            self.assertNotIn("manager-qual", app.uncertain_instances)

    async def test_command_palette_opens_from_ctrl_p(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            before = app.screen
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertIsNot(app.screen, before)

    async def test_app_preserves_textual_clipboard_and_quit_bindings(self) -> None:
        from workspace_mcp_manager.tui_textual import WorkspaceManagerApp

        client = FakeClient()
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)):
            active = {key: value.binding for key, value in app.active_bindings.items()}
            self.assertNotEqual(active["ctrl+c"].action, "quit")
            self.assertEqual(active["ctrl+q"].action, "quit")
            self.assertTrue(active["ctrl+q"].priority)
        self.assertTrue(
            any(binding.key == "f6" and binding.action == "toggle_terminal_selection" for binding in WorkspaceManagerApp.BINDINGS)
        )

    async def test_terminal_copy_paste_fallback_bindings_are_available(self) -> None:
        bindings = list(ManagerInput.BINDINGS)
        self.assertTrue(any(binding.key == "ctrl+shift+c" and binding.action == "copy" for binding in bindings))
        self.assertTrue(any(binding.key == "ctrl+insert" and binding.action == "copy" for binding in bindings))
        self.assertTrue(any(binding.key == "ctrl+shift+v" and binding.action == "paste" for binding in bindings))
        self.assertTrue(any(binding.key == "shift+insert" and binding.action == "paste" for binding in bindings))

    async def test_top_bar_shows_tui_and_cli_versions(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        self.assertIn("TUI 0.1.1", str(app.sub_title))
        self.assertIn("CLI 9.8.7-test", str(app.sub_title))

    async def test_input_ctrl_c_and_ctrl_v_round_trip_without_quitting(self) -> None:
        client = FakeClient()
        with (
            mock.patch("workspace_mcp_manager.tui_textual._copy_to_host_clipboard"),
            mock.patch("workspace_mcp_manager.tui_textual._read_host_clipboard", return_value=None),
        ):
            app = build_app(client, initial_load=False)
            async with app.run_test(size=(80, 24)) as pilot:
                app.push_screen(SettingsScreen("manager-qual", client.desired, "d" * 64))
                await pilot.pause()
                port = app.screen.query_one("#setting-2", Input)
                port.value = "7654"
                port.focus()
                await pilot.press("ctrl+shift+a", "ctrl+c")
                await pilot.pause()
                self.assertEqual(app.clipboard, "7654")
                self.assertIsInstance(app.screen, SettingsScreen)

                port.value = ""
                port.cursor_position = 0
                await pilot.press("ctrl+v")
                await pilot.pause()
                self.assertEqual(port.value, "7654")
                self.assertIsInstance(app.screen, SettingsScreen)

    async def test_wsl_host_clipboard_bridge_handles_copy_and_explicit_paste(self) -> None:
        client = FakeClient()
        with (
            mock.patch("workspace_mcp_manager.tui_textual._copy_to_host_clipboard") as copy_host,
            mock.patch("workspace_mcp_manager.tui_textual._read_host_clipboard", return_value="8123"),
        ):
            app = build_app(client, initial_load=False)
            async with app.run_test(size=(80, 24)) as pilot:
                app.push_screen(SettingsScreen("manager-qual", client.desired, "d" * 64))
                await pilot.pause()
                port = app.screen.query_one("#setting-2", Input)
                port.value = "7654"
                port.focus()
                await pilot.press("ctrl+shift+a", "ctrl+c")
                await pilot.pause()
                copy_host.assert_called_with("7654")

                port.value = ""
                port.cursor_position = 0
                await pilot.press("ctrl+v")
                await pilot.pause()
                self.assertEqual(port.value, "8123")

    async def test_degraded_instance_reason_is_visible_without_raw_json(self) -> None:
        client = FakeClient()
        client.health = "Degraded"
        client.attention_reasons = ["MCP discovery failed"]
        app = build_app(client)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(3):
                await pilot.pause()
            row = app.screen.query_one("#instances", DataTable).get_row_at(0)
            self.assertIn("Degraded", str(row))
            await pilot.click("#open")
            for _ in range(3):
                await pilot.pause()
            overview = str(app.screen.query_one("#overview-content").render())
            self.assertIn("MCP discovery failed", overview)
            self.assertNotIn("{", overview)

    async def test_add_rw_access_then_review_and_apply(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(InstanceScreen("manager-qual"))
            for _ in range(3):
                await pilot.pause()
            app.screen.query_one(TabbedContent).active = "access"
            await pilot.pause()
            await pilot.click("#access-add-rw")
            await pilot.pause()
            self.assertIsInstance(app.screen, AccessModal)
            app.screen.query_one("#access-alias", Input).value = "new-models"
            app.screen.query_one("#access-path", Input).value = "/srv/new-models"
            app.screen.query_one("#save", Button).press()
            await asyncio.sleep(0.2)
            for _ in range(5):
                await pilot.pause()
            self.assertEqual(client.access_add_calls[-1]["mode"], "rw")
            self.assertIsInstance(app.screen, InstanceScreen)
            await pilot.click("#review")
            for _ in range(4):
                await pilot.pause()
            self.assertIsInstance(app.screen, ReviewChangesScreen)
            await pilot.click("#apply")
            for _ in range(6):
                await pilot.pause()
            self.assertEqual(client.apply_calls[-1], "a" * 64)

    async def test_git_transport_changes_through_structured_settings(self) -> None:
        client = FakeClient()
        client.desired["git"]["remote"] = {"name": "origin", "protocol": "ssh"}
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(SettingsScreen("manager-qual", client.desired, "d" * 64))
            await pilot.pause()
            remote_index = next(
                index
                for index, field in enumerate(SETTINGS_FIELDS)
                if field.path == ("git", "remote", "protocol")
            )
            app.screen.query_one(f"#setting-{remote_index}", Select).value = "https-gh"
            await pilot.click("#save")
            for _ in range(8):
                await pilot.pause()
            self.assertEqual(client.desired["git"]["remote"]["protocol"], "https-gh")

    async def test_logs_are_loaded_through_manager_category(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(LogsScreen("manager-qual"))
            for _ in range(4):
                await pilot.pause()
            self.assertEqual(client.log_categories, ["all"])
            self.assertIn("ready", "\n".join(app.screen.raw_lines))

    async def test_stop_start_restart_use_same_lifecycle_execution_path(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(InstanceScreen("manager-qual"))
            for _ in range(3):
                await pilot.pause()
            for action in ("stop", "start", "restart"):
                await pilot.click(f"#{action}")
                for _ in range(5):
                    await pilot.pause()
            self.assertEqual(client.lifecycle_calls, ["stop", "start", "restart"])

    async def test_new_instance_wizard_previews_then_creates_and_deploys(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        from workspace_mcp_manager.tui_textual import NewInstanceScreen

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(NewInstanceScreen())
            for _ in range(3):
                await pilot.pause()
            app.screen.query_one("#new-instance-id", Input).value = "new-qual"
            app.screen.query_one("#new-workspace", Input).value = "/home/operator/repos/new-qual"
            await pilot.click("#wizard-next")
            for _ in range(3):
                await pilot.pause()
            app.screen.query_one("#new-mcp-port", Input).value = "7766"
            await pilot.click("#wizard-next")
            for _ in range(3):
                await pilot.pause()
            app.screen.query_one("#new-tunnel-id", Input).value = "tunnel_new_qual"
            app.screen.query_one("#new-health-port", Input).value = "7171"
            await pilot.click("#wizard-next")
            for _ in range(3):
                await pilot.pause()
            await pilot.click("#wizard-next")
            for _ in range(5):
                await pilot.pause()
            self.assertEqual(app.screen.step, 4)
            self.assertEqual(app.screen.preview_payload["plan_fingerprint"], "a" * 64)
            self.assertFalse(app.screen.query_one("#wizard-deploy", Button).disabled)
            await pilot.click("#wizard-deploy")
            for _ in range(10):
                await pilot.pause()
            self.assertEqual(client.save_calls, 1)
            self.assertEqual(client.apply_calls, ["a" * 64])
            self.assertIsInstance(app.screen, InstanceScreen)

    async def test_pm3_1_wizard_projects_manager_context_without_profile_or_secret_inputs(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        from workspace_mcp_manager.tui_textual import NewInstanceScreen

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(NewInstanceScreen())
            for _ in range(5):
                await pilot.pause()
            self.assertIsInstance(app.screen.query_one("#new-copy-source"), Select)
            self.assertEqual(list(app.screen.query("#new-tunnel-profile")), [])
            input_ids = {widget.id for widget in app.screen.query(Input)}
            self.assertNotIn("new-tunnel-profile", input_ids)
            self.assertFalse(any(any(term in str(widget_id).lower() for term in ("secret", "token", "api-key", "password")) for widget_id in input_ids))
            runtime_children = list(app.screen.query_one("#wizard-runtime").children)
            permission_index = next(index for index, widget in enumerate(runtime_children) if widget.id == "new-permission")
            port_index = next(index for index, widget in enumerate(runtime_children) if widget.id == "new-mcp-port")
            self.assertLess(permission_index, port_index)
            self.assertIn("7656", str(app.screen.query_one("#new-runtime-known-ports").render()))
            self.assertIn("repository-local", str(app.screen.query_one("#new-git-summary").render()))
            self.assertEqual(app.screen.query_one("#new-auth-setup", Button).styles.display, "block")

    async def test_pm3_1_managed_launch_context_prioritizes_open_existing(self) -> None:
        client = FakeClient()
        client.desired["workspace_path"] = os.getcwd()
        app = build_app(client, initial_load=False)
        from workspace_mcp_manager.tui_textual import NewInstanceScreen

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(NewInstanceScreen())
            for _ in range(5):
                await pilot.pause()
            self.assertEqual(app.screen.existing_instance_id, "manager-qual")
            self.assertTrue(app.screen.query_one("#wizard-next", Button).disabled)
            self.assertEqual(app.screen.query_one("#existing-workspace-row").styles.display, "block")
            app.screen.query_one("#new-open-existing", Button).press()
            for _ in range(3):
                await pilot.pause()
            self.assertIsInstance(app.screen, InstanceScreen)

    async def test_pm3_1_workspace_change_invalidates_target_edits_but_keeps_runtime_overrides(self) -> None:
        client = FakeClient()
        app = build_app(client, initial_load=False)
        from workspace_mcp_manager.tui_textual import NewInstanceScreen

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(NewInstanceScreen())
            for _ in range(5):
                await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, NewInstanceScreen)
            screen.workspace_identity = "/old/workspace"
            screen.field_edits = [
                {"path": "/instance_id", "operation": "set", "value": "old-id"},
                {"path": "/tunnel/id", "operation": "set", "value": "tunnel_old"},
                {"path": "/mcp/port", "operation": "set", "value": 7788},
            ]
            screen.access_edits = [{"operation": "add", "mode": "rw", "path": "/srv/old"}]
            screen._populating = True
            screen.query_one("#new-workspace", Input).value = "/new/workspace"
            screen._populating = False
            screen._request_candidate("test workspace change")
            for _ in range(8):
                await pilot.pause()
            paths = {item["path"] for item in screen.field_edits}
            self.assertEqual(paths, {"/mcp/port"})
            self.assertEqual(screen.access_edits, [])


if __name__ == "__main__":
    unittest.main()
