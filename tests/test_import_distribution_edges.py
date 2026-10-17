from __future__ import annotations

import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile

from tests.test_distribution import DistributionFixture, INSTALLER, make_tunnel_archive, TUNNEL_IDENTITY


class ImportInstallerEdges(unittest.TestCase):
    def legacy_reboot(self, fixture):
        toolkit = fixture.prefix / 'lib/workspace-mcp'
        toolkit.mkdir(parents=True)
        (toolkit / 'VERSION').write_text('0.3.2\n')
        helper = toolkit / 'workspace-mcp-reboot'
        helper.write_text('#!/bin/sh\necho legacy-helper\n'); helper.chmod(0o755)
        command = fixture.prefix / 'bin/workspace-mcp-reboot'
        command.parent.mkdir(parents=True)
        command.symlink_to('../lib/workspace-mcp/workspace-mcp-reboot')
        return command, helper

    def test_verified_reboot_collision_is_replaced_without_changing_old_toolkit(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            command, helper = self.legacy_reboot(fixture)
            old = helper.read_bytes()
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.readlink(command), '../lib/workspace-mcp-manager/current/bin/workspace-mcp-reboot')
            self.assertEqual(helper.read_bytes(), old)
            self.assertEqual((fixture.home / '.local/state/workspace-mcp-manager/legacy-reboot-target.saved').read_text().strip(), '../lib/workspace-mcp/workspace-mcp-reboot')

    def test_precommit_recovery_restores_legacy_reboot_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            command, helper = self.legacy_reboot(fixture)
            result = fixture.run('--qualification-failpoint', 'during_current_commit')
            self.assertLess(result.returncode, 0, result.stderr)
            result = fixture.recover()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.readlink(command), '../lib/workspace-mcp/workspace-mcp-reboot')
            self.assertTrue(helper.is_file())

    def test_arm_tunnel_layout_and_entry_types_are_strict(self):
        source = INSTALLER.read_text()
        function = source[source.index('provision_tunnel_client() {'):source.index('stage_distribution_frontends() {')]
        harness = '''set -euo pipefail
lock_get() { case "$2" in architecture) printf aarch64;; expected_observable_identity) printf '%s' "$IDENTITY";; *) printf fixture;; esac; }
acquire_artifact() { printf '%s' "$ARTIFACT"; }
die() { echo "$*" >&2; exit 2; }
''' + function + '\nprovision_tunnel_client "$STAGE" unused\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'original.zip'; make_tunnel_archive(original, 'arm64')
            with zipfile.ZipFile(original) as archive:
                entries = [(info, archive.read(info)) for info in archive.infolist()]
            for case in ('valid','missing','unexpected','duplicate','unsafe','symlink'):
                with self.subTest(case=case):
                    selected = list(entries)
                    if case == 'missing': selected.pop()
                    if case in {'unexpected','unsafe'}: selected.append((zipfile.ZipInfo('extra' if case == 'unexpected' else '../escape'), b'data'))
                    if case == 'duplicate': selected.append(entries[0])
                    candidate = root / (case+'.zip')
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', UserWarning)
                        with zipfile.ZipFile(candidate, 'w') as archive:
                            for info, body in selected:
                                fresh = zipfile.ZipInfo(info.filename); fresh.external_attr = info.external_attr
                                if case == 'symlink' and info.filename == 'cloudflared': fresh.external_attr = (stat.S_IFLNK | 0o777) << 16
                                archive.writestr(fresh, body)
                    stage = root / case
                    result = subprocess.run(['bash','-c',harness], text=True, capture_output=True, env={**os.environ,'BOOTSTRAP_PYTHON':sys.executable,'ARTIFACT':str(candidate),'STAGE':str(stage),'IDENTITY':TUNNEL_IDENTITY})
                    self.assertEqual(result.returncode == 0, case == 'valid', result.stderr)
                    if case != 'valid': self.assertFalse(stage.exists())

    def test_host_profiles_are_explicit(self):
        source = INSTALLER.read_text()
        function = source[source.index('preflight_read_only() {'):source.index('current_target() {')]
        harness = '''set -euo pipefail
require_command() { :; }
bootstrap_python_check() { :; }
grep() { if [[ "$2" == '^ID=' ]]; then echo "ID=$OS_ID"; else echo "VERSION_ID=$OS_VERSION"; fi; }
uname() { if [[ "$1" == -r ]]; then printf '%s' "$KERNEL"; else printf '%s' "$ARCH"; fi; }
systemctl() { :; }
die() { echo "$*" >&2; exit 2; }
''' + function + '\npreflight_read_only\n'
        for os_id,version,arch,kernel,expected in [('ubuntu','22.04','aarch64','native',True),('ubuntu','24.04','x86_64','microsoft-WSL2',True),('ubuntu','22.04','aarch64','microsoft-WSL2',False),('ubuntu','24.04','x86_64','native',False),('ubuntu','22.04','x86_64','native',False),('debian','22.04','aarch64','native',False)]:
            with self.subTest(profile=(os_id,version,arch,kernel)):
                result = subprocess.run(['bash','-c',harness], text=True, capture_output=True, env={**os.environ,'OS_ID':os_id,'OS_VERSION':version,'ARCH':arch,'KERNEL':kernel})
                self.assertEqual(result.returncode == 0, expected, result.stderr)

    def test_uv_archives_require_exact_regular_binaries(self):
        source = INSTALLER.read_text()
        validation = source[source.index('validate_tar_paths() {'):source.index('provision_python() {')]
        provision = source[source.index('provision_uv() {'):source.index('write_manager_wrappers() {')]
        harness = '''set -euo pipefail
lock_get() { case "$2" in architecture) printf '%s' "$ARCH";; expected_observable_identity) printf 'uv 0.12.3';; *) printf fixture;; esac; }
acquire_artifact() { printf '%s' "$ARTIFACT"; }
die() { echo "$*" >&2; exit 2; }
''' + validation + provision + '\nprovision_uv "$STAGE" "$ARTIFACT_DIR"\n'
        for arch in ('x86_64','aarch64'):
            for case in ('valid','missing','duplicate','symlink','unsafe'):
                with self.subTest(arch=arch,case=case), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory); archive_path = root / 'uv.tar.gz'
                    prefix = 'uv-'+arch+'-unknown-linux-gnu'
                    with tarfile.open(archive_path,'w:gz') as archive:
                        info = tarfile.TarInfo(prefix); info.type = tarfile.DIRTYPE; info.mode = 0o755; archive.addfile(info)
                        names = ['uv','uvx']
                        if case == 'missing': names.pop()
                        if case == 'duplicate': names.append('uv')
                        for name in names:
                            filename = prefix+'/'+name
                            if case == 'unsafe' and name == 'uv': filename = prefix+'/../uv'
                            info = tarfile.TarInfo(filename); info.mode = 0o755
                            body = b'#!/bin/sh\necho "uv 0.12.3"\n'
                            if case == 'symlink' and name == 'uv':
                                info.type = tarfile.SYMTYPE; info.linkname = '/tmp/unsafe-import-uv'; archive.addfile(info)
                            else:
                                info.size = len(body); archive.addfile(info,io.BytesIO(body))
                    stage = root / 'stage'
                    result = subprocess.run(['bash','-c',harness], text=True, capture_output=True, env={**os.environ,'BOOTSTRAP_PYTHON':sys.executable,'ARCH':arch,'ARTIFACT':str(archive_path),'STAGE':str(stage),'ARTIFACT_DIR':str(root)})
                    self.assertEqual(result.returncode == 0, case == 'valid', result.stderr)
                    if case != 'valid': self.assertFalse(stage.exists())
