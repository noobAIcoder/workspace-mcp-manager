from __future__ import annotations

from pathlib import Path
import unittest


class P6QualificationScriptTests(unittest.TestCase):
    def test_authority_check_matches_quoted_runtime_command(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p6_wsl.sh").read_text(encoding="utf-8")
        self.assertIn("grep -F '\"_runtime\" \"tunnel\"'", text)
        self.assertNotIn("grep -F '_runtime tunnel'", text)

    def test_harness_can_resume_after_successful_first_apply(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p6_wsl.sh").read_text(encoding="utf-8")
        self.assertIn("RESUME_AFTER_FIRST_APPLY=false", text)
        self.assertIn('if [ -f "$APPLIED" ]; then', text)
        self.assertIn('RESUME_AFTER_FIRST_APPLY=true', text)
        self.assertIn('existing applied state is not resumable present+running state', text)
        self.assertIn('previous first apply remains ready', text)


if __name__ == "__main__":
    unittest.main()
