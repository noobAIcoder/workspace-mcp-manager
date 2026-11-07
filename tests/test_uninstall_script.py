import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UninstallScriptTests(unittest.TestCase):
    def test_modes_and_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'scripts/uninstall.sh', root / 'uninstall.sh')
            (root / 'install.sh').write_text('''#!/bin/bash
case "$1" in
 --maintenance-status) if [[ -f "${0%/*}/applied" ]]; then echo '{"ownership":"absent"}'; else echo '{"ownership":"standalone_distribution"}'; fi ;;
 --maintenance-preview) echo '{"ok":true,"action":"uninstall"}' ;;
 --maintenance-apply) cat > "${0%/*}/applied" ;;
 --recover-only) touch "${0%/*}/recovered" ;;
esac
''')
            def run(*args):
                return subprocess.run(['bash', str(root / 'uninstall.sh'), *args], input='', text=True, capture_output=True)
            self.assertEqual(run('--dry-run').returncode, 0)
            self.assertFalse((root / 'applied').exists())
            self.assertNotEqual(run().returncode, 0)
            self.assertNotEqual(run('--dry-run', '--confirmation', 'UNINSTALL').returncode, 0)
            self.assertNotEqual(run('--confirmation', 'yes').returncode, 0)
            self.assertEqual(run('--confirmation', 'UNINSTALL').returncode, 0)
            self.assertEqual(json.loads((root / 'applied').read_text())['action'], 'uninstall')
            self.assertEqual(run('--recover-only').returncode, 0)
            self.assertTrue((root / 'recovered').exists())
