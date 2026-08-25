from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.sh"
COMPAT_INSTALLER = ROOT / "scripts" / "install_toolkit.sh"
LEGACY_PYTHON_INSTALLER = ROOT / "scripts" / "install_toolkit.py"


class ToolkitInstallerAuthorityTests(unittest.TestCase):
    def test_shell_installer_is_the_only_distribution_authority(self) -> None:
        self.assertTrue(INSTALLER.is_file())
        self.assertFalse(LEGACY_PYTHON_INSTALLER.exists())
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("construct_candidate", text)
        self.assertIn("recover_transaction", text)
        self.assertIn("commit_current", text)
        self.assertIn("publish_receipt", text)

    def test_legacy_frontend_is_exec_only(self) -> None:
        self.assertTrue(COMPAT_INSTALLER.is_file())
        text = COMPAT_INSTALLER.read_text(encoding="utf-8")
        self.assertIn('exec "$SCRIPT_DIR/install.sh" "$@"', text)
        for forbidden in (
            "construct_candidate",
            "stage_release",
            "pip install",
            "atomic_symlink",
            "recover_transaction",
            "distribution-transaction",
        ):
            self.assertNotIn(forbidden, text)

    def test_distribution_installer_never_invokes_sudo(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("sudo ", text)
        self.assertNotIn("sudo\t", text)

    def test_distribution_python_and_uv_are_candidate_owned(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('"$stage/runtimes/python/bin/python3"', text)
        self.assertIn('"$stage/tools/uv/uv"', text)
        self.assertIn("--require-hashes", text)
        self.assertIn("--no-index", text)


if __name__ == "__main__":
    unittest.main()
