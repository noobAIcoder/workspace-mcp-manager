from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CURRENT = 'dist-' + 'a' * 40
PREVIOUS = 'dist-' + 'b' * 40


class GuidedSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'account home'
        self.prefix = self.home / 'custom prefix'
        self.calls = self.root / 'calls'
        shutil.copy2(ROOT / 'bootstrap.sh', self.root / 'setup.sh')

    def run_menu(self, input='0\n', *, ownership='standalone_distribution', pending=False, error=None, legacy=False, apply_error=False):
        status = {'ok': True, 'ownership': ownership, 'instances': {'one': {}},
                  'recovery_required': pending, 'current': CURRENT,
                  'releases': [{'id': CURRENT, 'version': '0.2.0', 'components': {}},
                               {'id': PREVIOUS, 'version': '0.1.0', 'components': {}}]}
        if ownership == 'absent':
            status.update(current=None, instances={}, releases=[])
        elif ownership == 'recognized_existing':
            status.update(current='c' * 64, releases=[])
        reply = json.dumps({'ok': False, 'error': error} if error else status)
        preview = json.dumps({'ok': True, 'status': status, 'action': 'uninstall',
                              'preserved': ['credentials', 'workspaces'],
                              'recovery_command': 'bash /offline/setup.sh --interactive'})
        apply_reply = '''mkdir -p -- "$TEST_HOME/.local/state/workspace-mcp-manager"
printf '{}\\n' > "$TEST_HOME/.local/state/workspace-mcp-manager/maintenance-transaction.json"
printf '%s\\n' '{"ok":false,"error":"second removal failed","completed_instances":["one"],"remaining_instances":["two"]}'
exit 2''' if apply_error else "printf '%s\\n' '{\"ok\":true,\"action\":\"complete\"}'"
        installer = '''#!/bin/bash
printf '%s\\n' "$*" >> "$TEST_CALLS"
case "$1" in
 --maintenance-status) printf '%s\\n' REPLY; STATUS_EXIT ;;
 --check) printf '%s\\n' 'historical check failed' >&2; exit 2 ;;
 --maintenance-preview) printf '%s\\n' PREVIEW ;;
 --maintenance-apply) cat >/dev/null; APPLY_REPLY ;;
 --recover-only) printf '%s\\n' '{"ok":true,"action":"recover"}' ;;
 *) exit 2 ;;
esac
'''.replace('APPLY_REPLY', apply_reply).replace('REPLY', shlex.quote(reply)).replace('PREVIEW', shlex.quote(preview)).replace('STATUS_EXIT', 'exit 2' if error else 'exit 0')
        (self.root / 'install.sh').write_text(installer)
        if legacy:
            (self.prefix / 'lib/workspace-mcp').mkdir(parents=True, exist_ok=True)
        result = subprocess.run(['bash', str(self.root / 'setup.sh'), '--interactive',
                                 '--account-home', str(self.home), '--prefix', str(self.prefix)],
                                input=input, text=True, capture_output=True, timeout=15,
                                env={**os.environ, 'TEST_CALLS': str(self.calls), 'TEST_HOME': str(self.home)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_root_uses_state_specific_tasks_and_hides_release_ids(self):
        for state, label in [('absent', 'Install manager'), ('recognized_existing', 'Upgrade manager installation'),
                             ('standalone_distribution', 'Check for manager updates')]:
            with self.subTest(state=state):
                output = self.run_menu(ownership=state)
                self.assertIn('1. ' + label, output)
                self.assertIn('2. Open instance manager', output)
                self.assertIn('3. Maintenance', output)
                self.assertIn('4. Installation details', output)
                self.assertNotIn(CURRENT, output)
                self.assertNotIn('5. Import legacy toolkit instances', output)
                self.assertNotIn('--check', self.calls.read_text())

    def test_legacy_import_is_conditional(self):
        output = self.run_menu(legacy=True)
        self.assertIn('5. Import legacy toolkit instances', output)

    def test_inspection_failure_does_not_claim_absence_or_zero_instances(self):
        output = self.run_menu(error='fixture inspection failure')
        self.assertIn('Installation status could not be verified', output)
        self.assertNotIn('0 configured instances', output)
        self.assertNotIn('No manager installation', output)
        self.assertIn('fixture inspection failure', output)

    def test_details_show_target_and_release_identity(self):
        output = self.run_menu('4\n0\n0\n')
        self.assertIn(CURRENT, output)
        self.assertIn(str(self.home), output)
        self.assertIn(str(self.prefix), output)
        self.assertNotIn('--maintenance-apply', self.calls.read_text())

    def test_blank_and_eof_exit_without_mutation(self):
        for input in ('\n', '', '3\n\n\n', '1\n'):
            with self.subTest(input=input):
                self.run_menu(input)
                self.assertNotIn('--maintenance-apply', self.calls.read_text())
                self.assertNotIn('--recover-only', self.calls.read_text())

    def test_uninstall_requires_exact_confirmation(self):
        for confirmation in ('', 'yes', 'UNINSTALL'):
            with self.subTest(confirmation=confirmation):
                self.calls.unlink(missing_ok=True)
                output = self.run_menu('3\n3\n' + confirmation + '\n0\n')
                self.assertIn('Type UNINSTALL', output)
                self.assertIn('bash /offline/setup.sh --interactive', output)
                self.assertEqual('--maintenance-apply' in self.calls.read_text(), confirmation == 'UNINSTALL')

    def test_recovery_has_priority_and_requires_confirmation(self):
        output = self.run_menu('1\n3\n1\ny\n0\n', pending=True)
        self.assertIn('Recommended: recover', output)
        self.assertIn('--recover-only', self.calls.read_text())
        self.assertNotIn('--check', self.calls.read_text())

    def test_rollback_selection_excludes_active_release(self):
        output = self.run_menu('3\n2\n1\n\n0\n')
        self.assertIn('1. Manager 0.1.0', output)
        self.assertNotIn('2. Manager 0.2.0', output)
        self.assertIn('--maintenance-target ' + PREVIOUS, self.calls.read_text())
        self.assertNotIn('--maintenance-apply', self.calls.read_text())

    def test_predecessor_cannot_uninstall_or_rollback(self):
        output = self.run_menu('3\n2\n3\n0\n0\n', ownership='recognized_existing')
        self.assertIn('Upgrade the manager installation first', output)
        self.assertNotIn('--maintenance-preview', self.calls.read_text())

    def test_partial_uninstall_explains_remaining_work_and_recovery(self):
        output = self.run_menu('3\n3\nUNINSTALL\n0\n', apply_error=True)
        self.assertIn('Removed deployments: one', output)
        self.assertIn('Deployments still requiring removal: two', output)
        self.assertIn('Recover interrupted operation before retrying', output)
        self.assertIn('bash /offline/setup.sh --interactive', output)

    def test_recovery_cancels_by_default(self):
        output = self.run_menu('3\n1\n\n0\n', pending=True)
        self.assertIn('Cancelled: recovery was not started', output)
        self.assertNotIn('--recover-only', self.calls.read_text())

    def test_unknown_installation_cannot_start_installation(self):
        output = self.run_menu('1\n0\n', error='foreign installation')
        self.assertIn('Blocked: verify installation status', output)
        self.assertNotIn('Checking the release server', output)

    def test_installed_setup_survives_removal_of_its_program_directory(self):
        program = self.root / 'program/distribution'
        program.mkdir(parents=True)
        shutil.copy2(ROOT / 'bootstrap.sh', program / 'setup.sh')
        capsule = self.home / '.local/state/workspace-mcp-manager/setup-recovery'
        capsule.mkdir(parents=True)
        shutil.copy2(ROOT / 'bootstrap.sh', capsule / 'setup.sh')
        (capsule / 'install.sh').write_text("#!/bin/bash\nprintf '%s\\n' '{\"ok\":true,\"ownership\":\"absent\",\"instances\":{},\"releases\":[]}'\n")
        (program / 'install.sh').write_text('''#!/bin/bash
case "$1" in
 --maintenance-status) printf '%s\\n' '{"ok":true,"ownership":"standalone_distribution","current":"dist-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","instances":{},"releases":[]}' ;;
 --maintenance-preview) printf '%s\\n' '{"ok":true,"action":"uninstall","status":{"instances":{}}}' ;;
 --maintenance-apply) cat >/dev/null; mv -- "$TEST_PROGRAM" "$TEST_QUARANTINE"; printf '%s\\n' '{"ok":true,"action":"uninstall"}' ;;
 *) exit 2 ;;
esac
''')
        result = subprocess.run(['bash', str(program / 'setup.sh'), '--interactive', '--account-home', str(self.home)],
                                input='3\n3\nUNINSTALL\n0\n', text=True, capture_output=True, timeout=15,
                                env={**os.environ, 'TEST_PROGRAM': str(program.parent), 'TEST_QUARANTINE': str(self.root / 'quarantine')})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('Completed: manager uninstalled', result.stdout)
        self.assertIn('No manager installation was found', result.stdout)


class GuidedInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.marker = self.home / '.local/state/workspace-mcp-manager/distribution.json'
        self.tui_marker = self.root / 'opened'
        self.home.mkdir()
        self.remote = self.root / 'remote'
        (self.remote / 'scripts').mkdir(parents=True)
        (self.remote / 'install-versions.lock').write_text((ROOT / 'install-versions.lock').read_text())
        (self.remote / 'scripts/install.sh').write_text('''#!/bin/bash
set -eu
for arg in "$@"; do
 if [[ "$arg" == --check ]]; then
  printf '%s\\n' '{"ok":true,"ownership":"absent","install_required":true,"recovery_required":false,"candidate_product_version":"0.3.0","candidate_distribution_id":"dist-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
  exit 0
 fi
done
mkdir -p -- "$(dirname -- "$TEST_APPLIED")"
printf '{}\\n' > "$TEST_APPLIED"
printf '%s\\n' '{"ok":true,"action":"install","product_version":"0.3.0"}'
''')
        for command in (['git', 'init', '-q', '-b', 'main'], ['git', 'add', '.'],
                        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture']):
            subprocess.run(command, cwd=self.remote, check=True)
        (self.root / 'install.sh').write_text('''#!/bin/bash
if [[ -f "$TEST_APPLIED" ]]; then
 printf '%s\\n' '{"ok":true,"ownership":"standalone_distribution","current":"dist-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","instances":{},"releases":[]}'
else
 printf '%s\\n' '{"ok":true,"ownership":"absent","instances":{},"releases":[]}'
fi
''')
        tui = self.home / '.local/bin/workspace-mcp-manager-tui'
        tui.parent.mkdir(parents=True)
        tui.write_text('#!/bin/bash\ntouch "$TEST_TUI_MARKER"\nprintf "Instance manager opened\\n"\n')
        tui.chmod(0o755)

    def run_menu(self, input, *, ready=True, ref='refs/heads/main', configure=False, current=False):
        # Control prerequisites without depending on the test machine's OS/systemd.
        source = (ROOT / 'bootstrap.sh').read_text()
        boundary = '\n[[ -n "$PREFIX" ]] || PREFIX="$ACCOUNT_HOME/.local"'
        source = source.replace(boundary, '\nsetup_readiness() { host_ready=' + ('true' if ready else 'false') + '; readiness="fixture: user systemd unavailable"; }\n' + boundary)
        (self.root / 'setup.sh').write_text(source)
        if current:
            installer = self.remote / 'scripts/install.sh'
            installer.write_text(installer.read_text().replace('"install_required":true', '"install_required":false'))
            subprocess.run(['git', 'add', '.'], cwd=self.remote, check=True)
            subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'current fixture'], cwd=self.remote, check=True)
        args = ['bash', str(self.root / 'setup.sh'), '--interactive', '--account-home', str(self.home),
                '--qualification', '--repository', self.remote.as_uri(), '--ref', ref]
        if configure:
            args.append('--configure')
        result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=20,
                                env={**os.environ, 'TEST_APPLIED': str(self.marker), 'TEST_TUI_MARKER': str(self.tui_marker)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_success_offers_next_steps_and_opens_manager_only_when_selected(self):
        result = self.run_menu('1\n1\ny\n1\n0\n')
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.tui_marker.exists())
        self.assertIn('Candidate manager version: 0.3.0', result.stdout)
        self.assertIn('Completed: manager installed', result.stdout)
        self.assertIn('Next steps', result.stdout)
        self.assertLess(result.stdout.index('Completed:'), result.stdout.index('Next steps'))

    def test_cancelled_review_does_not_offer_success_next_steps(self):
        result = self.run_menu('1\n1\n\n0\n')
        self.assertFalse(self.marker.exists())
        self.assertIn('Cancelled: no installation changes', result.stdout)
        self.assertNotIn('Next steps', result.stdout)

    def test_prerequisite_failure_blocks_before_acquisition(self):
        result = self.run_menu('1\n1\n0\n', ready=False)
        self.assertFalse(self.marker.exists())
        self.assertIn('prerequisites need attention', result.stdout)
        self.assertNotIn('Checking the release server', result.stdout)

    def test_release_server_failure_has_actionable_result(self):
        result = self.run_menu('1\n1\n0\n', ref='refs/heads/missing')
        self.assertFalse(self.marker.exists())
        self.assertIn('installation/update did not complete', result.stdout)
        self.assertIn('Maintenance', result.stdout)

    def test_configure_opens_manager_without_a_second_next_steps_prompt(self):
        result = self.run_menu('1\n1\ny\n0\n', configure=True)
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.tui_marker.exists())
        self.assertIn('Instance manager opened', result.stdout)
        self.assertNotIn('Next steps', result.stdout)

    def test_already_current_does_not_mutate_or_offer_success_next_steps(self):
        result = self.run_menu('1\n1\n0\n', current=True)
        self.assertIn('Already current', result.stdout)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.tui_marker.exists())
        self.assertNotIn('Next steps', result.stdout)

    def test_success_exit_does_not_open_manager(self):
        result = self.run_menu('1\n1\ny\n0\n')
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.tui_marker.exists())
        self.assertIn('Next steps', result.stdout)

    def test_success_return_refreshes_setup(self):
        result = self.run_menu('1\n1\ny\n2\n0\n')
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.tui_marker.exists())
        self.assertIn('1. Check for manager updates', result.stdout)

    def test_eof_during_review_cancels_installation(self):
        result = self.run_menu('1\n1\n')
        self.assertFalse(self.marker.exists())
        self.assertIn('Cancelled: no installation changes', result.stdout)


    def prepare_full_upgrade(self):
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_text('{"previous":true}')
        manager = self.home / '.local/lib/workspace-mcp-manager/releases' / CURRENT / 'bin/workspace-mcp-manager'
        manager.parent.mkdir(parents=True)
        self.fleet_marker = self.root / 'fleet-applied'
        preview = {'ok':True, 'account_home':str(self.home), 'prefix':str(self.home / '.local'),
                   'target_root':str(manager.parents[1]), 'target_release':CURRENT, 'fingerprint':'fleet-preview',
                   'product_version':'0.3.0', 'instances':[{'instance_id':'one', 'original':{'mcp':{'binary':'/old/mcp'},'tunnel':{'binary':'/old/tunnel'}},
                   'candidate':{'mcp':{'binary':'/new/mcp'},'tunnel':{'binary':'/new/tunnel'},'lifecycle':{'deployment':'present','runtime':'running'}},'force_restart':True,'valid':True,'errors':[]}]}
        manager.write_text('#!/bin/bash\ncase "$3" in\n preview) printf \'%s\\n\' ' + shlex.quote(json.dumps(preview)) + '\n;;\n apply) touch ' + shlex.quote(str(self.fleet_marker)) + '; printf \'%s\\n\' \'{"ok":true,"action":"instance-upgrade","completed":["one"],"remaining":[]}\';;\n *) exit 2;;\nesac\n')
        manager.chmod(0o755)

    def test_full_upgrade_requires_second_confirmation(self):
        self.prepare_full_upgrade()
        result = self.run_menu('6\n1\ny\n\n0\n')
        self.assertIn('Type UPGRADE', result.stdout)
        self.assertFalse(self.fleet_marker.exists())
        self.assertIn('instance runtimes were not changed', result.stdout)

    def test_full_upgrade_refreshes_an_already_current_release(self):
        self.prepare_full_upgrade()
        result = self.run_menu('6\n1\nUPGRADE\n0\n', current=True)
        self.assertIn('Already current', result.stdout)
        self.assertTrue(self.fleet_marker.exists())
        self.assertIn('Upgrade manager and all instances', result.stdout)

    def test_cancelled_manager_stage_does_not_start_instance_stage(self):
        self.prepare_full_upgrade()
        result = self.run_menu('6\n1\n\n0\n')
        self.assertFalse(self.fleet_marker.exists())
        self.assertNotIn('Type UPGRADE', result.stdout)

    def test_full_upgrade_configure_opens_manager_after_refresh(self):
        self.prepare_full_upgrade()
        result = self.run_menu('6\n1\ny\nUPGRADE\n0\n', configure=True)
        self.assertTrue(self.fleet_marker.exists())
        self.assertTrue(self.tui_marker.exists())
        self.assertNotIn('Next steps', result.stdout)

    def test_full_upgrade_refuses_preview_for_another_account(self):
        self.prepare_full_upgrade()
        manager = self.home / '.local/lib/workspace-mcp-manager/releases' / CURRENT / 'bin/workspace-mcp-manager'
        manager.write_text(manager.read_text().replace(str(self.home), '/different-account'))
        result = self.run_menu('6\n1\n0\n', current=True)
        self.assertFalse(self.fleet_marker.exists())
        self.assertNotIn('Type UPGRADE', result.stdout)
        self.assertIn('preview does not match the selected account', result.stdout)

    def test_maintenance_resumes_recorded_instance_upgrade(self):
        self.prepare_full_upgrade()
        manager = self.home / '.local/lib/workspace-mcp-manager/releases' / CURRENT / 'bin/workspace-mcp-manager'
        manager.write_text(manager.read_text().replace(' apply)', ' apply|recover)'))
        (self.marker.parent / 'instance-upgrade.json').write_text('{}')
        status = {'ok':True,'ownership':'standalone_distribution','current':CURRENT,'instances':{},'releases':[],
                  'recovery_required':True,'instance_upgrade':{'target_release':CURRENT}}
        (self.root / 'install.sh').write_text("#!/bin/bash\nprintf '%s\\n' " + shlex.quote(json.dumps(status)) + '\n')
        result = self.run_menu('3\n1\ny\n0\n')
        self.assertTrue(self.fleet_marker.exists())
        self.assertIn('Completed: instance runtimes refreshed', result.stdout)

    def test_malformed_pending_upgrade_never_dispatches_unverified_recovery(self):
        self.prepare_full_upgrade()
        (self.marker.parent / 'instance-upgrade.json').write_text('{}')
        result = self.run_menu('3\n1\ny\n0\n')
        self.assertFalse(self.fleet_marker.exists())
        self.assertIn('recovery target could not be verified', result.stdout)


if __name__ == '__main__':
    unittest.main()
