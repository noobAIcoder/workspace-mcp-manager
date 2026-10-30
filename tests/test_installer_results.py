import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallerResultTests(unittest.TestCase):
    def result(self, payload):
        source = (ROOT / "bootstrap.sh").read_text()
        function = source[source.index("setup_result() {"):source.index("\nsetup_summary() {")]
        return subprocess.run(["bash", "-c", function + "\nsetup_result"],
                              input=payload, text=True, capture_output=True)

    def test_incomplete_result_cannot_report_completion(self):
        for payload in ('{}', '{"ok":true}', "[]", "invalid", '{"ok":"true"}'):
            with self.subTest(payload=payload):
                result = self.result(payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Completed:", result.stdout)

    def test_reported_failure_returns_failure(self):
        result = self.result('{"ok":false,"error":"specific failure"}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("specific failure", result.stdout)

    def test_noop_is_explained(self):
        result = self.result('{"ok":true,"action":"noop"}')
        self.assertEqual(result.returncode, 0)
        self.assertIn("already current", result.stdout)

    def test_conflicting_modes_fail_before_creating_target(self):
        import tempfile
        cases = [
            ('bootstrap.sh', ['--interactive', '--non-interactive']),
            ('bootstrap.sh', ['--non-interactive', '--check', '--review']),
            ('bootstrap.sh', ['--non-interactive', '--check', '--configure']),
            ('scripts/install.sh', ['--maintenance-status', '--maintenance-apply']),
            ('scripts/install.sh', ['--recover-only', '--upgrade']),
            ('scripts/upgrade.sh', ['--check', '--configure']),
        ]
        for script, args in cases:
            with self.subTest(script=script, args=args), tempfile.TemporaryDirectory() as temp:
                home = Path(temp) / 'account'
                result = subprocess.run(['bash', str(ROOT / script), *args,
                                         '--account-home', str(home)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(home.exists())

    def test_missing_option_value_is_actionable(self):
        for script in ('bootstrap.sh', 'scripts/install.sh', 'scripts/upgrade.sh'):
            result = subprocess.run(['bash', str(ROOT / script), '--prefix', '--check'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('--prefix requires a value', result.stderr)

    def test_direct_install_guidance_precedes_target_mutation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'account'
            result = subprocess.run(['bash', str(ROOT / 'scripts/install.sh'),
                                     '--account-home', str(home)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('bash bootstrap.sh', result.stderr)
            self.assertIn('bash scripts/setup-local.sh', result.stderr)
            self.assertFalse(home.exists())
