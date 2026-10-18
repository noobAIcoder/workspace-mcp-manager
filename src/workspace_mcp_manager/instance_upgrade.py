"""Reviewed, resumable instance-runtime refresh over the normal reconciler."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import uuid

from .distribution_runtime import DistributionRuntimeError, runtime_defaults
from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError
from .lifecycle import HostApplyService
from .maintenance import Maintenance, MaintenanceError, atomic_json, digest, read_json, sync_dir, validate_release
from .operator_projection import OperatorProjectionService
from .redaction import redact_text
from .upgrade_state import JOURNAL_NAME, read_upgrade, upgrade_authority


class InstanceUpgradeService:
    def __init__(self, paths, registry, *, projection=None, apply_service=None):
        self.paths, self.registry = paths, registry
        self.journal = paths.state_root / JOURNAL_NAME
        self.projection = projection or OperatorProjectionService(paths, registry)
        self.apply_service = apply_service or HostApplyService(paths, registry)

    def blocked(self, message, *, stale=False):
        raise ManagerError(ErrorCode.STALE_STATE if stale else ErrorCode.RECONCILIATION_CONFLICT, message)

    def target(self):
        if self.paths.registry_dir != self.paths.account_home / '.config/workspace-mcp-manager/instances':
            self.blocked('instance upgrade requires the canonical account registry')
        try:
            defaults = runtime_defaults(self.paths)
        except (DistributionRuntimeError, OSError) as exc:
            self.blocked(str(exc))
        root = defaults.distribution_root
        if root is None:
            self.blocked('instance upgrade requires an installed, verified distribution')
        prefix = root.parents[3]
        try:
            data = validate_release(root)
            status = Maintenance(self.paths.account_home, prefix).status()
            if status['ownership'] != 'standalone_distribution' or status['current'] != root.name:
                self.blocked('the executing instance-upgrade release is no longer current')
            if status['recovery_required'] and not self.journal.exists():
                self.blocked('recover pending distribution maintenance or imports first')
        except (ValueError, OSError, MaintenanceError) as exc:
            self.blocked(str(exc))
        for name in ('distribution-transaction.json', 'maintenance-transaction.json'):
            p = self.paths.state_root / name
            if p.exists() or p.is_symlink():
                self.blocked('recover pending distribution maintenance first')
        if list((self.paths.state_root / 'imports').glob('*.json')):
            self.blocked('recover pending legacy imports first')
        return defaults, prefix, data

    def preview(self):
        if self.journal.exists() or self.journal.is_symlink():
            self.blocked('recover the pending instance upgrade first')
        defaults, prefix, manifest = self.target()
        rows = []
        for desired in self.registry.list():
            original = desired.to_dict()
            candidate = deepcopy(original)
            candidate['mcp']['binary'] = str(defaults.coding_tools_mcp)
            candidate['tunnel']['binary'] = str(defaults.tunnel_client)
            proposed = DesiredInstance.from_dict(candidate)
            present = candidate['lifecycle']['deployment'] == 'present'
            view = self.projection.preview(proposed) if present else None
            valid = not present or (view.get('ok', False) and view['proposed_plan']['valid'] and view['collision_result']['clear'])
            rows.append({'instance_id': desired.instance_id.value, 'original': original, 'candidate': candidate,
                         'original_fingerprint': desired.fingerprint(), 'candidate_fingerprint': proposed.fingerprint(),
                         'force_restart': candidate['lifecycle'] == {'deployment': 'present', 'runtime': 'running'},
                         'plan_fingerprint': view['plan_fingerprint'] if view else None,
                         'valid': bool(valid), 'errors': view.get('errors', []) if view else [],
                         'operations': view.get('semantic_operations', []) if view else []})
        result = {'schema_version': 1, 'action': 'instance-upgrade', 'ok': all(row['valid'] for row in rows),
                  'account_home': str(self.paths.account_home), 'prefix': str(prefix), 'registry_dir': str(self.paths.registry_dir),
                  'target_root': str(defaults.distribution_root), 'target_release': defaults.distribution_root.name,
                  'product_version': manifest['product_version'], 'components': manifest['components'], 'instances': rows}
        result['fingerprint'] = digest(result)
        return result

    def apply(self, reviewed, expected_fingerprint):
        _, prefix, _ = self.target()
        maintenance = Maintenance(self.paths.account_home, prefix)
        try:
            with maintenance.locked():
                with maintenance.quiescent_instances([d.instance_id.value for d in self.registry.list()]):
                    fresh = self.preview()
                    if not fresh['ok']:
                        self.blocked('instance upgrade preview contains blockers')
                    if fresh != reviewed or expected_fingerprint != fresh['fingerprint']:
                        self.blocked('instance upgrade preview is stale; review again', stale=True)
                    token = uuid.uuid4().hex
                    backup_root = self.paths.state_root / 'backups'
                    if backup_root.is_symlink():
                        self.blocked('unsafe instance upgrade backup directory')
                    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    backup = backup_root / ('instance-upgrade-' + token)
                    backup.mkdir(mode=0o700)
                    rows = []
                    for row in fresh['instances']:
                        atomic_json(backup / (row['instance_id'] + '.json'), row['original'])
                        rows.append({key: value for key, value in row.items() if key not in {'valid', 'errors', 'operations'}})
                        rows[-1].update(phase='pending', apply_transaction=None)
                    sync_dir(backup); sync_dir(backup_root)
                    state = {key: fresh[key] for key in ('account_home', 'prefix', 'registry_dir', 'target_root', 'target_release')}
                    state.update(schema_version=1, token=token, backup=str(backup), entries=rows, status='running', failed=None, error=None)
                    atomic_json(self.journal, state)
                return self._continue(state)
        except MaintenanceError as exc:
            self.blocked(str(exc))

    def recover(self):
        try:
            state = read_upgrade(self.journal)
        except ValueError as exc:
            self.blocked(str(exc))
        defaults, prefix, _ = self.target()
        if state['target_root'] != str(defaults.distribution_root) or state['prefix'] != str(prefix):
            self.blocked('resume instance upgrade using its recorded immutable target release')
        try:
            with Maintenance(self.paths.account_home, prefix).locked():
                self.target()
                # Re-read under the lock: another recovery may have advanced it.
                state = read_upgrade(self.journal)
                return self._continue(state)
        except (MaintenanceError, ValueError) as exc:
            self.blocked(str(exc))

    def _verify(self, desired):
        if desired.lifecycle.to_dict()['deployment'] == 'absent':
            return
        view = self.projection.preview(desired)
        if not view['proposed_plan']['valid'] or any(item['operation'] != 'NOOP' for item in view['proposed_plan']['operations']):
            self.blocked('completed instance no longer converges: ' + desired.instance_id.value)
        self.apply_service._qualify(desired)

    def _committed_apply(self, state, row):
        path = self.paths.state_root / 'applied' / (row['instance_id'] + '.json')
        if not path.exists():
            return None
        applied = read_json(path)
        tx = applied.get('transaction_id')
        if not isinstance(tx, str) or not tx or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in tx):
            self.blocked('unsafe applied transaction identity')
        evidence = self.paths.state_root / 'transactions' / row['instance_id'] / (tx + '.json')
        if not evidence.exists():
            return None
        record = read_json(evidence)
        if (applied.get('desired_fingerprint') == row['candidate_fingerprint']
                and record.get('desired_fingerprint') == row['candidate_fingerprint']
                and record.get('instance_id') == row['instance_id']
                and record.get('action') == 'instance-upgrade:' + state['token']):
            return tx
        return None

    def _continue(self, state):
        current_id = None
        try:
            # Validate all saved configuration before performing any resumed work.
            backup = Path(state['backup'])
            if backup.is_symlink() or backup.parent.is_symlink() or not backup.is_dir():
                self.blocked('instance upgrade backup is missing or unsafe')
            actual = {d.instance_id.value: d for d in self.registry.list()}
            if set(actual) != {row['instance_id'] for row in state['entries']}:
                self.blocked('configured instance set changed during upgrade')
            for row in state['entries']:
                current_id = row['instance_id']
                original = DesiredInstance.from_dict(row['original'])
                candidate = DesiredInstance.from_dict(row['candidate'])
                if original.fingerprint() != row['original_fingerprint'] or candidate.fingerprint() != row['candidate_fingerprint']:
                    self.blocked('saved upgrade declaration fingerprint mismatch')
                if read_json(backup / (current_id + '.json')) != row['original']:
                    self.blocked('instance upgrade backup changed')
                allowed = {row['original_fingerprint']} if row['phase'] == 'pending' else {row['original_fingerprint'], row['candidate_fingerprint']}
                if row['phase'] == 'completed':
                    allowed = {row['candidate_fingerprint']}
                if actual[current_id].fingerprint() not in allowed:
                    self.blocked('instance configuration changed during upgrade: ' + current_id)
                if row['phase'] == 'completed':
                    self._verify(candidate)
            for row in state['entries']:
                current_id = row['instance_id']
                if row['phase'] == 'completed':
                    continue
                desired = DesiredInstance.from_dict(row['candidate'])
                with upgrade_authority(self.paths.state_root, current_id):
                    print('Refreshing instance: ' + current_id + '…', file=sys.stderr, flush=True)
                    committed = self._committed_apply(state, row) if row['phase'] == 'applying' else None
                    if committed:
                        row['apply_transaction'] = committed
                        self._verify(desired)
                    else:
                        # Review before replacing the declaration. On resume an
                        # incomplete apply is replanned against actual owned resources.
                        view = self.projection.preview(desired) if desired.lifecycle.to_dict()['deployment'] == 'present' else None
                        if view and (not view['proposed_plan']['valid'] or not view['collision_result']['clear']):
                            self.blocked('instance reconciliation is blocked: ' + current_id)
                        if row['phase'] == 'pending' and view and view['plan_fingerprint'] != row['plan_fingerprint']:
                            self.blocked('instance plan changed since review: ' + current_id, stale=True)
                        current_fingerprint = self.registry.get(current_id).fingerprint()
                        if current_fingerprint not in {row['original_fingerprint'], row['candidate_fingerprint']}:
                            self.blocked('instance changed before runtime update: ' + current_id, stale=True)
                        row['phase'] = 'updating'; atomic_json(self.journal, state)
                        self.registry.update(desired, expected_current_fingerprint=current_fingerprint)
                        row['phase'] = 'applying'; atomic_json(self.journal, state)
                        if view:
                            result = self.apply_service.apply(current_id, force_restart=row['force_restart'],
                                                             action='instance-upgrade:' + state['token'],
                                                             expected_plan_fingerprint=view['plan_fingerprint'])
                            if not result.get('ok'):
                                self.blocked('instance apply did not complete: ' + current_id)
                            row['apply_transaction'] = result.get('transaction_id')
                    row['phase'] = 'completed'; atomic_json(self.journal, state)
                    print('Completed instance: ' + current_id, file=sys.stderr, flush=True)
            current = {d.instance_id.value: d.fingerprint() for d in self.registry.list()}
            if current != {row['instance_id']: row['candidate_fingerprint'] for row in state['entries']}:
                self.blocked('instance declarations changed before upgrade completion', stale=True)
            state.update(status='completed', failed=None, error=None)
            atomic_json(self.journal, state)
            # Retain the completed record with the backup; never prune releases.
            atomic_json(Path(state['backup']) / 'result.json', state)
            self.journal.unlink(); sync_dir(self.journal.parent)
            return self._result(state, True)
        except Exception as exc:
            state.update(status='failed', failed=current_id, error=redact_text(str(exc)))
            atomic_json(self.journal, state)
            return self._result(state, False)

    @staticmethod
    def _result(state, ok):
        return {'ok': ok, 'action': 'instance-upgrade', 'target_release': state['target_release'], 'backup': state['backup'],
                'completed': [r['instance_id'] for r in state['entries'] if r['phase'] == 'completed'],
                'failed': state['failed'], 'remaining': [r['instance_id'] for r in state['entries'] if r['phase'] != 'completed'],
                'error': state['error'], 'recovery_required': not ok}
