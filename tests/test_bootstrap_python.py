from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapPythonTests(unittest.TestCase):
    def validate(self, data):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'lock.json'
            lock.write_text(data)
            return subprocess.run([sys.executable, str(ROOT / 'scripts/lock_validate.py'), str(lock),
                                   str(ROOT / 'requirements-coding-tools.lock'), 'x86_64'], capture_output=True, text=True)

    def test_valid_lock_and_strict_rejections(self):
        original = (ROOT / 'install-versions.lock').read_text()
        self.assertEqual(self.validate(original).returncode, 0)
        for source in (original.replace('"schema_version": 1', '"schema_version": true'),
                       original.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
                       original.replace('"schema_version": 1', '"schema_version": NaN')):
            self.assertNotEqual(self.validate(source).returncode, 0)

    def test_no_python_menu_still_offers_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'setup.sh'
            shutil.copy2(ROOT / 'bootstrap.sh', script)
            result = subprocess.run(['bash', str(script), '--interactive'], input='0\n', text=True,
                                    capture_output=True, env={**os.environ, 'WORKSPACE_MCP_BOOTSTRAP_PYTHON': '/bin/false'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Installation state: unknown', result.stdout)
            self.assertIn('7. Install and configure everything', result.stdout)

    def test_runtime_failures_and_local_checks_prevent_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            checkout = work / 'checkout'
            (checkout / 'scripts').mkdir(parents=True)
            for name in ('install-versions.lock', 'requirements-coding-tools.lock'):
                shutil.copy2(ROOT / name, checkout / name)
            shutil.copy2(ROOT / 'scripts/lock_validate.py', checkout / 'scripts/lock_validate.py')
            shell = '''set -eu
source "$1/scripts/bootstrap-python.sh"
die() { printf '%s\\n' "$*" >&2; exit 2; }
require_command() { command -v "$1" >/dev/null; }
select_bootstrap_python() { [[ "$1" == 10 ]]; }
uname() { printf 'x86_64\\n'; }
curl() { printf 'corrupt archive' > "$8"; }
tar() { printf 'UNSAFE EXTRACTION\\n'; return 1; }
acquire_bootstrap_python "$2" "$3"
'''
            for mode, expected in (('digest', 'digest mismatch'), ('interrupted', 'Python acquisition failed'),
                                   ('local', 'local checks never download runtimes')):
                with self.subTest(mode=mode):
                    candidate = shell
                    if mode == 'interrupted':
                        candidate = candidate.replace('printf \'corrupt archive\' > "$8"', 'return 130')
                    if mode == 'local':
                        candidate = candidate.replace('acquire_bootstrap_python "$2" "$3"', 'acquire_bootstrap_python "$2" "$3" false')
                    result = subprocess.run(['bash', '-c', candidate, 'test', str(ROOT), str(checkout), str(work)],
                                            capture_output=True, text=True, env={**os.environ, 'WORKSPACE_MCP_BOOTSTRAP_PYTHON': sys.executable})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertNotIn('UNSAFE EXTRACTION', result.stdout)

    def test_interpreter_selection_returns_executable_not_shell_function(self):
        shell = '''set -eu
source "$1/scripts/bootstrap-python.sh"
python3() { "$WORKSPACE_MCP_BOOTSTRAP_PYTHON" "$@"; }
select_bootstrap_python 10
[[ "$WORKSPACE_MCP_BOOTSTRAP_PYTHON" == /* ]]
python3 -c 'import json'
'''
        result = subprocess.run(['bash', '-c', shell, 'test', str(ROOT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
