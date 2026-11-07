from pathlib import Path
import os
import subprocess
import tempfile
import configparser
import sys
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GuidedHostTests(unittest.TestCase):
    @unittest.skipIf(os.getuid() == 0, 'guided configuration requires a regular account')
    def test_guided_handoff_continues_existing_instance_and_requires_verified_result(self):
        source = (ROOT / 'bootstrap.sh').read_text().split('\n[[ -n "$PREFIX" ]] || PREFIX=', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            prefix = home / 'custom'
            (prefix / 'bin').mkdir(parents=True)
            manager = prefix / 'bin/workspace-mcp-manager'
            identity = json.dumps({'account_home': str(home), 'manager_prefix': str(prefix)})
            manager.write_text("#!/bin/bash\nprintf '%s\\n' '" + identity + "'\n")
            manager.chmod(0o755)
            release = prefix / 'lib/workspace-mcp-manager/releases/dist-test'
            release.mkdir(parents=True)
            (release / 'bin').symlink_to(prefix / 'bin')
            tui = prefix / 'bin/workspace-mcp-manager-tui'
            script = home / 'setup.sh'
            script.write_text(source + '''
PREFIX="$HOME/custom"
pending=false
SETUP_EXIT=false
status='{"current":"dist-test","instances":{"one":{}}}'
getent() { printf 'user:x:1000:1000::%s:/bin/bash\\n' "$HOME"; }
setup_supported_host() { return 0; }
setup_prepare_host() { return 0; }
setup_inspect() { return 0; }
setup_install_task() { install_ready=true; }
setup_guided_task
''')
            for outcome in ('verified', 'incomplete', 'missing'):
                tui.write_text('''#!/bin/bash
[[ "$WORKSPACE_MCP_GUIDED_INSTANCE" == one ]] || exit 3
if [[ "$TEST_OUTCOME" != missing ]]; then
 printf '{"schema_version":1,"outcome":"%s","instance_id":"one"}' "$TEST_OUTCOME" > "$WORKSPACE_MCP_GUIDED_RESULT"
fi
''')
                tui.chmod(0o755)
                result = subprocess.run(['bash', str(script)], input='one\ny\n', text=True, capture_output=True,
                                        env={**os.environ, 'HOME': str(home), 'TEST_OUTCOME': outcome, 'SUDO_USER': ''})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual('Setup verified:' in result.stdout, outcome == 'verified')
                self.assertIn('Continue an existing instance', result.stdout)
            self.assertEqual((home / '.profile').read_text().count('export PATH='), 1)
            identity = json.dumps({'account_home': str(home / 'other'), 'manager_prefix': str(prefix)})
            manager.write_text("#!/bin/bash\nprintf '%s\\n' '" + identity + "'\n")
            result = subprocess.run(['bash', str(script)], input='one\ny\n', text=True, capture_output=True,
                                    env={**os.environ, 'HOME': str(home), 'TEST_OUTCOME': 'verified', 'SUDO_USER': ''})
            self.assertIn('does not match the selected target', result.stdout)
            self.assertNotIn('Setup verified:', result.stdout)

    def test_wsl_edit_preserves_unrelated_settings_and_is_idempotent(self):
        source = (ROOT / 'bootstrap.sh').read_text()
        code = source.split("<<'PYWSL' || return 1\n", 1)[1].split('\nPYWSL', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wsl.conf'
            path.write_text('[network]\ngenerateResolvConf=false\n[boot]\ncommand=echo hello\n[automount]\nroot=/mnt/custom\n')
            code = code.replace("'/etc/wsl.conf'", repr(str(path))).replace("dir='/etc'", 'dir=' + repr(directory))
            for _ in range(2):
                result = subprocess.run([sys.executable, '-c', code], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            settings = configparser.ConfigParser()
            settings.read(path)
            self.assertEqual(settings['network']['generateResolvConf'], 'false')
            self.assertEqual(settings['automount']['root'], '/mnt/custom')
            self.assertEqual(settings['boot']['command'], 'echo hello')
            self.assertEqual(settings['boot']['systemd'], 'true')

    def host(self, overrides, answer=''):
        source = (ROOT / 'bootstrap.sh').read_text().split('\n[[ -n "$PREFIX" ]] || PREFIX=', 1)[0]
        # Exercise the actual Bash stage with host operations replaced by fakes.
        script = source + '\n' + overrides + '\nsetup_prepare_host\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'setup.sh'
            path.write_text(script)
            return subprocess.run(['bash', str(path)], cwd=ROOT, input=answer,
                                  capture_output=True, text=True, timeout=10)

    def test_package_install_defaults_to_cancel(self):
        result = self.host("dpkg-query() { return 1; }; sudo() { echo SUDO_CALLED; }")
        self.assertIn('sudo apt-get install', result.stdout)
        self.assertNotIn('SUDO_CALLED', result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_failed_package_install_stops_before_configuration(self):
        result = self.host("dpkg-query() { return 1; }; sudo() { echo FAILED_APT; return 1; }; systemctl() { echo BAD_SYSTEMD; }", 'y\n')
        self.assertIn('Prerequisite installation failed', result.stdout)
        self.assertNotIn('BAD_SYSTEMD', result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_unavailable_native_systemd_stops_for_login_renewal(self):
        result = self.host("dpkg-query() { printf 'install ok installed'; }; systemctl() { return 1; }; uname() { echo native; }")
        self.assertIn('Log out and back', result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_wsl_change_cancellation_still_stops_for_restart(self):
        result = self.host("dpkg-query() { printf 'install ok installed'; }; systemctl() { return 1; }; uname() { echo microsoft; }; sudo() { echo BAD_SUDO; }", '\n')
        self.assertIn('wsl.exe --shutdown', result.stdout)
        self.assertNotIn('BAD_SUDO', result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_rerun_checks_actual_systemd_connection(self):
        result = self.host("dpkg-query() { printf 'install ok installed'; }; systemctl() { return 0; }")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('shutdown', result.stdout)


class NativeNobleRegression(unittest.TestCase):
    def test_native_noble_platform_is_accepted(self):
        source = (ROOT / 'bootstrap.sh').read_text()
        function = source.split('setup_supported_host() {', 1)[1].split('\nsetup_prepare_host()', 1)[0]
        harness = 'setup_supported_host() {' + function + '\nuname() { case "$1" in -m) echo aarch64;; -r) echo 6.8-native;; esac; }; setup_supported_host\n'
        for version, arch, kernel, expected in [
            ('24.04', 'aarch64', '6.8-native', 0),
            ('22.04', 'aarch64', '6.8-native', 0),
            ('24.04', 'x86_64', '6.6-microsoft-WSL2', 0),
            ('24.04', 'x86_64', '6.8-native', 1),
            ('24.04', 'aarch64', '6.6-microsoft-WSL2', 1),
            ('20.04', 'aarch64', '6.8-native', 1),
        ]:
            with self.subTest(version=version, arch=arch, kernel=kernel):
                candidate = harness.replace('. /etc/os-release;', 'ID=ubuntu; VERSION_ID=' + version + ';')
                candidate = candidate.replace('echo aarch64', 'echo ' + arch).replace('echo 6.8-native', 'echo ' + kernel)
                result = subprocess.run(['bash', '-c', candidate], capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
