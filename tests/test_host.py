from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.host import CommandObservation, HostInspector
from workspace_mcp_manager.paths import ManagerPaths


class HostInspectorTests(unittest.TestCase):
    def _paths(self, root: Path) -> ManagerPaths:
        return ManagerPaths(root, root / ".config/wmm", root / ".config/wmm/instances", root / ".state/wmm")

    def test_environment_reports_secret_presence_not_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inspector = HostInspector(
                self._paths(Path(directory)),
                environ={"PATH": "/usr/bin", "CONTROL_PLANE_API_KEY": "do-not-print"},
            )
            metadata = inspector._environment_metadata()
            self.assertTrue(metadata["secret_presence"]["CONTROL_PLANE_API_KEY"])
            self.assertNotIn("do-not-print", repr(metadata))

    def test_external_roots_exclude_system_binaries(self) -> None:
        components = {
            "git": {"path": "/usr/bin/git"},
            "codex": {"path": "/home/operator/.local/lib/codex/bin/codex"},
        }
        roots = HostInspector._external_roots(components)
        self.assertEqual(roots, ["/home/operator/.local/lib/codex/bin"])

    def test_legacy_candidates_are_derived_from_systemd_when_configs_are_inaccessible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inspector = HostInspector(self._paths(Path(directory)), environ={"PATH": "/usr/bin"})
            legacy = inspector._legacy_inventory(
                {
                    "units": [
                        "coding-tools-mcp-alpha.service enabled enabled",
                        "tunnel-client-alpha.service enabled enabled",
                        "coding-tools-mcp-beta.service enabled enabled",
                    ]
                }
            )
            self.assertEqual(legacy["systemd_instance_candidates"], ["alpha", "beta"])
            self.assertTrue(legacy["config_scan_incomplete"])

    @patch("workspace_mcp_manager.host.shutil.which")
    def test_linger_falls_back_to_list_users(self, which) -> None:
        which.return_value = "/usr/bin/loginctl"
        with tempfile.TemporaryDirectory() as directory:
            inspector = HostInspector(self._paths(Path(directory)), environ={"PATH": "/usr/bin"})
            with patch.object(
                inspector,
                "_run",
                side_effect=[
                    CommandObservation(("loginctl",), 1, "", "lookup failed"),
                    CommandObservation(("loginctl",), 0, "1000 operator yes active\n", ""),
                ],
            ):
                with patch("workspace_mcp_manager.host.os.getuid", return_value=1000):
                    result = inspector._linger()
        self.assertTrue(result["enabled"])
        self.assertEqual(result["source"], "loginctl-list-users")

    @patch("workspace_mcp_manager.host.shutil.which")
    def test_doctor_fails_missing_required_component(self, which) -> None:
        mapping = {
            "python3": "/usr/bin/python3",
            "systemctl": "/usr/bin/systemctl",
            "coding-tools-mcp": "/opt/coding-tools-mcp",
            "tunnel-client": None,
            "loginctl": None,
            "ss": None,
        }
        which.side_effect = lambda name: mapping.get(name)
        with tempfile.TemporaryDirectory() as directory:
            inspector = HostInspector(self._paths(Path(directory)), environ={"PATH": "/usr/bin"})
            with patch.object(
                inspector,
                "_run",
                return_value=CommandObservation(("x",), 0, "running\n", ""),
            ):
                doctor = inspector.doctor()
        self.assertFalse(doctor["ok"])
        self.assertEqual(
            next(item for item in doctor["checks"] if item["name"] == "tunnel-client")["status"],
            "FAIL",
        )


if __name__ == "__main__":
    unittest.main()

