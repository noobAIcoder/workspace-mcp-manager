import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LocalSetupTests(unittest.TestCase):
    def test_help_and_unknown_option(self):
        script = ROOT / "scripts/setup-local.sh"
        result = subprocess.run(["bash", str(script), "--help"], cwd="/tmp", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("clean local commit", result.stdout)
        self.assertNotEqual(subprocess.run(["bash", str(script), "--invalid"], capture_output=True).returncode, 0)

    def test_dirty_source_rejected_without_acquisition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "uncommitted").write_text("pending")
            result = subprocess.run(["bash", str(ROOT / "bootstrap.sh"), "--non-interactive", "--local-repo", str(root), "--check"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("commit local changes", result.stderr)
            self.assertIn("uncommitted", result.stderr)
