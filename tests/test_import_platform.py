import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PlatformLocksTests(unittest.TestCase):
    def test_arm_lock_preserves_versions_and_dependency_identity(self):
        x86 = json.loads((ROOT / 'install-versions.lock').read_text())
        arm = json.loads((ROOT / 'install-versions.aarch64.lock').read_text())
        self.assertEqual(arm['architecture'], 'aarch64')
        for name, component in arm['components'].items():
            self.assertEqual(component['architecture'], 'aarch64')
            self.assertEqual(component['version'], x86['components'][name]['version'])
            self.assertEqual(component['dependencies'], x86['components'][name]['dependencies'])
        self.assertIn('linux-arm64.zip', arm['components']['tunnel_client']['acquisition_location'])
