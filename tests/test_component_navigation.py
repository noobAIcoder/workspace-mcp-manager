from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import nullcontext
from typing import Any
from unittest import mock

try:
    from textual.app import App
    from textual.widgets import Button, Static
    from workspace_mcp_manager.tui_textual import (
        MainMenuScreen, ComponentsScreen, DashboardScreen, WorkspaceManagerApp,
        _is_ssh_session, build_app,
    )
    HAS_TEXTUAL = True
except ModuleNotFoundError:
    HAS_TEXTUAL = False


class FakeClient:
    def cli_version(self) -> str:
        return "0.3-test"

    def component_inventory(self, *, check_updates: bool = False) -> dict[str, Any]:
        return {
            "ok": True,
            "components": {
                name: {
                    "installations": [], "documentation_url": f"https://example.org/{name}",
                    "manual_commands": f"# manual instructions for {name}",
                    "latest_stable": "0.5.0" if check_updates else None,
                    "upstream_checked": check_updates,
                }
                for name in ("coding-tools-mcp", "tunnel-client")
            },
        }


@unittest.skipUnless(HAS_TEXTUAL, "Textual is not installed")
class ComponentNavigationTests(unittest.TestCase):
    def test_tui_opens_with_two_primary_choices(self) -> None:
        async def run() -> None:
            app = build_app(FakeClient(), initial_load=False, show_main_menu=True)
            async with app.run_test(size=(100, 35)) as pilot:
                self.assertIsInstance(app.screen, MainMenuScreen)
                ids = {button.id for button in app.screen.query(Button)}
                self.assertEqual(ids, {"manage-components", "manage-instances"})
                app.screen.query_one("#manage-components", Button).press()
                await pilot.pause()
                self.assertIsInstance(app.screen, ComponentsScreen)
                app.screen.query_one("#component-back", Button).press()
                await pilot.pause()
                self.assertIsInstance(app.screen, MainMenuScreen)
        asyncio.run(run())

    def test_manage_instances_retains_existing_dashboard(self) -> None:
        async def run() -> None:
            app = build_app(FakeClient(), initial_load=False, show_main_menu=True)
            async with app.run_test(size=(100, 35)) as pilot:
                app.screen.query_one("#manage-instances", Button).press()
                await pilot.pause()
                self.assertIsInstance(app.screen, DashboardScreen)
        asyncio.run(run())

    def test_copy_commands_over_ssh_reports_request_not_confirmed_delivery(self) -> None:
        async def run() -> None:
            app = build_app(FakeClient(), initial_load=False, show_main_menu=True)
            with mock.patch.dict(os.environ, {"SSH_TTY": "/dev/pts/9"}):
                async with app.run_test(size=(100, 35)) as pilot:
                    app.screen.query_one("#manage-components", Button).press()
                    await pilot.pause()
                    self.assertIsInstance(app.screen, ComponentsScreen)
                    with mock.patch.object(app, "copy_to_clipboard") as copy:
                        app.screen.query_one("#component-copy", Button).press()
                        await pilot.pause()
                        copy.assert_called_once_with("# manual instructions for coding-tools-mcp")
                    status = str(app.screen.query_one("#screen-status", Static).render())
                    self.assertIn("OSC 52", status)
                    self.assertIn("paste to verify", status)
                    self.assertNotIn("commands copied", status.lower())
        asyncio.run(run())

    def test_terminal_view_prints_commands_without_running_them(self) -> None:
        async def run() -> None:
            app = build_app(FakeClient(), initial_load=False, show_main_menu=True)
            async with app.run_test(size=(100, 35)) as pilot:
                app.screen.query_one("#manage-components", Button).press()
                await pilot.pause()
                self.assertIsInstance(app.screen, ComponentsScreen)
                with (
                    mock.patch.object(app, "suspend", return_value=nullcontext()) as suspend,
                    mock.patch("builtins.print") as display,
                    mock.patch("builtins.input", return_value="") as wait,
                ):
                    app.screen.query_one("#component-show", Button).press()
                    await pilot.pause()
                    suspend.assert_called_once_with()
                    wait.assert_called_once()
                    self.assertTrue(any(
                        "# manual instructions for coding-tools-mcp" in repr(call)
                        for call in display.call_args_list
                    ))
                status = str(app.screen.query_one("#screen-status", Static).render())
                self.assertIn("manual copying", status)
        asyncio.run(run())

    def test_ssh_never_writes_to_server_windows_clipboard(self) -> None:
        async def run() -> None:
            app = build_app(FakeClient(), initial_load=False, show_main_menu=True)
            with (
                mock.patch.dict(os.environ, {"SSH_CONNECTION": "client 111 server 22"}),
                mock.patch.object(App, "copy_to_clipboard") as osc52,
                mock.patch("workspace_mcp_manager.tui_textual._copy_to_host_clipboard") as windows_bridge,
            ):
                async with app.run_test(size=(100, 35)) as pilot:
                    app.copy_to_clipboard("copy from remote to local")
                    osc52.assert_called_once_with("copy from remote to local")
                    windows_bridge.assert_not_called()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
