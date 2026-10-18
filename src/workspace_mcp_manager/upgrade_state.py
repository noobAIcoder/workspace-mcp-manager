"""Instance-upgrade journal contract and scoped authority; stdlib/Python 3.10."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path
import re

JOURNAL_NAME = 'instance-upgrade.json'
_AUTHORITY = ContextVar('instance_upgrade_authority', default=frozenset())


@contextmanager
def upgrade_authority(state_root, instance_id):
    token = _AUTHORITY.set(_AUTHORITY.get() | {(str(Path(state_root).resolve()), instance_id)})
    try:
        yield
    finally:
        _AUTHORITY.reset(token)


def upgrade_authorized(state_root, instance_id):
    return (str(Path(state_root).resolve()), instance_id) in _AUTHORITY.get()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate journal key: ' + key)
        result[key] = value
    return result


def read_upgrade(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('instance upgrade journal must be a regular file')
    try:
        data = json.loads(path.read_text(), object_pairs_hook=_unique)
        required = {'schema_version', 'token', 'account_home', 'prefix', 'registry_dir', 'target_root', 'target_release', 'backup', 'entries', 'status', 'failed', 'error'}
        if not isinstance(data, dict) or set(data) != required or type(data['schema_version']) is not int or data['schema_version'] != 1:
            raise ValueError('unsupported instance upgrade journal')
        if not re.fullmatch('[0-9a-f]{32}', data['token']) or not re.fullmatch('dist-[0-9a-f]{40}', data['target_release']):
            raise ValueError('invalid upgrade identity')
        home = path.parents[3]
        prefix = Path(data['prefix'])
        target = prefix / 'lib/workspace-mcp-manager/releases' / data['target_release']
        if not prefix.is_absolute() or '..' in prefix.parts or data['account_home'] != str(home):
            raise ValueError('invalid upgrade account or prefix')
        if data['registry_dir'] != str(home / '.config/workspace-mcp-manager/instances') or data['target_root'] != str(target):
            raise ValueError('invalid upgrade target')
        if data['backup'] != str(path.parent / 'backups' / ('instance-upgrade-' + data['token'])):
            raise ValueError('invalid upgrade backup')
        if data['status'] not in {'running', 'failed', 'completed'} or not isinstance(data['entries'], list):
            raise ValueError('invalid upgrade progress')
        names = []
        for row in data['entries']:
            if not isinstance(row, dict) or set(row) != {'instance_id', 'original', 'candidate', 'original_fingerprint', 'candidate_fingerprint', 'force_restart', 'plan_fingerprint', 'phase', 'apply_transaction'}:
                raise ValueError('invalid upgrade entry')
            iid = row['instance_id']
            if not isinstance(iid, str) or len(iid) > 63 or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', iid):
                raise ValueError('invalid upgrade instance')
            names.append(iid)
            if row['phase'] not in {'pending', 'updating', 'applying', 'completed'} or type(row['force_restart']) is not bool:
                raise ValueError('invalid instance upgrade phase')
            for field in ('original_fingerprint', 'candidate_fingerprint'):
                if not isinstance(row[field], str) or not re.fullmatch('[0-9a-f]{64}', row[field]):
                    raise ValueError('invalid declaration fingerprint')
            if row['plan_fingerprint'] is not None and (not isinstance(row['plan_fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}', row['plan_fingerprint'])):
                raise ValueError('invalid plan fingerprint')
            transaction = row['apply_transaction']
            if transaction is not None and (not isinstance(transaction, str) or not re.fullmatch('[A-Za-z0-9_-]+', transaction)):
                raise ValueError('invalid apply transaction')
            original, candidate = row['original'], row['candidate']
            if not isinstance(original, dict) or not isinstance(candidate, dict) or original.get('instance_id') != iid or candidate.get('instance_id') != iid:
                raise ValueError('invalid declaration snapshot')
            expected = deepcopy(original)
            expected['mcp']['binary'] = str(target / 'runtimes/coding-tools-mcp/bin/coding-tools-mcp')
            expected['tunnel']['binary'] = str(target / 'runtimes/tunnel-client/tunnel-client')
            if expected != candidate:
                raise ValueError('upgrade changes fields other than runtime paths')
            lifecycle = original['lifecycle']
            if row['force_restart'] != (lifecycle == {'deployment': 'present', 'runtime': 'running'}):
                raise ValueError('invalid restart policy')
        if names != sorted(set(names)) or (data['failed'] is not None and data['failed'] not in names):
            raise ValueError('invalid upgrade instance sequence')
        if data['error'] is not None and not isinstance(data['error'], str):
            raise ValueError('invalid upgrade error')
        if data['status'] == 'completed' and any(row['phase'] != 'completed' for row in data['entries']):
            raise ValueError('incomplete completed upgrade')
        return data
    except (OSError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('invalid instance upgrade journal: ' + str(exc)) from exc
