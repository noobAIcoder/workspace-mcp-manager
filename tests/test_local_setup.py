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
        self.assertIn("local working-tree snapshot", result.stdout)
        self.assertNotEqual(subprocess.run(["bash", str(script), "--invalid"], capture_output=True).returncode, 0)

    def test_working_tree_snapshot_is_clean_and_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            before = subprocess.check_output(['git', 'status', '--porcelain=v1', '--untracked-files=all'], cwd=ROOT)
            identities = []
            for name in ('one', 'two'):
                target = Path(temp) / name
                identities.append(subprocess.check_output(['python3', str(ROOT / 'scripts/source_snapshot.py'), str(ROOT), str(target)]))
                self.assertEqual(subprocess.check_output(['git', '-C', str(target), 'status', '--porcelain']), b'')
                self.assertEqual((target / 'src/workspace_mcp_manager/guided_setup.py').read_bytes(), (ROOT / 'src/workspace_mcp_manager/guided_setup.py').read_bytes())
            self.assertEqual(identities[0], identities[1])
            self.assertEqual(before, subprocess.check_output(['git', 'status', '--porcelain=v1', '--untracked-files=all'], cwd=ROOT))

    def test_edits_change_identity_and_unrelated_files_do_not(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            subprocess.check_call(['python3', str(ROOT / 'scripts/source_snapshot.py'), str(ROOT), str(source)], stdout=subprocess.DEVNULL)
            def capture(name):
                return subprocess.check_output(['python3', str(ROOT / 'scripts/source_snapshot.py'), str(source), str(Path(temp) / name)])
            original = capture('one')
            (source / 'notes.txt').write_text('unrelated local data')
            self.assertEqual(original, capture('two'))
            module = source / 'src/workspace_mcp_manager/local_snapshot_probe.py'
            module.write_text('VALUE = 1\n')
            changed = capture('three')
            self.assertNotEqual(original, changed)
            module.write_text('VALUE = 2\n')
            self.assertNotEqual(changed, capture('four'))
            module.unlink()
            self.assertEqual(original, capture('five'))
            (source / 'scripts/lock_validate.py').unlink()
            result = subprocess.run(['python3', str(ROOT / 'scripts/source_snapshot.py'), str(source), str(Path(temp) / 'bad')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing snapshot input', result.stderr)

    def test_symlinked_input_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            subprocess.check_call(['python3', str(ROOT / 'scripts/source_snapshot.py'), str(ROOT), str(source)], stdout=subprocess.DEVNULL)
            path = source / 'scripts/lock_validate.py'
            path.unlink()
            path.symlink_to(ROOT / 'scripts/lock_validate.py')
            result = subprocess.run(['python3', str(ROOT / 'scripts/source_snapshot.py'), str(source), str(Path(temp) / 'bad')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Symlinked snapshot input', result.stderr)
