from __future__ import annotations

import asyncio
import unittest
from typing import Any

try:
    from textual.widgets import Button
    from workspace_mcp_manager.tui_textual import (
        MainMenuScreen, ComponentsScreen, DashboardScreen, build_app,
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


if __name__ == "__main__":
    unittest.main()
