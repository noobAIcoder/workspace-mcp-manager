from __future__ import annotations

import unittest
from pathlib import Path


class Pm2QualificationScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.text = (root / "scripts/qualify_pm2_wsl.sh").read_text(encoding="utf-8")

    def test_harness_is_read_only_manager_qual_and_curses_scoped(self) -> None:
        self.assertIn('INSTANCE_ID="${INSTANCE_ID:-manager-qual}"', self.text)
        self.assertIn("--snapshot --instance", self.text)
        self.assertIn("--smoke", self.text)
        self.assertIn("script -qfec", self.text)
        self.assertIn("install_toolkit.sh", self.text)
        self.assertNotIn("install_toolkit.py", self.text)
        self.assertIn("PM2_TUI_QUALIFICATION=PASS", self.text)
        self.assertNotIn(" instance apply ", self.text)
        self.assertNotIn(" instance update ", self.text)
        self.assertNotIn(" access add-", self.text)
        self.assertNotIn("_runtime", self.text)


if __name__ == "__main__":
    unittest.main()
