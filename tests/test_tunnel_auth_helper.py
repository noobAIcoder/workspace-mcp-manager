from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.runtime import parse_tunnel_environment
from workspace_mcp_manager.tunnel_auth_helper import (
    TunnelCredentialSetupError,
    configure_tunnel_credential,
    tunnel_credential_ready,
)


class TunnelCredentialHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.target = self.home / ".config/tunnel-client/runtime.env"

    def test_setup_creates_private_file_and_runtime_parser_accepts_it(self) -> None:
        supplied = "sk-control-test-123"
        self.assertFalse(tunnel_credential_ready(self.target))
        result = configure_tunnel_credential(
            self.target, home=self.home, prompt=lambda _: supplied
        )
        self.assertTrue(result)
        self.assertTrue(tunnel_credential_ready(self.target))
        self.assertEqual(
            parse_tunnel_environment(self.target)["CONTROL_PLANE_API_KEY"], supplied
        )
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.parent.stat().st_mode), 0o700)

    def test_missing_key_in_existing_env_is_repaired_without_losing_other_fields(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_text("OTHER_VALUE=preserve\n# keep comment\n", encoding="utf-8")
        self.assertTrue(configure_tunnel_credential(
            self.target, home=self.home, prompt=lambda _: "new-key"
        ))
        values = parse_tunnel_environment(self.target)
        self.assertEqual(values["OTHER_VALUE"], "preserve")
        self.assertEqual(values["CONTROL_PLANE_API_KEY"], "new-key")
        self.assertIn("# keep comment", self.target.read_text())

    def test_existing_api_key_is_replaced_without_duplicates(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_text(
            "export CONTROL_PLANE_API_KEY=old-key\nCONTROL_PLANE_API_KEY=duplicate\n"
        )
        self.assertTrue(configure_tunnel_credential(
            self.target, home=self.home, prompt=lambda _: "new-key"
        ))
        self.assertEqual(
            self.target.read_text().count("CONTROL_PLANE_API_KEY="), 1
        )
        self.assertEqual(
            parse_tunnel_environment(self.target)["CONTROL_PLANE_API_KEY"], "new-key"
        )

    def test_cancel_and_invalid_value_do_not_create_file(self) -> None:
        self.assertFalse(configure_tunnel_credential(
            self.target, home=self.home, prompt=lambda _: ""
        ))
        self.assertFalse(self.target.exists())
        with self.assertRaises(TunnelCredentialSetupError):
            configure_tunnel_credential(
                self.target, home=self.home, prompt=lambda _: "invalid value\n"
            )
        self.assertFalse(self.target.exists())

    def test_symlinked_file_and_directory_are_rejected(self) -> None:
        outside = self.home / "outside"
        outside.write_text("untouched")
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(outside)
        with self.assertRaisesRegex(TunnelCredentialSetupError, "symlink"):
            configure_tunnel_credential(
                self.target, home=self.home, prompt=lambda _: "any-key"
            )
        self.assertEqual(outside.read_text(), "untouched")
        self.target.unlink()
        self.target.parent.rmdir()
        self.target.parent.symlink_to(self.home)
        with self.assertRaisesRegex(TunnelCredentialSetupError, "symlink"):
            configure_tunnel_credential(
                self.target, home=self.home, prompt=lambda _: "any-key"
            )

    def test_external_path_is_not_written(self) -> None:
        other = self.home / "other.env"
        with self.assertRaisesRegex(TunnelCredentialSetupError, "supports only"):
            configure_tunnel_credential(
                other, home=self.home, prompt=lambda _: "any-key"
            )
        self.assertFalse(other.exists())


if __name__ == "__main__":
    unittest.main()
