from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from workspace_mcp_manager.errors import ManagerError
from workspace_mcp_manager.runtime import parse_tunnel_environment


class RuntimeTests(unittest.TestCase):
    def test_tunnel_environment_accepts_plain_export_and_quotes(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.env"
            path.write_text(
                "# comment\nexport CONTROL_PLANE_API_KEY='secret value'\nOTHER=value\n",
                encoding="utf-8",
            )
            values = parse_tunnel_environment(path)
            self.assertEqual(values["CONTROL_PLANE_API_KEY"], "secret value")
            self.assertEqual(values["OTHER"], "value")

    def test_tunnel_environment_requires_api_key(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.env"
            path.write_text("OTHER=value\n", encoding="utf-8")
            with self.assertRaises(ManagerError):
                parse_tunnel_environment(path)


if __name__ == "__main__":
    unittest.main()
