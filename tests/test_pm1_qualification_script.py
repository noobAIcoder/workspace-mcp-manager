from __future__ import annotations

import unittest
from pathlib import Path


class Pm1QualificationScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.text = (root / "scripts/qualify_pm1_wsl.sh").read_text(encoding="utf-8")

    def test_harness_is_manager_qual_scoped_and_reversible(self) -> None:
        self.assertIn('INSTANCE_ID="${INSTANCE_ID:-manager-qual}"', self.text)
        self.assertIn('cp "$REGISTRY" "$ORIGINAL_DECL"', self.text)
        self.assertIn('cp "$WORKSPACE/.git/config" "$ORIGINAL_GIT_CONFIG"', self.text)
        self.assertIn('cp "$ORIGINAL_DECL" "$REGISTRY"', self.text)
        self.assertIn('cp "$ORIGINAL_GIT_CONFIG" "$WORKSPACE/.git/config"', self.text)
        self.assertIn('cmp -s "$ORIGINAL_DECL" "$REGISTRY"', self.text)
        self.assertIn('cmp -s "$ORIGINAL_GIT_CONFIG" "$WORKSPACE/.git/config"', self.text)
        self.assertNotIn("leadbot", self.text.lower())
        self.assertNotIn("vigilus", self.text.lower())


if __name__ == "__main__":
    unittest.main()
