from __future__ import annotations

import unittest
from pathlib import Path


class CapabilityFreezeTests(unittest.TestCase):
    def test_all_reconciled_capabilities_are_explicitly_classified(self) -> None:
        root = Path(__file__).resolve().parents[1]
        lines = (root / "specs/capabilities.tsv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "id\tsource_class\tdisposition\ttarget\tname")
        capabilities = [line.split("\t", 4) for line in lines[1:] if line.strip()]
        self.assertEqual(len(capabilities), 130)
        self.assertEqual([row[0] for row in capabilities], [f"CAP-{i:03d}" for i in range(1, 131)])
        allowed = {"PRESERVE", "REPLACE", "RETIRE", "DEFER", "EXTERNAL"}
        for capability_id, source_class, disposition, target, name in capabilities:
            self.assertIn(source_class, {"A", "B", "C", "D", "C/D"}, capability_id)
            self.assertIn(disposition, allowed, capability_id)
            self.assertTrue(target, capability_id)
            self.assertTrue(name, capability_id)

    def test_freeze_records_source_digest_and_count(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "specs/m0-evidence.md").read_text(encoding="utf-8")
        self.assertIn("4daf6201f5b1c155759f44c9fd9f655f7bcae8097e66ab8266db8ca6e46a1892", text)
        self.assertIn("exactly 130 explicit dispositions", text)


if __name__ == "__main__":
    unittest.main()

