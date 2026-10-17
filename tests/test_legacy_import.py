from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from workspace_mcp_manager.legacy_import import LegacyImportService, ImportBlocked
from workspace_mcp_manager.legacy_templates import LegacyTemplates
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry


class FakeHost:
    def __init__(self):
        self.states = {}
        self.actions = []

    def states_for(self, units):
        return {u: self.states.get(u, {'enabled': True, 'active': True}) for u in units}

    def action(self, action, unit=None):
        self.actions.append((action, unit))

    def references(self, toolkit):
        return []


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.config = self.home / '.config/workspace-mcp-manager'
        self.paths = ManagerPaths(self.home, self.config, self.config / 'instances', self.home / '.local/state/workspace-mcp-manager', self.home / '.config/systemd/user', self.config / 'tunnel-profiles', self.home / '.local/bin/workspace-mcp-manager')
        self.registry = InstanceRegistry(self.paths.registry_dir)
        self.host = FakeHost()
        self.release = self.home / '.local/lib/workspace-mcp-manager/releases' / ('dist-' + 'a'*40)
        for name in ('runtimes/coding-tools-mcp/bin/coding-tools-mcp', 'runtimes/tunnel-client/tunnel-client'):
            p = self.release / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('#!/bin/sh\nexit 0\n'); p.chmod(0o755)
        self.service = LegacyImportService(self.paths, self.registry, host=self.host, release=self.release)
        self.validation = patch.object(self.service, 'validate_candidate')
        self.validation.start(); self.addCleanup(self.validation.stop)
        self.program_validation = patch.object(self.service, 'validate_legacy_program')
        self.program_validation.start(); self.addCleanup(self.program_validation.stop)

    def fixture(self, iid='one'):
        config = self.home / '.config/workspace-mcp' / (iid + '.conf')
        config.parent.mkdir(parents=True, exist_ok=True)
        workspace = self.home / iid; workspace.mkdir()
        env = self.home / 'credentials.env'
        env.write_text('CONTROL_PLANE_API_KEY=secret-fixture-never-display\n'); env.chmod(0o600)
        values = dict(CONFIG_VERSION='1', INSTANCE_ID=iid, WORKSPACE_PATH=str(workspace), MCP_BINARY=str(self.home / 'old-mcp'), MCP_HOST='127.0.0.1', MCP_PORT=str(19000 + len(list(config.parent.glob('*.conf')))*2), MCP_PERMISSION_MODE='dangerous', MCP_SHELL_ENV_INHERIT='all', MCP_EXEC_PATH='/usr/local/bin:/usr/bin:/bin', TUNNEL_BINARY=str(self.home / 'old-tunnel'), TUNNEL_PROFILE='workspace-mcp-' + iid, TUNNEL_ID='tunnel_ABC'+iid, TUNNEL_ENV_FILE=str(env), TUNNEL_HEALTH_HOST='127.0.0.1', TUNNEL_HEALTH_PORT=str(19001 + len(list(config.parent.glob('*.conf')))*2))
        config.write_text(''.join(k+'='+v+'\n' for k,v in values.items())); config.chmod(0o600)
        templates = LegacyTemplates(self.paths, values)
        self.paths.user_unit_dir.mkdir(parents=True, exist_ok=True)
        for path, body in templates.files().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        return config, values

    def test_preview_preserves_settings_upgrades_runtime_and_does_not_mutate(self):
        config, values = self.fixture()
        before = config.read_bytes()
        preview = self.service.preview(['one'])
        desired = preview['instances'][0]['desired']
        self.assertTrue(preview['ok'], preview)
        self.assertEqual(desired['mcp']['permission_mode'], 'dangerous')
        self.assertEqual(desired['tunnel']['id'], values['TUNNEL_ID'])
        self.assertTrue(desired['mcp']['binary'].startswith(str(self.release)))
        self.assertNotIn('secret-fixture', json.dumps(preview))
        self.assertEqual(before, config.read_bytes())
        self.assertFalse(self.paths.registry_dir.exists())
        self.assertEqual(self.host.actions, [])

    def test_unknown_config_and_custom_unit_block(self):
        config, _ = self.fixture()
        config.write_text(config.read_text()+'UNKNOWN_FLAG=true\n')
        self.assertFalse(self.service.preview(['one'])['ok'])
        config.write_text(config.read_text().replace('UNKNOWN_FLAG=true\n',''))
        unit = self.paths.user_unit_dir / 'coding-tools-mcp-one.service'
        unit.write_text(unit.read_text()+'ExecStartPost=/bin/true\n')
        self.assertFalse(self.service.preview(['one'])['ok'])
        self.assertEqual(self.host.actions, [])

    def test_stale_preview_cannot_stop_services(self):
        config, _ = self.fixture()
        preview = self.service.preview(['one'])
        config.write_text(config.read_text().replace('dangerous','safe'))
        with self.assertRaises(ImportBlocked):
            self.service.apply(preview, preview['fingerprint'])
        self.assertEqual(self.host.actions, [])

    def test_failure_restores_original_resources(self):
        config, _ = self.fixture()
        original = config.read_bytes()
        unit = self.paths.user_unit_dir / 'coding-tools-mcp-one.service'
        old_unit = unit.read_bytes()
        preview = self.service.preview(['one'])
        with patch.object(self.service, 'apply_candidate', side_effect=RuntimeError('fixture failure')):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertFalse(result['ok'], result)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(unit.read_bytes(), old_unit)
        self.assertFalse(self.registry.path_for('one').exists())
        self.assertEqual(result['restored'], ['one'])
        self.assertFalse(self.service.journal_path('one').exists())

    def test_batch_stops_after_failure(self):
        self.fixture('one'); self.fixture('two')
        preview = self.service.preview(['one','two'])
        with patch.object(self.service, 'apply_candidate', side_effect=RuntimeError('fixture failure')):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertEqual(result['unattempted'], ['two'])
        self.assertNotIn(('stop', 'tunnel-client-two.service'), self.host.actions)

    def test_committed_recovery_converges_forward_and_retry_is_noop(self):
        self.fixture()
        preview = self.service.preview(['one'])
        with patch.object(self.service, 'apply_candidate', return_value={'ok': True}):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['completed'], ['one'])
        self.assertFalse(self.service.journal_path('one').exists())
        again = self.service.preview(['one'])
        self.assertTrue(again['ok'], again)
        self.assertEqual(again['instances'][0]['action'], 'already-imported')

    def materialize(self, desired, row):
        from workspace_mcp_manager.generation import ResourceGenerator
        for resource in ResourceGenerator(self.paths).generate(desired).resources:
            path = Path(resource.path)
            if resource.content is None:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(resource.content)
            path.chmod(resource.mode)
        return {'ok': True}

    def test_successful_batch_keeps_other_instances_then_retires_toolkit(self):
        self.fixture('one'); self.fixture('two')
        toolkit = self.home / '.local/lib/workspace-mcp'
        toolkit.mkdir(parents=True)
        from workspace_mcp_manager.legacy_import import TOOLKIT_FILES
        for name in TOOLKIT_FILES:
            (toolkit / name).write_text('0.3.2\n' if name == 'VERSION' else 'fixture\n')
        link = self.home / '.local/bin/workspace-mcp-setup'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to('../lib/workspace-mcp/workspace-mcp-setup')
        first = self.service.preview(['one'])
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize):
            result = self.service.apply(first, first['fingerprint'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['retirement']['state'], 'retained')
        self.assertTrue(toolkit.exists())
        self.assertTrue((self.home / '.config/workspace-mcp/two.conf').exists())
        second = self.service.preview(['two'])
        self.assertTrue(second['ok'], second)
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize):
            result = self.service.apply(second, second['fingerprint'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['retirement']['state'], 'retired')
        self.assertFalse(toolkit.exists())
        self.assertFalse(link.is_symlink())
        self.assertTrue((self.service.root / 'retired-toolkit/VERSION').is_file())
        self.assertEqual(self.service.list()['candidates'], [])
        self.assertIn('secret-fixture', (self.home / 'credentials.env').read_text())

    def test_interrupted_apply_recovers_original_and_blocks_selected_registry(self):
        config, _ = self.fixture()
        original = config.read_bytes()
        preview = self.service.preview(['one'])
        def interrupt(desired, row):
            self.materialize(desired, row)
            raise KeyboardInterrupt()
        with patch.object(self.service, 'apply_candidate', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(preview, preview['fingerprint'])
        self.assertTrue(self.service.journal_path('one').exists())
        from workspace_mcp_manager.errors import ManagerError
        with self.assertRaises(ManagerError):
            self.registry.update(self.registry.get('one'))
        result = self.service.recover()
        self.assertTrue(result['ok'])
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse(self.registry.path_for('one').exists())
        self.assertFalse(self.service.journal_path('one').exists())
        self.assertIn(('start', 'coding-tools-mcp-one.service'), self.host.actions)
        self.assertIn(('start', 'tunnel-client-one.service'), self.host.actions)

    def test_commit_marker_recovers_forward(self):
        self.fixture()
        preview = self.service.preview(['one'])
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize), patch.object(self.service, '_clear', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(preview, preview['fingerprint'])
        self.assertTrue(self.service.receipt_path('one').exists())
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize):
            result = self.service.recover()
        self.assertTrue(result['ok'])
        self.assertTrue(self.registry.path_for('one').is_file())
        self.assertFalse(self.service.journal_path('one').exists())

    def test_changed_created_resource_retains_recovery_journal(self):
        self.fixture()
        preview = self.service.preview(['one'])
        def interrupt(desired, row):
            self.materialize(desired, row)
            raise KeyboardInterrupt()
        with patch.object(self.service, 'apply_candidate', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(preview, preview['fingerprint'])
        unit = self.paths.user_unit_dir / 'coding-tools-mcp-one.service'
        unit.write_text(unit.read_text() + '# unexpected change\n')
        with self.assertRaises(ImportBlocked):
            self.service.recover()
        self.assertTrue(self.service.journal_path('one').exists())
        self.assertIn('# unexpected change', unit.read_text())

    def test_mixed_service_intent_requires_reviewed_declaration(self):
        self.fixture()
        self.host.states['tunnel-client-one.service'] = {'enabled': False, 'active': False}
        preview = self.service.preview(['one'])
        self.assertFalse(preview['ok'])
        draft = preview['instances'][0]['desired']
        resolved = self.service.preview(['one'], resolutions={'one': draft})
        self.assertTrue(resolved['ok'], resolved)
        changed = deepcopy(draft)
        changed['mcp']['permission_mode'] = 'safe'
        rejected = self.service.preview(['one'], resolutions={'one': changed})
        self.assertFalse(rejected['ok'])

    def test_symlinked_configuration_and_unknown_dropin_block(self):
        config, _ = self.fixture()
        original = config.with_suffix('.original')
        config.rename(original); config.symlink_to(original)
        self.assertFalse(self.service.preview(['one'])['ok'])
        config.unlink(); original.rename(config)
        dropins = self.paths.user_unit_dir / 'coding-tools-mcp-one.service.d'
        dropins.mkdir(); (dropins / 'custom.conf').write_text('[Service]\nEnvironment="EXTRA=value"\n')
        self.assertFalse(self.service.preview(['one'])['ok'])
        self.assertEqual(self.host.actions, [])

    def test_known_github_and_exec_roots_are_preserved(self):
        self.fixture()
        dropins = self.paths.user_unit_dir / 'coding-tools-mcp-one.service.d'
        dropins.mkdir()
        gh_dir = self.home / '.config/workspace-mcp/one-gh'
        roots = str(gh_dir) + ':' + str(self.home / '.nvm/versions/node/v22')
        (dropins / 'github-access.conf').write_text('[Service]\nEnvironment="GH_CONFIG_DIR=' + str(gh_dir) + '"\nEnvironment="CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=' + str(gh_dir) + '"\n')
        (dropins / 'zz-exec-roots.conf').write_text('[Service]\nEnvironment="CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=' + roots + '"\n')
        preview = self.service.preview(['one'])
        self.assertTrue(preview['ok'], preview)
        desired = preview['instances'][0]['desired']
        self.assertEqual(desired['github']['mode'], 'external')
        self.assertEqual(desired['github']['config_dir'], str(gh_dir))
        self.assertEqual(desired['mcp']['external_roots'], roots.split(':'))

    def test_stock_templates_do_not_indent_systemd_sections(self):
        _, values = self.fixture()
        templates = LegacyTemplates(self.paths, values)
        self.assertIn('\n[Unit]\n', templates.render_mcp_unit())
        self.assertIn('\n[Service]\n', templates.render_tunnel_unit())

    def test_tampered_preview_document_is_rejected(self):
        self.fixture()
        preview = self.service.preview(['one'])
        preview['instances'][0]['desired']['mcp']['permission_mode'] = 'safe'
        with self.assertRaises(ImportBlocked):
            self.service.apply(preview, preview['fingerprint'])
        self.assertEqual(self.host.actions, [])

    def test_disabled_boot_enablement_survives_later_generation(self):
        self.fixture()
        for name in ('coding-tools-mcp-one.service', 'tunnel-client-one.service'):
            self.host.states[name] = {'enabled': False, 'active': False}
        self.host.states['workspace-mcp-admission-guard-one.timer'] = {'enabled': False, 'active': False}
        preview = self.service.preview(['one'])
        self.assertTrue(preview['ok'], preview)
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertTrue(result['ok'], result)
        from workspace_mcp_manager.generation import ResourceGenerator
        bundle = ResourceGenerator(self.paths).generate(self.registry.get('one'))
        units = {r.unit_name: r for r in bundle.resources if r.unit_name}
        self.assertFalse(units['coding-tools-mcp-one.service'].enable_when_present)
        self.assertFalse(units['tunnel-client-one.service'].enable_when_present)

    def test_known_access_mountpoints_are_archived_and_restored(self):
        self.fixture()
        source = self.home / 'shared'; source.mkdir()
        access = self.home / 'one/.workspace-mcp-access'
        (access / 'shared').mkdir(parents=True)
        dropins = self.paths.user_unit_dir / 'coding-tools-mcp-one.service.d'
        dropins.mkdir()
        (dropins / 'workspace-mcp-manager-access.conf').write_text('[Service]\nPrivateUsers=true\nBindReadOnlyPaths='+str(source)+':'+str(access / 'shared')+'\n')
        preview = self.service.preview(['one'])
        self.assertTrue(preview['ok'], preview)
        self.assertEqual(preview['instances'][0]['desired']['access']['read_only'], [{'alias':'shared','path':str(source)}])
        def fail(desired, row):
            self.materialize(desired, row)
            raise RuntimeError('fixture failure')
        with patch.object(self.service, 'apply_candidate', side_effect=fail):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertFalse(result['ok'])
        self.assertTrue((access / 'shared').is_dir())
        self.assertEqual(list((access / 'shared').iterdir()), [])
        self.assertEqual(list(source.iterdir()), [])

    def test_unknown_toolkit_files_prevent_retirement(self):
        self.fixture()
        toolkit = self.home / '.local/lib/workspace-mcp'
        toolkit.mkdir(parents=True)
        (toolkit / 'unknown').write_text('preserve')
        preview = self.service.preview(['one'])
        self.assertEqual(preview['retirement']['state'], 'blocked')
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['retirement']['state'], 'blocked')
        self.assertEqual((toolkit / 'unknown').read_text(), 'preserve')

    def test_retirement_interruption_recovers_after_archive_rename(self):
        self.fixture()
        from workspace_mcp_manager.legacy_import import TOOLKIT_FILES
        toolkit = self.home / '.local/lib/workspace-mcp'
        toolkit.mkdir(parents=True)
        for name in TOOLKIT_FILES:
            (toolkit / name).write_text('0.3.2\n' if name == 'VERSION' else 'fixture')
        link = self.home / '.local/bin/workspace-mcp-setup'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to('../lib/workspace-mcp/workspace-mcp-setup')
        preview = self.service.preview(['one'])
        original_replace = os.replace
        def interrupt_after_rename(source, destination):
            original_replace(source, destination)
            if Path(source) == toolkit:
                raise KeyboardInterrupt()
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize), patch('workspace_mcp_manager.legacy_import.os.replace', side_effect=interrupt_after_rename):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(preview, preview['fingerprint'])
        self.assertTrue((self.service.root / '_retirement.json').is_file())
        self.assertFalse(toolkit.exists())
        result = self.service.recover()
        self.assertTrue(result['ok'], result)
        self.assertFalse(link.is_symlink())
        self.assertFalse((self.service.root / '_retirement.json').exists())

    def test_modified_predecessor_program_is_not_silently_adopted(self):
        self.fixture()
        self.program_validation.stop()
        toolkit = self.service.toolkit
        toolkit.mkdir(parents=True)
        (toolkit / 'VERSION').write_text('0.3.2\n')
        (toolkit / 'workspace-mcp').write_text('custom program\n')
        preview = self.service.preview(['one'])
        self.assertFalse(preview['ok'])
        self.assertIn('modified legacy', preview['blockers'][0]['reason'])
        self.assertEqual(self.host.actions, [])

    def test_credential_reference_inside_legacy_state_stays_in_place(self):
        config, values = self.fixture()
        state = self.service.audit._state_path('one')
        state.mkdir(parents=True)
        credential = state / 'runtime.env'
        credential.write_text('CONTROL_PLANE_API_KEY=fixture-secret\n')
        config.write_text(config.read_text().replace(values['TUNNEL_ENV_FILE'], str(credential)))
        preview = self.service.preview(['one'])
        self.assertTrue(preview['ok'], preview)
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertTrue(result['ok'], result)
        self.assertTrue(credential.is_file())
        self.assertNotIn('fixture-secret', json.dumps(preview))
        self.assertFalse((self.service.root / 'backups/one/state').exists())

    def test_original_enablement_link_targets_are_restored(self):
        self.fixture()
        wants = self.paths.user_unit_dir / 'default.target.wants'
        wants.mkdir()
        link = wants / 'coding-tools-mcp-one.service'
        link.symlink_to('../coding-tools-mcp-one.service')
        preview = self.service.preview(['one'])
        with patch.object(self.service, 'apply_candidate', side_effect=RuntimeError('fixture failure')):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertFalse(result['ok'])
        self.assertEqual(os.readlink(link), '../coding-tools-mcp-one.service')

    def test_endpoint_conflicts_block_entire_batch_before_downtime(self):
        _, one = self.fixture('one')
        config, two = self.fixture('two')
        config.write_text(config.read_text().replace('MCP_PORT='+two['MCP_PORT'], 'MCP_PORT='+one['MCP_PORT']))
        # Keep the recognized unit template aligned with the new legacy config.
        two['MCP_PORT'] = one['MCP_PORT']
        for path, body in LegacyTemplates(self.paths, two).files().items():
            path.write_text(body)
        preview = self.service.preview(['one','two'])
        self.assertFalse(preview['ok'])
        self.assertIn('conflicts', preview['blockers'][0]['reason'])
        self.assertEqual(self.host.actions, [])

    def test_interruption_after_restoration_does_not_leave_a_retry_blocking_backup(self):
        self.fixture()
        preview = self.service.preview(['one'])
        original_clear = self.service._clear
        def interrupt_after_clear(iid):
            original_clear(iid)
            raise KeyboardInterrupt()
        with patch.object(self.service, 'apply_candidate', side_effect=RuntimeError('fixture failure')), patch.object(self.service, '_clear', side_effect=interrupt_after_clear):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(preview, preview['fingerprint'])
        self.assertFalse((self.service.root / 'backups/one').exists())
        self.assertFalse(self.service.journal_path('one').exists())
        retry = self.service.preview(['one'])
        with patch.object(self.service, 'apply_candidate', side_effect=self.materialize):
            result = self.service.apply(retry, retry['fingerprint'])
        self.assertTrue(result['ok'], result)

    def test_retirement_requires_an_installed_manager_even_with_no_instances(self):
        self.service.release = None
        with self.assertRaises(ImportBlocked):
            self.service.preview([])

    def test_failed_legacy_restart_keeps_journal_and_allows_log_growth_on_retry(self):
        self.fixture()
        state = self.service.audit._state_path('one')
        state.mkdir(parents=True)
        log = state / 'mcp.log'; log.write_text('original log\n')
        preview = self.service.preview(['one'])
        original_action = self.host.action
        def unready(action, unit=None):
            original_action(action, unit)
            if action == 'start' and unit == 'coding-tools-mcp-one.service':
                self.host.states[unit] = {'enabled':True,'active':False}
        with patch.object(self.service, 'apply_candidate', side_effect=RuntimeError('fixture failure')), patch.object(self.host,'action',side_effect=unready):
            result = self.service.apply(preview, preview['fingerprint'])
        self.assertFalse(result['ok'])
        self.assertTrue(result['recovery_required'])
        self.assertEqual(result['restored'], [])
        log.write_text(log.read_text() + 'restart output\n')
        self.host.states.pop('coding-tools-mcp-one.service')
        result = self.service.recover()
        self.assertTrue(result['ok'], result)
        self.assertIn('restart output', log.read_text())
        self.assertFalse(self.service.journal_path('one').exists())

    def test_handover_checks_effective_boot_enablement_before_commit(self):
        self.fixture()
        for unit in ('coding-tools-mcp-one.service','tunnel-client-one.service','workspace-mcp-admission-guard-one.service','workspace-mcp-admission-guard-one.timer'):
            self.host.states[unit] = {'enabled':False,'active':False}
        preview = self.service.preview(['one'])
        self.assertTrue(preview['ok'], preview)
        row = preview['instances'][0]
        from workspace_mcp_manager.domain import DesiredInstance
        self.host.states['coding-tools-mcp-one.service'] = {'enabled':True,'active':False}
        with patch('workspace_mcp_manager.lifecycle.HostLifecycleWorker') as worker:
            worker.return_value.run.return_value = {'ok':True}
            with self.assertRaises(ImportBlocked):
                self.service.apply_candidate(DesiredInstance.from_dict(row['desired']),row)

    def test_symlinked_registry_is_rejected_before_handover(self):
        self.config.mkdir(parents=True)
        other = self.home / 'other-registry'; other.mkdir()
        self.paths.registry_dir.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ImportBlocked):
            LegacyImportService(self.paths, self.registry, host=self.host, release=self.release)
        self.assertEqual(list(other.iterdir()), [])


class ImportCliTests(unittest.TestCase):
    def test_json_transport_handles_preview_before_instance_lookup(self):
        from workspace_mcp_manager.cli import main
        from contextlib import redirect_stdout
        from io import StringIO
        paths = unittest.mock.Mock()
        paths.registry_dir = Path('/tmp/unused-import-registry')
        output = StringIO()
        with patch('workspace_mcp_manager.cli.ManagerPaths.for_current_user', return_value=paths), patch('workspace_mcp_manager.cli.LegacyImportService') as service, patch('sys.stdin', StringIO('{"selected":["one"]}')), redirect_stdout(output):
            service.return_value.preview.return_value = {'ok': False, 'blockers': ['fixture']}
            code = main(['_runtime', 'import', 'preview'])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_public_apply_requires_reviewed_fingerprint(self):
        from workspace_mcp_manager.cli import build_parser
        args = build_parser().parse_args(['instance', 'import', 'apply', '--preview', '/tmp/review.json', '--expected-preview-fingerprint', 'abc'])
        self.assertEqual(args.import_action, 'apply')
        self.assertEqual(args.expected_preview_fingerprint, 'abc')
