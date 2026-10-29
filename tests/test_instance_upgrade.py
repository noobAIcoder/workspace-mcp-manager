from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from io import StringIO

from tests.helpers import sample_instance, sample_v2_instance
from tests.test_setup_maintenance import release
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.distribution_runtime import RuntimeDefaults
from workspace_mcp_manager.errors import ManagerError
from workspace_mcp_manager.instance_upgrade import InstanceUpgradeService
from workspace_mcp_manager.maintenance import atomic_json, inventory, Maintenance, MaintenanceError
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry, assert_maintenance_allows
from workspace_mcp_manager.upgrade_state import read_upgrade
from workspace_mcp_manager.cli import main, build_parser
from workspace_mcp_manager.host_apply import HostExecutionBridge


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class InstanceUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / 'home'
        self.prefix = self.home / 'custom prefix'
        self.root = self.prefix / 'lib/workspace-mcp-manager'
        self.target = release(self.root, 'a')
        for relative in ('runtimes/coding-tools-mcp/bin/coding-tools-mcp', 'runtimes/tunnel-client/tunnel-client', 'runtimes/python/bin/python3'):
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        manifest = json.loads((self.target / 'install.json').read_text())
        manifest['content_manifest'] = [{'path': name, **entry} for name, entry in inventory(self.target).items()
                                        if entry['type'] != 'directory' and name != 'install.json']
        (self.target / 'install.json').write_text(json.dumps(manifest))
        (self.root / 'current').symlink_to('releases/' + self.target.name)
        (self.prefix / 'bin').mkdir()
        for name in ('workspace-mcp-manager', 'workspace-mcp-manager-tui', 'workspace-mcp-reboot', 'workspace-mcp-manager-upgrade'):
            (self.prefix / 'bin' / name).symlink_to('../lib/workspace-mcp-manager/current/bin/' + name)
        config = self.home / '.config/workspace-mcp-manager'
        self.paths = ManagerPaths(self.home, config, config / 'instances', self.home / '.local/state/workspace-mcp-manager',
                                  self.home / '.config/systemd/user', config / 'tunnel-profiles', self.prefix / 'bin/workspace-mcp-manager')
        self.registry = InstanceRegistry(self.paths.registry_dir)
        self.originals = {}
        for index, (name, deployment, runtime) in enumerate((('one', 'present', 'running'), ('two', 'present', 'stopped'), ('three', 'absent', 'stopped'))):
            raw = sample_instance()
            raw['instance_id'] = name
            raw['mcp']['port'] += index
            raw['tunnel']['health_port'] += index
            raw['lifecycle'] = {'deployment': deployment, 'runtime': runtime}
            desired = DesiredInstance.from_dict(raw)
            self.registry.create(desired)
            self.originals[name] = desired.to_dict()
        defaults = RuntimeDefaults(self.target, self.target / 'runtimes/coding-tools-mcp/bin/coding-tools-mcp', self.target / 'runtimes/tunnel-client/tunnel-client')
        patcher = mock.patch('workspace_mcp_manager.instance_upgrade.runtime_defaults', return_value=defaults)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.applied = set()
        self.calls = []
        self.fail = None
        self.crash_after_apply = False
        self.projection = mock.Mock()
        self.projection.preview.side_effect = self.project
        self.worker = mock.Mock()
        self.worker.apply.side_effect = self.apply_instance
        self.worker._qualify.return_value = None
        self.service = InstanceUpgradeService(self.paths, self.registry, projection=self.projection, apply_service=self.worker)

    def project(self, desired):
        iid = desired.instance_id.value
        return {'ok': True, 'collision_result': {'clear': True}, 'errors': [],
                'proposed_plan': {'valid': True, 'operations': [{'operation': 'NOOP' if iid in self.applied else 'UPDATE'}]},
                'plan_fingerprint': digest([desired.fingerprint(), iid in self.applied]), 'semantic_operations': []}

    def apply_instance(self, iid, *, force_restart, action, expected_plan_fingerprint=None):
        assert_maintenance_allows(self.paths.state_root, instance_id=iid)
        self.calls.append((iid, force_restart))
        if iid == self.fail:
            raise RuntimeError('fixture apply failure')
        desired = self.registry.get(iid)
        self.applied.add(iid)
        transaction = 'transaction-' + iid + '-' + str(len(self.calls))
        atomic_json(self.paths.state_root / 'transactions' / iid / (transaction + '.json'),
                    {'action': action, 'instance_id': iid, 'desired_fingerprint': desired.fingerprint(), 'status': 'succeeded'})
        atomic_json(self.paths.state_root / 'applied' / (iid + '.json'),
                    {'instance_id': iid, 'desired_fingerprint': desired.fingerprint(), 'transaction_id': transaction})
        if self.crash_after_apply:
            self.crash_after_apply = False
            raise KeyboardInterrupt('simulated hard interruption after apply commit')
        return {'ok': True, 'transaction_id': transaction}

    def run_upgrade(self):
        preview = self.service.preview()
        self.assertTrue(preview['ok'], preview)
        return self.service.apply(preview, preview['fingerprint'])

    def test_preview_is_read_only_and_explicit_about_lifecycle_and_paths(self):
        before = set(self.home.rglob('*'))
        preview = self.service.preview()
        self.assertTrue(preview['ok'])
        self.assertEqual(before, set(self.home.rglob('*')))
        self.assertEqual([row['instance_id'] for row in preview['instances']], ['one', 'three', 'two'])
        for row in preview['instances']:
            original = self.originals[row['instance_id']]
            candidate = deepcopy(original)
            candidate['mcp']['binary'] = str(self.target / 'runtimes/coding-tools-mcp/bin/coding-tools-mcp')
            candidate['tunnel']['binary'] = str(self.target / 'runtimes/tunnel-client/tunnel-client')
            self.assertEqual(row['candidate'], candidate)
        self.assertEqual([row['force_restart'] for row in preview['instances']], [True, False, False])

    def test_apply_preserves_settings_and_stopped_absent_intent(self):
        result = self.run_upgrade()
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.calls, [('one', True), ('two', False)])
        for iid, original in self.originals.items():
            actual = self.registry.get(iid).to_dict()
            actual['mcp']['binary'] = original['mcp']['binary']
            actual['tunnel']['binary'] = original['tunnel']['binary']
            self.assertEqual(actual, original)
        backup = Path(result['backup'])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        for iid, original in self.originals.items():
            self.assertEqual(json.loads((backup / (iid + '.json')).read_text()), original)
        self.assertFalse(self.service.journal.exists())

    def test_already_current_still_restarts_running_instances_on_new_run(self):
        self.assertTrue(self.run_upgrade()['ok'])
        self.assertTrue(self.run_upgrade()['ok'])
        self.assertEqual(self.calls, [('one', True), ('two', False)] * 2)

    def test_first_failure_stops_and_resume_does_not_repeat_completed_instances(self):
        self.fail = 'two'
        failed = self.run_upgrade()
        self.assertFalse(failed['ok'])
        self.assertEqual(failed['completed'], ['one', 'three'])
        self.assertEqual(failed['failed'], 'two')
        self.assertTrue(self.service.journal.exists())
        with self.assertRaises(ManagerError):
            self.registry.update(self.registry.get('one'))
        with self.assertRaises(MaintenanceError):
            Maintenance(self.home, self.prefix).preview('uninstall')
        self.fail = None
        result = self.service.recover()
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.calls.count(('one', True)), 1)
        self.assertFalse(self.service.journal.exists())

    def test_failure_on_first_instance_leaves_remaining_configurations_untouched(self):
        self.fail = 'one'
        result = self.run_upgrade()
        self.assertFalse(result['ok'])
        self.assertEqual(result['remaining'], ['one', 'three', 'two'])
        self.assertEqual(self.registry.get('two').to_dict(), self.originals['two'])
        self.assertEqual(self.registry.get('three').to_dict(), self.originals['three'])

    def test_crash_after_apply_commit_does_not_restart_again_on_resume(self):
        self.crash_after_apply = True
        with self.assertRaises(KeyboardInterrupt):
            self.run_upgrade()
        self.assertTrue(self.service.recover()['ok'])
        self.assertEqual(self.calls.count(('one', True)), 1)

    def test_stale_preview_does_not_write_journal_or_change_other_instances(self):
        preview = self.service.preview()
        changed = deepcopy(self.originals['one']); changed['mcp']['port'] += 100
        self.registry.update(DesiredInstance.from_dict(changed))
        with self.assertRaises(ManagerError):
            self.service.apply(preview, preview['fingerprint'])
        self.assertFalse(self.service.journal.exists())
        self.assertEqual(self.calls, [])

    def test_blocked_preview_cannot_apply(self):
        self.projection.preview.return_value = None
        self.projection.preview.side_effect = lambda desired: {'ok': True, 'collision_result': {'clear': False}, 'proposed_plan': {'valid': False}, 'errors': ['foreign resource'], 'plan_fingerprint': 'a' * 64}
        preview = self.service.preview()
        self.assertFalse(preview['ok'])
        with self.assertRaises(ManagerError):
            self.service.apply(preview, preview['fingerprint'])
        self.assertEqual(self.calls, [])

    def test_completed_instance_drift_blocks_resume(self):
        self.fail = 'two'
        self.run_upgrade()
        path = self.registry.path_for('one')
        raw = json.loads(path.read_text()); raw['mcp']['port'] += 1
        path.write_text(json.dumps(raw))
        calls = list(self.calls)
        result = self.service.recover()
        self.assertFalse(result['ok'])
        self.assertEqual(self.calls, calls)

    def test_tampered_journal_cannot_redirect_target_or_backup(self):
        self.fail = 'one'; self.run_upgrade()
        original = json.loads(self.service.journal.read_text())
        for key in ('target_root', 'backup'):
            changed = deepcopy(original); changed[key] = '/tmp/foreign'
            self.service.journal.write_text(json.dumps(changed))
            with self.assertRaises((ManagerError, ValueError)):
                self.service.recover()
        self.service.journal.write_text(json.dumps(original))
        self.assertEqual(read_upgrade(self.service.journal)['target_release'], self.target.name)

    def test_status_reports_pending_upgrade_and_target(self):
        self.fail = 'one'; self.run_upgrade()
        status = Maintenance(self.home, self.prefix).status()
        self.assertTrue(status['recovery_required'])
        self.assertEqual(status['instance_upgrade']['target_release'], self.target.name)

    def test_credentials_are_not_copied_into_backup(self):
        credential = self.home / 'private.env'; credential.write_text('SECRET=never-copy-this-value')
        raw = self.registry.get('one').to_dict(); raw['tunnel']['env_file'] = str(credential)
        self.registry.update(DesiredInstance.from_dict(raw))
        result = self.run_upgrade()
        contents = ''.join(path.read_text() for path in Path(result['backup']).glob('*.json'))
        self.assertNotIn('never-copy-this-value', contents)
        self.assertEqual(credential.read_text(), 'SECRET=never-copy-this-value')

    def test_public_upgrade_commands_use_host_boundary(self):
        for action in ('preview', 'recover', 'apply'):
            arguments = ['instance', 'upgrade', action]
            request = {}
            if action == 'preview':
                arguments.append('--all')
            elif action == 'apply':
                path = self.home / 'review.json'; path.write_text('{"ok":true}')
                arguments += ['--preview', str(path), '--expected-preview-fingerprint', 'reviewed']
                request = {'preview': {'ok': True}, 'expected_fingerprint': 'reviewed'}
            with self.subTest(action=action), mock.patch('workspace_mcp_manager.cli.ManagerPaths.for_current_user', return_value=self.paths), \
                 mock.patch('workspace_mcp_manager.cli.HostExecutionBridge') as bridge, redirect_stdout(StringIO()):
                bridge.return_value.run_instance_upgrade.return_value = {'ok': True}
                self.assertEqual(main(arguments), 0)
                bridge.return_value.run_instance_upgrade.assert_called_once_with(action, request)

    def test_host_bridge_uses_stdin_and_streams_progress_separately(self):
        import subprocess
        bridge = HostExecutionBridge(self.paths)
        with mock.patch('workspace_mcp_manager.host_apply._run', return_value=subprocess.CompletedProcess([], 0, '{"ok":false,"remaining":["one"]}', None)) as run:
            result = bridge.run_instance_upgrade('recover', {})
        self.assertFalse(result['ok'])
        self.assertEqual(run.call_args.args[0][-3:], ['_runtime', 'instance-upgrade', 'recover'])
        self.assertEqual(run.call_args.kwargs['input_text'], '{}')
        self.assertTrue(run.call_args.kwargs['stream_stderr'])
        self.assertEqual(run.call_args.kwargs['timeout'], 3600.0)

    def test_new_instance_creation_and_regular_lifecycle_are_blocked_during_upgrade(self):
        self.fail = 'one'; self.run_upgrade()
        extra = deepcopy(self.originals['one']); extra['instance_id'] = 'new'
        with self.assertRaises(ManagerError):
            self.registry.create(DesiredInstance.from_dict(extra))
        with self.assertRaises(ManagerError):
            assert_maintenance_allows(self.paths.state_root, instance_id='one')

    def test_corrupt_backup_blocks_resume_without_applying_more_work(self):
        self.fail = 'one'; result = self.run_upgrade()
        (Path(result['backup']) / 'one.json').write_text('{}')
        calls = list(self.calls)
        self.assertFalse(self.service.recover()['ok'])
        self.assertEqual(self.calls, calls)

    def test_symlinked_journal_is_never_replaced(self):
        other = self.home / 'external.json'; other.write_text('{}')
        self.service.journal.parent.mkdir(parents=True, exist_ok=True)
        self.service.journal.symlink_to(other)
        with self.assertRaises(ManagerError):
            self.service.recover()
        self.assertTrue(self.service.journal.is_symlink())
        self.assertEqual(other.read_text(), '{}')

    def test_unfinished_apply_is_reconciled_from_actual_state_on_resume(self):
        real_write = atomic_json
        def interrupt(path, payload):
            real_write(path, payload)
            if Path(path) == self.service.journal and payload['entries'][0]['phase'] == 'applying':
                raise KeyboardInterrupt('crash after declaration commit, before apply')
        with mock.patch('workspace_mcp_manager.instance_upgrade.atomic_json', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_upgrade()
        self.assertEqual(self.calls, [])
        self.assertTrue(self.service.recover()['ok'])
        self.assertEqual(self.calls.count(('one', True)), 1)

    def test_completed_journal_cleanup_is_idempotent(self):
        real_write = atomic_json
        def interrupt(path, payload):
            real_write(path, payload)
            if Path(path) == self.service.journal and payload['status'] == 'completed':
                raise KeyboardInterrupt('crash before journal cleanup')
        with mock.patch('workspace_mcp_manager.instance_upgrade.atomic_json', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_upgrade()
        calls = list(self.calls)
        self.assertTrue(self.service.recover()['ok'])
        self.assertEqual(self.calls, calls)

    def test_changed_runtime_files_block_recovery(self):
        self.fail = 'one'; self.run_upgrade()
        (self.target / 'runtimes/coding-tools-mcp/bin/coding-tools-mcp').write_text('tampered')
        with self.assertRaises(ManagerError):
            self.service.recover()

    def test_interruptions_at_journal_boundaries_resume_without_duplicate_restarts(self):
        for boundary in ('prepared', 'updating', 'checkpoint'):
            with self.subTest(boundary=boundary):
                fixture = InstanceUpgradeTests()
                fixture.setUp()
                try:
                    def interrupt(path, payload):
                        atomic_json(path, payload)
                        if Path(path) != fixture.service.journal:
                            return
                        first = payload['entries'][0]['phase']
                        if (boundary == 'prepared' and first == 'pending'
                                or boundary == 'updating' and first == 'updating'
                                or boundary == 'checkpoint' and first == 'completed'):
                            raise KeyboardInterrupt('simulated journal-boundary interruption')
                    with mock.patch('workspace_mcp_manager.instance_upgrade.atomic_json', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                        fixture.run_upgrade()
                    self.assertTrue(fixture.service.recover()['ok'])
                    self.assertEqual(fixture.calls.count(('one', True)), 1)
                finally:
                    fixture.doCleanups()

    def test_interrupted_backup_does_not_change_instances(self):
        def interrupt(path, payload):
            atomic_json(path, payload)
            if Path(path).name == 'one.json':
                raise KeyboardInterrupt('simulated interrupted backup')
        with mock.patch('workspace_mcp_manager.instance_upgrade.atomic_json', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_upgrade()
        self.assertFalse(self.service.journal.exists())
        self.assertEqual(self.calls, [])
        self.assertEqual({d.instance_id.value: d.to_dict() for d in self.registry.list()}, self.originals)
        self.assertTrue(self.service.preview()['ok'])

    def test_offline_recovery_capsule_inspects_pending_upgrade_without_package_imports(self):
        import subprocess
        self.fail = 'one'; self.run_upgrade()
        maintenance = Maintenance(self.home, self.prefix)
        maintenance.prepare_recovery_capsule()
        capsule = self.paths.state_root / 'setup-recovery'
        self.assertTrue((capsule / 'upgrade_state.py').is_file())
        result = subprocess.run(['bash', str(capsule / 'install.sh'), '--maintenance-status', '--account-home', str(self.home), '--prefix', str(self.prefix)],
                                text=True, capture_output=True, timeout=10, env={**os.environ, 'PATH': '/usr/bin:/bin'})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(json.loads(result.stdout)['recovery_required'])

    def test_distribution_installer_refuses_pending_instance_upgrade(self):
        import subprocess
        self.fail = 'one'; self.run_upgrade()
        script = Path(__file__).resolve().parents[1] / 'scripts/install.sh'
        result = subprocess.run(['bash', str(script), '--recover-only', '--account-home', str(self.home), '--prefix', str(self.prefix)],
                                text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('resume the pending instance upgrade', result.stderr)

    def test_symlinked_distribution_lock_cannot_start_upgrade(self):
        outside = self.home / 'outside'; outside.write_text('preserve')
        self.paths.state_root.mkdir(parents=True, exist_ok=True)
        (self.paths.state_root / 'distribution.lock').symlink_to(outside)
        preview = self.service.preview()
        with self.assertRaises(ManagerError):
            self.service.apply(preview, preview['fingerprint'])
        self.assertEqual(outside.read_text(), 'preserve')

    def test_v2_git_access_agent_and_github_settings_are_preserved(self):
        raw = sample_v2_instance(); raw['instance_id'] = 'one'
        self.registry.update(DesiredInstance.from_dict(raw))
        original = self.registry.get('one').to_dict()
        self.assertTrue(self.run_upgrade()['ok'])
        after = self.registry.get('one').to_dict()
        after['mcp']['binary'] = original['mcp']['binary']
        after['tunnel']['binary'] = original['tunnel']['binary']
        self.assertEqual(after, original)

    def test_backup_write_failure_is_structured_and_does_not_change_instances(self):
        preview = self.service.preview()
        with mock.patch('workspace_mcp_manager.instance_upgrade.atomic_json', side_effect=PermissionError('backup is not writable')):
            with self.assertRaises(ManagerError):
                self.service.apply(preview, preview['fingerprint'])
        self.assertFalse(self.service.journal.exists())
        self.assertEqual(self.calls, [])

    def test_resume_write_failure_retains_journal_and_returns_structured_error(self):
        self.fail = 'one'; self.run_upgrade()
        with mock.patch('workspace_mcp_manager.instance_upgrade.atomic_json', side_effect=PermissionError('state is not writable')):
            with self.assertRaises(ManagerError):
                self.service.recover()
        self.assertTrue(self.service.journal.exists())


if __name__ == '__main__':
    unittest.main()
