from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from workspace_mcp_manager.maintenance import Maintenance, MaintenanceError

ROOT = Path(__file__).resolve().parents[1]


def release(root: Path, char: str) -> Path:
    path = root / 'releases' / ('dist-' + char * 40)
    path.mkdir(parents=True)
    for name in ('workspace-mcp-manager', 'workspace-mcp-manager-tui', 'workspace-mcp-reboot', 'workspace-mcp-manager-upgrade'):
        file = path / 'bin' / name
        file.parent.mkdir(exist_ok=True)
        file.write_text('#!/bin/sh\nexit 0\n')
        file.chmod(0o755)
    manifest = []
    for file in sorted(path.rglob('*')):
        if file.is_file():
            manifest.append({'path': file.relative_to(path).as_posix(), 'type': 'file', 'mode': '0755', 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    data = {'schema_version': 3, 'distribution_id': path.name, 'release_commit': char * 40,
            'product_version': '0.2.0', 'repository': 'https://github.com/noobAIcoder/workspace-mcp-manager.git',
            'release_ref': 'refs/heads/release', 'manager_source_digest': 'a' * 64, 'locked_inputs': {},
            'components': {}, 'manifest_scope': 'all-immutable-files-except-install.json; install.json-sha256-is-anchored-by-receipt',
            'content_manifest': manifest}
    (path / 'install.json').write_text(json.dumps(data))
    return path


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.prefix = self.home / '.local'
        self.root = self.prefix / 'lib/workspace-mcp-manager'
        self.a = release(self.root, 'a')
        self.b = release(self.root, 'b')
        (self.root / 'current').symlink_to('releases/' + self.a.name)
        (self.prefix / 'bin').mkdir()
        for name in ('workspace-mcp-manager', 'workspace-mcp-manager-tui', 'workspace-mcp-reboot', 'workspace-mcp-manager-upgrade'):
            (self.prefix / 'bin' / name).symlink_to('../lib/workspace-mcp-manager/current/bin/' + name)
        self.service = Maintenance(self.home, self.prefix)
        self.runtime = mock.patch.object(self.service, 'validate_runtime')
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.compat = mock.patch.object(self.service, 'validate_compatibility')
        self.compat.start()
        self.addCleanup(self.compat.stop)
        self.units = mock.patch.object(self.service, 'remaining_units', return_value=[])
        self.units.start()
        self.removal = mock.patch.object(self.service, 'removal_preview', return_value=[])
        self.removal.start()
        self.addCleanup(self.removal.stop)
        self.addCleanup(self.units.stop)

    def test_preview_is_read_only_and_releases_are_local(self):
        before = sorted(str(p) for p in self.home.rglob('*'))
        preview = self.service.preview('rollback', self.b.name)
        self.assertTrue(preview['ok'])
        self.assertEqual(preview['target'], self.b.name)
        self.assertEqual(before, sorted(str(p) for p in self.home.rglob('*')))

    def test_standalone_requires_upgrade_entrypoint(self):
        (self.prefix / 'bin/workspace-mcp-manager-upgrade').unlink()
        with self.assertRaisesRegex(MaintenanceError, 'missing entry point: workspace-mcp-manager-upgrade'):
            self.service.status()

    def test_rollback_preserves_declarations_and_runtime_paths(self):
        registry = self.home / '.config/workspace-mcp-manager/instances'
        registry.mkdir(parents=True)
        declaration = registry / 'one.json'
        declaration.write_text(json.dumps({'instance_id': 'one', 'lifecycle': {'deployment': 'present', 'runtime': 'running'}, 'mcp': {'binary': str(self.a / 'runtime')}}))
        original = declaration.read_bytes()
        self.service.apply(self.service.preview('rollback', self.b.name), 'yes')
        self.assertEqual(os.readlink(self.root / 'current'), 'releases/' + self.b.name)
        self.assertEqual(declaration.read_bytes(), original)
        self.assertFalse(self.service.journal.exists())

    def test_stale_preview_and_changed_files_are_rejected(self):
        preview = self.service.preview('uninstall')
        (self.a / 'bin/workspace-mcp-manager').write_text('changed')
        with self.assertRaises(MaintenanceError):
            self.service.apply(preview, 'UNINSTALL')
        self.assertTrue(self.root.exists())

    def test_unknown_root_file_blocks_uninstall(self):
        (self.root / 'foreign').write_text('keep')
        with self.assertRaises(MaintenanceError):
            self.service.preview('uninstall')

    def test_confirmation_is_required(self):
        with self.assertRaises(MaintenanceError):
            self.service.apply(self.service.preview('uninstall'), 'yes')
        self.assertTrue(self.root.exists())

    def test_uninstall_preserves_user_data(self):
        config = self.home / '.config/workspace-mcp-manager'
        config.mkdir(parents=True)
        credential = config / 'credential'
        credential.write_text('secret')
        self.service.apply(self.service.preview('uninstall'), 'UNINSTALL')
        self.assertFalse(self.root.exists())
        self.assertEqual(credential.read_text(), 'secret')
        self.assertFalse((self.prefix / 'bin/workspace-mcp-manager').is_symlink())
        self.assertTrue((self.service.state / 'backups').is_dir())

    def test_failed_instance_removal_retains_manager_and_can_retry(self):
        registry = self.home / '.config/workspace-mcp-manager/instances'
        registry.mkdir(parents=True)
        for name in ('one', 'two'):
            (registry / (name + '.json')).write_text(json.dumps({'instance_id': name, 'lifecycle': {'deployment': 'present', 'runtime': 'running'}}))
        def remove(name):
            path = registry / (name + '.json')
            data = json.loads(path.read_text())
            data['lifecycle'] = {'deployment': 'absent', 'runtime': 'stopped'}
            path.write_text(json.dumps(data))
            # The real lifecycle worker saves absent intent before reconciliation.
            if name == 'two':
                raise MaintenanceError('removal failed')
        with mock.patch.object(self.service, 'remove_instance', side_effect=remove):
            with self.assertRaises(MaintenanceError):
                self.service.apply(self.service.preview('uninstall'), 'UNINSTALL')
        self.assertTrue(self.root.exists())
        self.assertEqual(json.loads((registry / 'one.json').read_text())['lifecycle']['deployment'], 'absent')
        self.assertIn('two', self.service.recover()['remaining_instances'])
        self.assertTrue(list((self.service.state / 'backups').rglob('one.json')))

    def test_rollback_recovery_converges_after_switch(self):
        preview = self.service.preview('rollback', self.b.name)
        with mock.patch.object(self.service, 'write_receipt', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.service.apply(preview, 'yes')
        self.assertTrue(self.service.journal.exists())
        self.service.recover()
        self.assertEqual(os.readlink(self.root / 'current'), 'releases/' + self.b.name)
        self.assertFalse(self.service.journal.exists())

    def test_uninstall_recovery_after_quarantine(self):
        with mock.patch.object(self.service, 'finish_uninstall', side_effect=OSError('interrupted')):
            with self.assertRaises(MaintenanceError):
                self.service.apply(self.service.preview('uninstall'), 'UNINSTALL')
        self.assertFalse(self.root.exists())
        self.service.recover()
        self.assertFalse(self.service.journal.exists())
        self.assertEqual(self.service.status()['ownership'], 'absent')

    def test_malformed_journal_cannot_delete_arbitrary_paths(self):
        self.service.state.mkdir(parents=True)
        self.service.journal.write_text(json.dumps({'operation': 'uninstall', 'quarantine': str(self.home)}))
        with self.assertRaises(MaintenanceError):
            self.service.recover()
        self.assertTrue(self.home.is_dir())

    def test_partial_quarantine_cleanup_can_resume_but_changed_content_blocks(self):
        with mock.patch.object(self.service, 'finish_uninstall', side_effect=OSError('interrupted')):
            with self.assertRaises(MaintenanceError):
                self.service.apply(self.service.preview('uninstall'), 'UNINSTALL')
        value = self.service.load_journal()
        quarantine = self.service.quarantine(value)
        (quarantine / 'releases' / self.a.name / 'bin/workspace-mcp-manager').unlink()
        (quarantine / 'unexpected').write_text('keep')
        with self.assertRaises(MaintenanceError):
            self.service.recover()
        (quarantine / 'unexpected').unlink()
        self.service.recover()
        self.assertFalse(quarantine.exists())

    def test_preview_includes_instance_removal_effects(self):
        self.service.removal_preview.return_value = [{'instance_id': 'one', 'operations': [{'operation': 'STOP', 'target': 'one.service'}]}]
        result = self.service.preview('uninstall')
        self.assertEqual(result['deployment_removal'][0]['instance_id'], 'one')

    def test_new_registry_writes_are_blocked_during_uninstall(self):
        from workspace_mcp_manager.registry import InstanceRegistry
        from workspace_mcp_manager.domain import DesiredInstance
        from workspace_mcp_manager.errors import ManagerError
        from tests.helpers import sample_v2_instance
        data = sample_v2_instance()
        data['instance_id'] = 'new'
        desired = DesiredInstance.from_dict(data)
        self.service.state.mkdir(parents=True)
        self.service.journal.write_text(json.dumps({'operation': 'uninstall'}))
        with self.assertRaises(ManagerError):
            InstanceRegistry(self.service.registry).create(desired)

    def test_recovery_does_not_remove_changed_entrypoints(self):
        with mock.patch.object(self.service, 'finish_uninstall', side_effect=OSError('interrupted')):
            with self.assertRaises(MaintenanceError):
                self.service.apply(self.service.preview('uninstall'), 'UNINSTALL')
        path = self.prefix / 'bin/workspace-mcp-manager'
        path.unlink()
        path.write_text('foreign')
        with self.assertRaises(MaintenanceError):
            self.service.recover()
        self.assertEqual(path.read_text(), 'foreign')

    def test_rollback_compatibility_failure_leaves_authority_unchanged(self):
        self.service.validate_compatibility.side_effect = MaintenanceError('unsupported declaration')
        with self.assertRaises(MaintenanceError):
            self.service.preview('rollback', self.b.name)
        self.assertEqual(os.readlink(self.root / 'current'), 'releases/' + self.a.name)
        self.assertFalse(self.service.journal.exists())

    def test_uninstall_saves_profiles_logs_and_absent_records_for_reinstallation(self):
        registry = self.service.registry
        registry.mkdir(parents=True)
        record = registry / 'one.json'
        original = {'instance_id': 'one', 'lifecycle': {'deployment': 'present', 'runtime': 'running'}}
        record.write_text(json.dumps(original))
        profile = self.service.config / 'tunnel-profiles/one.yaml'
        profile.parent.mkdir()
        profile.write_text('profile contents')
        log = self.service.state / 'instances/one/mcp.log'
        log.parent.mkdir(parents=True)
        log.write_text('retained log')
        credential = self.service.config / 'github/one/hosts.yml'
        credential.parent.mkdir(parents=True)
        credential.write_text('secret')
        def remove(name):
            data = dict(original)
            data['lifecycle'] = {'deployment': 'absent', 'runtime': 'stopped'}
            record.write_text(json.dumps(data))
            profile.unlink()
            log.unlink()
        with mock.patch.object(self.service, 'remove_instance', side_effect=remove):
            result = self.service.apply(self.service.preview('uninstall'), 'UNINSTALL')
        backup = Path(result['backup'])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads((backup / 'declarations/one.json').read_text()), original)
        self.assertEqual((backup / 'tunnel-profiles/one.yaml').read_text(), 'profile contents')
        self.assertEqual((backup / 'instances/one/mcp.log').read_text(), 'retained log')
        self.assertEqual(credential.read_text(), 'secret')
        # Simulate a later distribution installation: retained records stay absent.
        next_release = release(self.root, 'c')
        (self.root / 'current').symlink_to('releases/' + next_release.name)
        for name in ('workspace-mcp-manager', 'workspace-mcp-manager-tui', 'workspace-mcp-reboot', 'workspace-mcp-manager-upgrade'):
            (self.prefix / 'bin' / name).symlink_to('../lib/workspace-mcp-manager/current/bin/' + name)
        self.assertEqual(self.service.status()['instances']['one']['lifecycle'], {'deployment': 'absent', 'runtime': 'stopped'})

    def test_dangling_journal_and_foreign_receipt_are_blocked(self):
        self.service.state.mkdir(parents=True)
        self.service.journal.symlink_to('missing')
        with self.assertRaises(MaintenanceError):
            self.service.preview('uninstall')
        with self.assertRaises(MaintenanceError):
            self.service.recover()
        self.service.journal.unlink()
        (self.service.state / 'distribution.json').symlink_to(self.home / 'foreign')
        with self.assertRaises(MaintenanceError):
            self.service.preview('uninstall')

    def test_owned_historical_releases_are_backed_up_but_not_rollback_targets(self):
        legacy = self.root / 'releases' / ('c' * 64)
        (legacy / 'src/workspace_mcp_manager').mkdir(parents=True)
        (legacy / 'tui-runtime/site-packages/textual').mkdir(parents=True)
        (legacy / 'requirements-tui.lock').write_text('historical')
        (legacy / 'install.json').write_text('{"schema_version":2}')
        (legacy / 'bin').mkdir()
        for name in ('workspace-mcp-manager', 'workspace-mcp-manager-tui', 'workspace-mcp-reboot'):
            (legacy / 'bin' / name).write_text('historical command')
        preview = self.service.preview('uninstall')
        self.assertIn(legacy.name, preview['status']['retained_legacy'])
        self.assertNotIn(legacy.name, [item['id'] for item in preview['status']['releases']])
        result = self.service.apply(preview, 'UNINSTALL')
        self.assertEqual((Path(result['backup']) / 'legacy-releases' / legacy.name / 'requirements-tui.lock').read_text(), 'historical')

    def test_rollback_recovery_before_switch_preserves_original_release(self):
        original_replace = os.replace
        def interrupt(source, destination):
            if destination == self.root / 'current':
                raise OSError('interrupted before switch')
            return original_replace(source, destination)
        with mock.patch('workspace_mcp_manager.maintenance.os.replace', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.service.apply(self.service.preview('rollback', self.b.name), 'yes')
        self.service.recover()
        self.assertEqual(os.readlink(self.root / 'current'), 'releases/' + self.a.name)
        self.assertFalse(self.service.journal.exists())

    def test_quiescent_registry_blocks_creation_of_an_unpreviewed_instance(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from workspace_mcp_manager.registry import InstanceRegistry
        from workspace_mcp_manager.domain import DesiredInstance
        from tests.helpers import sample_v2_instance
        desired = DesiredInstance.from_dict(sample_v2_instance())
        started = Event()
        finished = Event()
        def create():
            started.set()
            result = InstanceRegistry(self.service.registry).create(desired)
            finished.set()
            return result
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.service.quiescent_instances([]):
                future = executor.submit(create)
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(0.1))
            self.assertTrue(future.result(timeout=2).is_file())

    def run_recovery_script(self, script):
        import sys
        directory = Path(self.tmp.name) / 'python-bin'
        directory.mkdir(exist_ok=True)
        python = directory / 'python3'
        if not python.is_symlink():
            python.symlink_to(sys.executable)
        return subprocess.run(['bash', str(script), '--recover-only', '--account-home', str(self.home), '--prefix', str(self.prefix)], env={**os.environ, 'PATH': str(directory) + os.pathsep + os.environ['PATH']}, text=True, capture_output=True, timeout=20)

    def test_installer_recovery_returns_one_compatible_json_result(self):
        with mock.patch.object(self.service, 'write_receipt', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.service.apply(self.service.preview('rollback', self.b.name), 'yes')
        result = self.run_recovery_script(ROOT / 'scripts/install.sh')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['recovery_result'], 'rollback-converged')
        self.assertEqual(payload['ownership'], 'standalone_distribution')

    def test_offline_capsule_recovers_after_program_files_move(self):
        with mock.patch.object(self.service, 'finish_uninstall', side_effect=OSError('interrupted')):
            with self.assertRaises(MaintenanceError):
                self.service.apply(self.service.preview('uninstall'), 'UNINSTALL')
        self.assertFalse(self.root.exists())
        result = self.run_recovery_script(self.service.state / 'setup-recovery/install.sh')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['recovery_result'], 'uninstall-completed')
        self.assertEqual(payload['ownership'], 'absent')


class PredecessorInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.prefix = self.home / '.local'
        self.root = self.prefix / 'lib/workspace-mcp-manager'
        self.release = self.root / 'releases' / ('c' * 64)
        (self.release / 'src/workspace_mcp_manager').mkdir(parents=True)
        (self.release / 'tui-runtime/site-packages/textual').mkdir(parents=True)
        (self.release / 'requirements-tui.lock').write_text('# predecessor\n')
        (self.release / 'install.json').write_text('{"schema_version":2}\n')
        (self.release / 'bin').mkdir()
        (self.prefix / 'bin').mkdir()
        for name in ('workspace-mcp-manager', 'workspace-mcp-manager-tui', 'workspace-mcp-reboot'):
            command = self.release / 'bin' / name
            command.write_text('#!/bin/sh\nexit 0\n')
            command.chmod(0o755)
            (self.prefix / 'bin' / name).symlink_to('../lib/workspace-mcp-manager/current/bin/' + name)
        (self.root / 'current').symlink_to('releases/' + self.release.name)
        registry = self.home / '.config/workspace-mcp-manager/instances'
        registry.mkdir(parents=True)
        (registry / 'one.json').write_text('{"instance_id":"one"}\n')
        self.service = Maintenance(self.home, self.prefix)

    def test_offline_status_recognizes_predecessor_without_systemd(self):
        before = {path: path.read_bytes() for path in self.home.rglob('*') if path.is_file()}
        with mock.patch('workspace_mcp_manager.maintenance.subprocess.run', side_effect=AssertionError('inspection must be offline')):
            status = self.service.status()
        self.assertEqual(status['ownership'], 'recognized_existing')
        self.assertEqual(status['current'], self.release.name)
        self.assertEqual(status['releases'], [])
        self.assertEqual(set(status['instances']), {'one'})
        self.assertFalse(status['recovery_required'])
        self.assertEqual(before, {path: path.read_bytes() for path in self.home.rglob('*') if path.is_file()})

    def test_menu_offers_migration_and_open_manager(self):
        result = subprocess.run(['bash', str(ROOT / 'bootstrap.sh'), '--interactive', '--account-home', str(self.home)], input='0\n', text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1. Migrate\n', result.stdout)
        self.assertIn('6. Open manager\n', result.stdout)
        self.assertIn('Instances: 1', result.stdout)
        self.assertNotIn('Inspection:', result.stdout)

    def test_predecessor_cannot_use_standalone_maintenance(self):
        for action in ('rollback', 'uninstall'):
            with self.subTest(action=action), self.assertRaisesRegex(MaintenanceError, 'requires a validated standalone'):
                self.service.preview(action, 'dist-' + 'a' * 40)

    def test_missing_predecessor_content_is_rejected(self):
        for relative in ('requirements-tui.lock', 'install.json', 'bin/workspace-mcp-manager'):
            with self.subTest(relative=relative):
                path = self.release / relative
                content, mode = path.read_bytes(), path.stat().st_mode
                path.unlink()
                with self.assertRaises(MaintenanceError):
                    self.service.status()
                path.write_bytes(content)
                path.chmod(mode)

    def test_nonexecutable_predecessor_command_is_rejected(self):
        (self.release / 'bin/workspace-mcp-manager').chmod(0o644)
        with self.assertRaises(MaintenanceError):
            self.service.status()

    def test_missing_predecessor_runtime_is_rejected(self):
        (self.release / 'tui-runtime/site-packages/textual').rmdir()
        with self.assertRaises(MaintenanceError):
            self.service.status()

    def test_predecessor_recovery_state_is_reported(self):
        self.service.state.mkdir(parents=True)
        (self.service.state / 'distribution-transaction.json').write_text('{}\n')
        status = self.service.status()
        self.assertEqual(status['ownership'], 'recognized_existing')
        self.assertTrue(status['recovery_required'])

    def test_invalid_metadata_is_rejected(self):
        (self.release / 'install.json').write_text('{"schema_version":3}\n')
        with self.assertRaises(MaintenanceError):
            self.service.status()

    def test_foreign_entrypoint_is_rejected(self):
        path = self.prefix / 'bin/workspace-mcp-manager'
        path.unlink()
        path.symlink_to('/bin/true')
        with self.assertRaises(MaintenanceError):
            self.service.status()

    def test_unexpected_upgrade_entrypoint_is_rejected(self):
        (self.prefix / 'bin/workspace-mcp-manager-upgrade').symlink_to('../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager-upgrade')
        with self.assertRaises(MaintenanceError):
            self.service.status()


class SetupMenuTests(unittest.TestCase):
    def test_help_explains_interactive_and_automation(self):
        completed = subprocess.run(['bash', str(ROOT / 'bootstrap.sh'), '--help'], text=True, capture_output=True)
        self.assertIn('--non-interactive', completed.stdout)
        self.assertIn('--interactive', completed.stdout)

    def test_menu_exit_is_offline_and_does_not_create_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(['bash', str(ROOT / 'bootstrap.sh'), '--interactive', '--account-home', directory], input='0\n', text=True, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('Uninstall', completed.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_inspection_errors_are_visible(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(ROOT / 'bootstrap.sh', root / 'setup.sh')
            (root / 'install.sh').write_text('#!/bin/bash\necho "fixture inspection failure" >&2\nexit 2\n')
            result = subprocess.run(['bash', str(root / 'setup.sh'), '--interactive', '--account-home', str(root / 'home')], input='0\n', text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('fixture inspection failure', result.stdout)
            self.assertNotIn('unavailable or blocked', result.stdout)

    def test_legacy_toolkit_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            toolkit = home / '.local/lib/workspace-mcp'
            toolkit.mkdir(parents=True)
            (toolkit / 'VERSION').write_text('0.3.2\n')
            result = subprocess.run(['bash', str(ROOT / 'bootstrap.sh'), '--interactive', '--account-home', directory], input='0\n', text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Manager installation: absent', result.stdout)
            self.assertIn('Legacy workspace-mcp toolkit: detected', result.stdout)
            self.assertEqual((toolkit / 'VERSION').read_text(), '0.3.2\n')

    def test_status_uses_system_python_without_package_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['bash', str(ROOT / 'scripts/install.sh'), '--maintenance-status', '--account-home', directory], text=True, capture_output=True, timeout=10, env={**os.environ, 'PATH': '/usr/bin:/bin'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['ownership'], 'absent')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_eof_cancels_without_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['bash', str(ROOT / 'bootstrap.sh'), '--interactive', '--account-home', directory], input='', text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

class BootstrapReviewTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        repo = root / 'remote'
        (repo / 'scripts').mkdir(parents=True)
        (repo / 'scripts/install.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
 if [[ "$arg" == --check ]]; then
  printf '%s\\n' '{"ownership":"absent","install_required":true,"recovery_required":false,"candidate_distribution_id":"dist-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
  exit 0
 fi
done
touch "$REVIEW_MARKER"
printf '%s\\n' '{"ok":true,"action":"install"}'
''')
        (repo / 'install-versions.lock').write_text((ROOT / 'install-versions.lock').read_text())
        for cmd in [['git', 'init', '-q', '-b', 'main'], ['git', 'add', '.'], ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture']]:
            subprocess.run(cmd, cwd=repo, check=True)
        home = root / 'home'
        home.mkdir()
        marker = root / 'applied'
        args = ['bash', str(ROOT / 'bootstrap.sh'), '--non-interactive', '--review', '--qualification', '--repository', repo.as_uri(), '--ref', 'refs/heads/main', '--account-home', str(home)]
        return args, home, marker

    def test_install_preview_cancels_by_default_and_uses_frozen_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            args, home, marker = self.fixture(directory)
            env = {**os.environ, 'REVIEW_MARKER': str(marker)}
            result = subprocess.run(args, input='\n', capture_output=True, text=True, env=env, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Apply this installation/update?', result.stdout)
            self.assertFalse(marker.exists())
            result = subprocess.run(args, input='y\n', capture_output=True, text=True, env=env, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.exists())

    def test_changed_declarations_after_preview_block_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            args, home, marker = self.fixture(directory)
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, 'REVIEW_MARKER': str(marker)})
            self.addCleanup(lambda: process.poll() is None and process.kill())
            output = ''
            while 'Apply this installation/update?' not in output:
                char = process.stdout.read(1)
                if not char:
                    self.fail('preview prompt was not produced')
                output += char
            registry = home / '.config/workspace-mcp-manager/instances'
            registry.mkdir(parents=True)
            (registry / 'changed.json').write_text('{}')
            remaining, errors = process.communicate('y\n', timeout=20)
            self.assertNotEqual(process.returncode, 0, errors)
            self.assertFalse(marker.exists())
            self.assertIn('changed', errors.lower())

class MenuDispatchTests(unittest.TestCase):
    def test_uninstall_requires_exact_confirmation_and_cancel_does_not_apply(self):
        import shutil
        for confirmation, applies in [('', False), ('yes', False), ('UNINSTALL', True)]:
            with self.subTest(confirmation=confirmation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy2(ROOT / 'bootstrap.sh', root / 'setup.sh')
                marker = root / 'applied'
                installer = root / 'install.sh'
                installer.write_text('''#!/usr/bin/env bash
set -euo pipefail
case "$1" in
 --maintenance-status) printf '%s\\n' '{"ok":true,"ownership":"standalone_distribution","current":"dist-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","instances":{},"releases":[]}' ;;
 --maintenance-preview) printf '%s\\n' '{"ok":true,"action":"uninstall","status":{"instances":{}},"fingerprint":"fixture"}' ;;
 --maintenance-apply) cat >/dev/null; touch "$MENU_MARKER"; printf '%s\\n' '{"ok":true,"action":"uninstall"}' ;;
 *) exit 2 ;;
esac
''')
                result = subprocess.run(['bash', str(root / 'setup.sh'), '--interactive', '--account-home', str(root / 'home')], input='5\n' + confirmation + '\n0\n', text=True, capture_output=True, timeout=10, env={**os.environ, 'MENU_MARKER': str(marker)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(marker.exists(), applies)
                self.assertIn('Type UNINSTALL', result.stdout)

    def test_scripted_default_requires_explicit_automation_mode(self):
        result = subprocess.run(['bash', str(ROOT / 'bootstrap.sh')], input='', text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--non-interactive', result.stderr)


class SetupPackagingTests(unittest.TestCase):
    def test_packaged_setup_wrapper_preserves_custom_target_and_runs_offline(self):
        import shlex
        import shutil
        import sys
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / 'custom home'
            prefix = home / 'custom prefix'
            final = prefix / 'lib/workspace-mcp-manager/releases' / ('dist-' + 'a' * 40)
            stage = base / 'stage'
            (stage / 'bin').mkdir(parents=True)
            (final / 'distribution').mkdir(parents=True)
            shutil.copy2(ROOT / 'bootstrap.sh', final / 'distribution/setup.sh')
            # The wrapper and menu need only status to exit, never acquisition.
            (final / 'distribution/install.sh').write_text("#!/bin/bash\nprintf '%s\\n' '{\"ownership\":\"absent\",\"instances\":{}}'\n")
            code = (ROOT / 'scripts/install.sh').read_text().split('trap on_exit EXIT')[0]
            harness = code + '\nBOOTSTRAP_PYTHON=' + shlex.quote(sys.executable) + '\nACCOUNT_HOME=' + shlex.quote(str(home)) + '\nPREFIX=' + shlex.quote(str(prefix)) + '\nwrite_setup_wrapper ' + shlex.quote(str(stage)) + ' ' + shlex.quote(str(final))
            result = subprocess.run(['bash', '-c', harness], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(['bash', str(stage / 'bin/workspace-mcp-manager-setup'), '--interactive'], input='0\n', text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(prefix), result.stdout)

class MaintenanceAdapterTests(unittest.TestCase):
    def test_loaded_and_on_disk_service_references_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            service = Maintenance(home, home / '.local')
            units = home / '.config/systemd/user'
            units.mkdir(parents=True)
            (units / 'orphan.service').write_text('ExecStart=' + str(service.root / 'current/bin/worker'))
            replies = [subprocess.CompletedProcess([], 0, 'loaded.service loaded active running test\n', ''),
                       subprocess.CompletedProcess([], 0, 'Id=loaded.service\nExecStart={ path=' + str(service.root / 'current/bin/worker') + '; }\n', '')]
            with mock.patch('workspace_mcp_manager.maintenance.subprocess.run', side_effect=replies):
                self.assertCountEqual(service.remaining_units(), ['loaded.service', str(units / 'orphan.service')])

    def test_lifecycle_adapter_uses_selected_account_and_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            service = Maintenance(home, home / 'custom prefix')
            with mock.patch('workspace_mcp_manager.lifecycle.HostLifecycleWorker') as worker:
                worker.return_value.run.return_value = {'ok': True}
                service.remove_instance('one')
            paths, registry = worker.call_args.args
            self.assertEqual(paths.account_home, home)
            self.assertEqual(paths.manager_executable, home / 'custom prefix/bin/workspace-mcp-manager')
            self.assertEqual(registry.directory, service.registry)
            worker.return_value.run.assert_called_once_with('remove', 'one')

    def test_removal_preview_uses_absent_intent_without_writing_record(self):
        from types import SimpleNamespace
        from workspace_mcp_manager.domain import DesiredInstance
        from workspace_mcp_manager.registry import InstanceRegistry
        from workspace_mcp_manager.planning import PlanOperation
        from tests.helpers import sample_v2_instance
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            service = Maintenance(home, home / '.local')
            raw = sample_v2_instance()
            raw['instance_id'] = 'one'
            desired = DesiredInstance.from_dict(raw)
            record = InstanceRegistry(service.registry).create(desired)
            before = record.read_bytes()
            plan = SimpleNamespace(valid=True, operations=[SimpleNamespace(operation=PlanOperation.STOP, target='one.service')])
            with mock.patch('workspace_mcp_manager.planning.ReconciliationPlanner.plan', return_value=plan) as planner:
                result = service.removal_preview()
            self.assertEqual(planner.call_args.args[0].lifecycle.to_dict(), {'deployment': 'absent', 'runtime': 'stopped'})
            self.assertEqual(record.read_bytes(), before)
            self.assertEqual(result[0]['operations'][0]['operation'], 'STOP')

class ImportMenuTests(unittest.TestCase):
    def test_import_menu_separates_preview_from_explicit_handover(self):
        import shutil
        import sys
        for confirmation, expected in [('', False), ('yes', False), ('MIGRATE', True)]:
            with self.subTest(confirmation=confirmation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy2(ROOT / 'bootstrap.sh', root / 'setup.sh')
                (root / 'install.sh').write_text("#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"ownership\":\"standalone_distribution\",\"instances\":{},\"releases\":[]}'\n")
                home = root / 'home'; manager = home / '.local/bin/workspace-mcp-manager'
                manager.parent.mkdir(parents=True)
                marker = root / 'migrated'
                manager.write_text('#!' + sys.executable + '\n' + '''import json,sys,pathlib
args=sys.argv[1:]
if '--help' in args: raise SystemExit(0)
if args[:3]==['instance','import','list']:
 print(json.dumps({'ok':True,'account_home':str(pathlib.Path(sys.argv[0]).parents[2]),'manager_prefix':str(pathlib.Path(sys.argv[0]).parents[1]),'candidates':[{'instance_id':'one','state':'ready'}]}))
elif args[:3]==['instance','import','preview']:
 print(json.dumps({'ok':True,'fingerprint':'fixture','instances':[{'instance_id':'one','action':'import','desired':{'workspace_path':'/workspace','lifecycle':{'runtime':'running'},'mcp':{'binary':'/bundled/mcp','port':9000,'permission_mode':'dangerous'},'tunnel':{'binary':'/bundled/tunnel','id':'tunnel_ABC','health_port':9001}}}], 'interruption':'Connections will restart.', 'retirement':{'state':'ready'}}))
elif args[:3]==['instance','import','apply']:
 pathlib.Path(''' + repr(str(marker)) + ''').touch()
 print(json.dumps({'ok':True,'completed':['one']}))
else: raise SystemExit(2)
''')
                manager.chmod(0o755)
                result = subprocess.run(['bash', str(root / 'setup.sh'), '--interactive', '--account-home', str(home)], input='7\nall\n'+confirmation+'\n0\n', text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(marker.exists(), expected)
                self.assertIn('Import and replace legacy toolkit', result.stdout)
                self.assertIn('Type MIGRATE', result.stdout)


class ImportAccountBoundaryTests(unittest.TestCase):
    def test_menu_refuses_a_manager_for_a_different_account(self):
        import shutil
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(ROOT / 'bootstrap.sh', root / 'setup.sh')
            (root / 'install.sh').write_text("#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"ownership\":\"standalone_distribution\",\"instances\":{},\"releases\":[]}'\n")
            home = root / 'home'
            manager = home / '.local/bin/workspace-mcp-manager'
            manager.parent.mkdir(parents=True)
            marker = root / 'preview-called'
            manager.write_text('#!' + sys.executable + '\n' + "import sys,json,pathlib\nif '--help' in sys.argv: raise SystemExit(0)\nif 'list' in sys.argv: print(json.dumps({'ok':True,'account_home':'/different-account','manager_prefix':'/different-prefix','candidates':[{'instance_id':'one','state':'ready'}]})); raise SystemExit(0)\npathlib.Path(" + repr(str(marker)) + ").touch()\nraise SystemExit(1)\n")
            manager.chmod(0o755)
            result = subprocess.run(['bash',str(root / 'setup.sh'),'--interactive','--account-home',str(home)], input='7\none\n\n0\n', text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertIn('account or prefix differs', result.stdout)
