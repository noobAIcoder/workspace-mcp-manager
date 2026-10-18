"""Offline maintenance backend, dispatched by the distribution installer.

No acquisition or terminal interaction lives here. Distribution mutations share
install.sh's account lock; previews are read-only and checked again under lock.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import time
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid

COMMANDS = ('workspace-mcp-manager', 'workspace-mcp-manager-tui', 'workspace-mcp-reboot', 'workspace-mcp-manager-upgrade')
SETUP = 'workspace-mcp-manager-setup'
RELEASE = re.compile(r'dist-[0-9a-f]{40}\Z')


class MaintenanceError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MaintenanceError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    if path.is_symlink() or not path.is_file():
        raise MaintenanceError(f'not a regular file: {path}')
    try:
        value = json.loads(path.read_text(), object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise MaintenanceError(f'JSON object required: {path.name}')
        return value
    except (ValueError, OSError) as exc:
        raise MaintenanceError(f'cannot read {path.name}: {exc}') from exc


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def inventory(root):
    """Include directories and symlinks without following any link."""
    if root.is_symlink() or not root.is_dir():
        raise MaintenanceError(f'not an owned directory: {root}')
    result = {}
    for base, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            item = {'mode': f'{stat.S_IMODE(mode):04o}'}
            if stat.S_ISLNK(mode):
                item.update(type='symlink', target=os.readlink(path))
            elif stat.S_ISREG(mode):
                item.update(type='file', sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            elif stat.S_ISDIR(mode):
                item.update(type='directory')
            else:
                raise MaintenanceError(f'unsupported content: {path}')
            result[relative] = item
    return result


def validate_release(path):
    data = read_json(path / 'install.json')
    required = {'schema_version', 'product_version', 'repository', 'release_ref', 'release_commit', 'distribution_id', 'manager_source_digest', 'locked_inputs', 'components', 'manifest_scope', 'content_manifest'}
    if set(data) != required or data['schema_version'] != 3 or not RELEASE.fullmatch(path.name):
        raise MaintenanceError(f'unsupported release: {path.name}; migrate historical installations before maintenance')
    if data['distribution_id'] != path.name or path.name != 'dist-' + data['release_commit']:
        raise MaintenanceError('release identity mismatch')
    if data['manifest_scope'] != 'all-immutable-files-except-install.json; install.json-sha256-is-anchored-by-receipt':
        raise MaintenanceError('unsupported manifest scope')
    if not isinstance(data['content_manifest'], list) or any(not isinstance(item, dict) for item in data['content_manifest']):
        raise MaintenanceError('invalid release manifest')
    if not isinstance(data['components'], dict) or any(not isinstance(item, dict) or not isinstance(item.get('version'), str) for item in data['components'].values()):
        raise MaintenanceError('invalid release components')
    actual = inventory(path)
    actual = {key: {'path': key, **value} for key, value in actual.items() if value['type'] != 'directory' and key != 'install.json'}
    expected = {}
    for item in data['content_manifest']:
        name = item.get('path')
        if not isinstance(name, str) or not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or name in expected:
            raise MaintenanceError('unsafe/duplicate manifest entry')
        expected[name] = item
    if actual != expected:
        raise MaintenanceError(f'immutable release content changed: {path.name}')
    return data


class Maintenance:
    def __init__(self, home, prefix, *, lock_held=False):
        self.home = Path(home).absolute()
        self.prefix = Path(prefix).absolute()
        self.root = self.prefix / 'lib/workspace-mcp-manager'
        self.state = self.home / '.local/state/workspace-mcp-manager'
        self.config = self.home / '.config/workspace-mcp-manager'
        self.registry = self.config / 'instances'
        self.journal = self.state / 'maintenance-transaction.json'
        self.lock_held = lock_held
        if self.state.is_symlink() or self.config.is_symlink() or (self.prefix / 'bin').is_symlink():
            raise MaintenanceError('symlinked manager state/configuration root')

    @contextmanager
    def locked(self):
        if self.lock_held:
            yield
            return
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (self.state / 'distribution.lock').is_symlink():
            raise MaintenanceError('symlinked distribution lock')
        with (self.state / 'distribution.lock').open('a') as stream:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise MaintenanceError('another distribution mutation holds the account-level lock')
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def declarations(self):
        if self.registry.is_symlink():
            raise MaintenanceError('registry is a symlink')
        result = {}
        if self.registry.exists():
            for path in sorted(self.registry.glob('*.json')):
                data = read_json(path)
                if data.get('instance_id') != path.stem:
                    raise MaintenanceError('declaration identity mismatch')
                result[path.stem] = data
        return result

    def entrypoints(self, *, predecessor=False):
        result = {}
        for name in (*COMMANDS, SETUP):
            path = self.prefix / 'bin' / name
            if predecessor and name == COMMANDS[3]:
                if path.exists() or path.is_symlink():
                    raise MaintenanceError(f'unexpected predecessor entry point: {name}')
                continue
            if not path.exists() and not path.is_symlink():
                if name != SETUP and self.root.exists():
                    raise MaintenanceError(f'missing entry point: {name}')
                continue
            if name == 'workspace-mcp-reboot' and not self.root.exists() and path.is_symlink():
                toolkit = self.prefix / 'lib/workspace-mcp'
                version = toolkit / 'VERSION'
                helper = toolkit / name
                if not toolkit.is_symlink() and version.is_file() and not version.is_symlink() and version.read_text().strip() == '0.3.2' and helper.is_file() and not helper.is_symlink() and path.resolve() == helper:
                    continue
            if not path.is_symlink():
                raise MaintenanceError(f'foreign entry point: {path}')
            target = os.readlink(path)
            expected = '../lib/workspace-mcp-manager/current/bin/' + name
            if name == SETUP:
                if not re.fullmatch(r'\.\./lib/workspace-mcp-manager/releases/dist-[0-9a-f]{40}/bin/workspace-mcp-manager-setup', target):
                    raise MaintenanceError('foreign setup entry point')
                if not self.journal.exists() and not path.resolve().is_file():
                    raise MaintenanceError('setup entry point is incomplete')
            elif target != expected:
                raise MaintenanceError(f'foreign entry point: {name}')
            result[name] = target
        return result

    def status(self):
        declarations = self.declarations()
        current = self.root / 'current'
        predecessor = current.is_symlink() and re.fullmatch(r'releases/[0-9a-f]{64}', os.readlink(current)) is not None
        entries = self.entrypoints(predecessor=predecessor)
        pending = any(path.exists() or path.is_symlink() for path in [self.journal, self.state / 'distribution-transaction.json']) or bool(list((self.state / 'imports').glob('*.json')))
        base = {'ok': True, 'ownership': 'absent', 'current': None, 'releases': [],
                'instances': declarations, 'recovery_required': pending, 'entrypoints': entries}
        upgrade = self.state / 'instance-upgrade.json'
        if upgrade.exists() or upgrade.is_symlink():
            helper = Path(__file__).with_name('upgrade_state.py')
            if not helper.is_file() or helper.is_symlink():
                raise MaintenanceError('instance upgrade inspector is unavailable')
            spec = importlib.util.spec_from_file_location('workspace_upgrade_state', helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            try:
                state = module.read_upgrade(upgrade)
            except ValueError as exc:
                raise MaintenanceError(str(exc)) from exc
            if state['prefix'] != str(self.prefix):
                raise MaintenanceError('instance upgrade belongs to a different prefix')
            base.update(recovery_required=True, instance_upgrade={
                'target_release': state['target_release'], 'target_root': state['target_root'],
                'completed': [r['instance_id'] for r in state['entries'] if r['phase'] == 'completed'],
                'failed': state['failed'], 'remaining': [r['instance_id'] for r in state['entries'] if r['phase'] != 'completed']})
        if not self.root.exists() and not self.root.is_symlink():
            if entries and not self.journal.exists():
                raise MaintenanceError('entry points exist without an owned distribution')
            return base
        if self.root.is_symlink() or set(p.name for p in self.root.iterdir()) != {'current', 'releases'}:
            raise MaintenanceError('foreign or ambiguous installation contents')
        if not current.is_symlink() or not re.fullmatch(r'releases/(?:dist-[0-9a-f]{40}|[0-9a-f]{64})', os.readlink(current)):
            raise MaintenanceError('invalid current release')
        releases = self.root / 'releases'
        if releases.is_symlink() or not releases.is_dir():
            raise MaintenanceError('invalid releases directory')
        items = []
        legacy = []
        for path in sorted(releases.iterdir()):
            if re.fullmatch('[0-9a-f]{64}', path.name):
                # Historical migration retains these without a content manifest.
                # Preserve their complete bytes in the uninstall backup.
                data = read_json(path / 'install.json')
                required = ['src/workspace_mcp_manager', 'tui-runtime/site-packages/textual']
                if path.is_symlink() or data.get('schema_version') != 2 or not all((path / name).is_dir() and not (path / name).is_symlink() for name in required):
                    raise MaintenanceError('unrecognized historical release')
                for name in COMMANDS[:3]:
                    if not (path / 'bin' / name).is_file():
                        raise MaintenanceError('incomplete historical release')
                if not (path / 'requirements-tui.lock').is_file():
                    raise MaintenanceError('incomplete historical dependency lock')
                if predecessor and path.name == current.readlink().name:
                    if (path / 'requirements-tui.lock').is_symlink() or not all(os.access(path / 'bin' / name, os.X_OK) for name in COMMANDS[:3]):
                        raise MaintenanceError('incomplete predecessor release')
                inventory(path)
                legacy.append(path.name)
                continue
            data = validate_release(path)
            items.append({'id': path.name, 'version': data['product_version'],
                          'components': {key: value['version'] for key, value in data['components'].items()}})
        if predecessor:
            if current.readlink().name not in legacy:
                raise MaintenanceError('current predecessor release is missing')
            base.update(ownership='recognized_existing', current=current.readlink().name, retained_legacy=legacy)
            return base
        if not any(item['id'] == current.readlink().name for item in items):
            raise MaintenanceError('current release is missing')
        base.update(ownership='standalone_distribution', current=current.readlink().name, releases=items, retained_legacy=legacy)
        return base

    def manager_paths(self):
        source = Path(__file__).resolve().parents[1]
        if not (source / 'workspace_mcp_manager').is_dir():
            source = source / 'manager/src'
        if not (source / 'workspace_mcp_manager').is_dir():
            source = self.root / 'current/manager/src'
        sys.path.insert(0, str(source))
        from workspace_mcp_manager.paths import ManagerPaths
        return ManagerPaths(self.home, self.config, self.registry, self.state,
                            self.home / '.config/systemd/user', self.config / 'tunnel-profiles',
                            self.prefix / 'bin/workspace-mcp-manager')

    def removal_preview(self):
        from dataclasses import replace
        paths = self.manager_paths()
        from workspace_mcp_manager.domain import DeploymentTarget, RuntimeTarget, LifecycleIntent
        from workspace_mcp_manager.registry import InstanceRegistry
        from workspace_mcp_manager.planning import ReconciliationPlanner
        registry = InstanceRegistry(self.registry)
        result = []
        for desired in registry.list():
            absent = replace(desired, lifecycle=LifecycleIntent(DeploymentTarget.ABSENT, RuntimeTarget.STOPPED))
            plan = ReconciliationPlanner(paths, registry).plan(absent)
            if not plan.valid:
                raise MaintenanceError(f'deployment removal has conflicts: {desired.instance_id.value}')
            result.append({'instance_id': desired.instance_id.value,
                           'operations': [{'operation': item.operation.value, 'target': item.target}
                                          for item in plan.operations if item.operation.value != 'NOOP']})
        return result

    @contextmanager
    def quiescent_instances(self, names):
        # Wait out previously started applies/writes, then keep them excluded
        # across the final inspection and quarantine commit.
        with ExitStack() as stack:
            gate_directory = self.registry / '.locks'
            gate_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            gate = stack.enter_context((gate_directory / '.registry.lock').open('a'))
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise MaintenanceError('a registry write is still in progress')
                    time.sleep(0.05)
            for name in sorted(names):
                if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
                    raise MaintenanceError('unsafe instance lock name')
                for directory in (self.state / 'locks', self.registry / '.locks'):
                    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    stream = stack.enter_context((directory / (name + '.lock')).open('a'))
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise MaintenanceError('an instance operation is still in progress')
                            time.sleep(0.05)
            yield

    def remaining_units(self):
        result = []
        directory = self.home / '.config/systemd/user'
        needles = (str(self.root), str(self.prefix / 'bin/workspace-mcp-manager'), str(self.prefix / 'bin/workspace-mcp-reboot'))
        if directory.exists():
            for path in sorted(directory.rglob('*')):
                if path.suffix not in {'.service', '.timer'} or not path.is_file():
                    continue
                text = path.read_text(errors='replace')
                if any(needle in text for needle in needles):
                    result.append(str(path))
        listing = subprocess.run(['systemctl', '--user', 'list-units', '--all', '--type=service', '--no-legend', '--plain'], text=True, capture_output=True, timeout=20)
        if listing.returncode:
            raise MaintenanceError('cannot inspect user services; user systemd must be available')
        names = [line.split()[0] for line in listing.stdout.splitlines() if line.split()]
        if names:
            shown = subprocess.run(['systemctl', '--user', 'show', '--property=Id,ExecStart', '--', *names], text=True, capture_output=True, timeout=20)
            if shown.returncode:
                raise MaintenanceError('cannot inspect loaded user service commands')
            for block in shown.stdout.split('\n\n'):
                if any(needle in block for needle in needles):
                    result.append(next((line[3:] for line in block.splitlines() if line.startswith('Id=')), 'loaded service'))
        return sorted(set(result))

    def preview(self, action, target=None):
        if list((self.state / 'imports').glob('*.json')):
            raise MaintenanceError('recover pending legacy imports before distribution maintenance')
        if action not in {'rollback', 'uninstall'}:
            raise MaintenanceError('unsupported maintenance action')
        status = self.status()
        if status['recovery_required']:
            raise MaintenanceError('recover the pending transaction first')
        if status['ownership'] != 'standalone_distribution':
            raise MaintenanceError('maintenance requires a validated standalone installation')
        if action == 'rollback':
            if not target or not RELEASE.fullmatch(target) or target == status['current']:
                raise MaintenanceError('choose a different validated retained release')
            path = self.root / 'releases' / target
            validate_release(path)
            self.validate_runtime(path)
            self.validate_compatibility(path)
        snapshot = inventory(self.root)
        receipt = self.state / 'distribution.json'
        if receipt.is_symlink():
            raise MaintenanceError('foreign receipt blocks maintenance')
        receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest() if receipt.is_file() and not receipt.is_symlink() else None
        deployment_removal = self.removal_preview() if action == 'uninstall' else []
        units = self.remaining_units() if action == 'uninstall' else []
        expected_units = {prefix + name + suffix for name in status['instances']
                          for prefix, suffix in [('coding-tools-mcp-', '.service'), ('tunnel-client-', '.service'),
                                                 ('workspace-mcp-ssh-agent-', '.service'),
                                                 ('workspace-mcp-admission-guard-', '.service'), ('workspace-mcp-admission-guard-', '.timer')]}
        if any(Path(unit).name not in expected_units for unit in units):
            raise MaintenanceError('unregistered services reference this installation; resolve them before uninstall')
        payload = {'ok': True, 'action': action, 'target': target, 'status': status,
                   'remaining_units': units, 'receipt_sha256': receipt_sha,
                   'deployment_removal': deployment_removal,
                   'recovery_command': shlex.join(['bash', str(self.state / 'setup-recovery/setup.sh'), '--interactive', '--account-home', str(self.home), '--prefix', str(self.prefix)]),
                   'preserved': ['configuration', 'declaration records', 'profiles', 'credentials', 'workspaces', 'logs']}
        payload['fingerprint'] = digest({'preview': payload, 'inventory': snapshot})
        return payload

    def validate_runtime(self, release):
        data = read_json(release / 'install.json')
        commands = {'python': [str(release / 'runtimes/python/bin/python3'), '--version'],
                    'uv': [str(release / 'tools/uv/uv'), '--version'],
                    'tunnel_client': [str(release / 'runtimes/tunnel-client/tunnel-client'), '--version']}
        for name, argv in commands.items():
            result = subprocess.run(argv, text=True, capture_output=True, timeout=20)
            if result.returncode or result.stdout.strip() != data['components'][name]['expected_observable_identity']:
                raise MaintenanceError(f'rollback runtime validation failed: {name}')
        code = 'import importlib.metadata,sys; sys.path.insert(0,sys.argv[1]); print(importlib.metadata.version("coding-tools-mcp"))'
        result = subprocess.run([commands['python'][0], '-B', '-I', '-S', '-c', code, str(release / 'runtimes/coding-tools-mcp/site-packages')], text=True, capture_output=True, timeout=20)
        if result.returncode or result.stdout.strip() != data['components']['coding_tools_mcp']['expected_observable_identity']:
            raise MaintenanceError('rollback coding-tools identity mismatch')
        for name in COMMANDS[:2]:
            if subprocess.run([str(release / 'bin' / name), '--help'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode:
                raise MaintenanceError(f'rollback wrapper validation failed: {name}')

    def validate_compatibility(self, release):
        source = release / 'manager/src'
        code = 'import sys; sys.path.insert(0,sys.argv[1]); from workspace_mcp_manager.domain import DesiredInstance; DesiredInstance.from_json(sys.stdin.read())'
        for declaration in self.declarations().values():
            result = subprocess.run([str(release / 'runtimes/python/bin/python3'), '-B', '-I', '-S', '-c', code, str(source)], input=json.dumps(declaration), text=True, capture_output=True, timeout=20)
            if result.returncode:
                raise MaintenanceError(f'rollback release cannot parse declaration {declaration["instance_id"]}')

    def write_receipt(self, release):
        data = read_json(release / 'install.json')
        # Use the installer's established receipt representation.
        content = {'schema_version': 1, 'distribution_id': data['distribution_id'],
                   'release_commit': data['release_commit'], 'repository': data['repository'],
                   'release_ref': data['release_ref'], 'product_version': data['product_version'],
                   'release_path': str(release),
                   'verified_at': datetime.now(timezone.utc).isoformat(),
                   'install_json_sha256': hashlib.sha256((release / 'install.json').read_bytes()).hexdigest()}
        atomic_json(self.state / 'distribution.json', content)

    def save_journal(self, value):
        atomic_json(self.journal, value)

    def clear_journal(self):
        self.journal.unlink(missing_ok=True)
        sync_dir(self.state)

    def remove_instance(self, name):
        # Invoked on the host by install.sh, using the same lifecycle reconciler.
        # Import before program deletion; all removal is complete before rename.
        paths = self.manager_paths()
        from workspace_mcp_manager.lifecycle import HostLifecycleWorker
        from workspace_mcp_manager.registry import InstanceRegistry
        result = HostLifecycleWorker(paths, InstanceRegistry(self.registry)).run('remove', name)
        if result.get('ok') is False:
            raise MaintenanceError(f'instance removal failed: {name}')

    def prepare_recovery_capsule(self):
        directory = Path(__file__).resolve().parent
        installer = directory / 'install.sh'
        bootstrap = directory / 'setup.sh'
        if not installer.is_file():
            repo = directory.parents[1]
            installer = repo / 'scripts/install.sh'
            bootstrap = repo / 'bootstrap.sh'
        destination = self.state / 'setup-recovery'
        if destination.is_symlink():
            raise MaintenanceError('foreign recovery capsule')
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        for source, name in [(installer, 'install.sh'), (bootstrap, 'setup.sh'), (Path(__file__).resolve(), 'maintenance.py'), (directory / 'upgrade_state.py', 'upgrade_state.py')]:
            if not source.is_file():
                raise MaintenanceError('cannot prepare offline recovery capsule')
            target = destination / name
            if target.is_symlink():
                raise MaintenanceError('foreign recovery capsule file')
            temporary = destination / ('.' + name + '.' + uuid.uuid4().hex)
            shutil.copyfile(source, temporary)
            os.chmod(temporary, 0o700)
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        sync_dir(destination)

    def backup_user_data(self, backup):
        backup.mkdir(parents=True, mode=0o700)
        # Credentials remain in place. Preserve originals of resources which
        # lifecycle removal may delete, including profiles and runtime logs.
        for label, source in [('declarations', self.registry), ('tunnel-profiles', self.config / 'tunnel-profiles'), ('instances', self.state / 'instances')]:
            if source.is_symlink():
                raise MaintenanceError(f'cannot back up symlinked data directory: {source}')
            if source.is_dir():
                shutil.copytree(source, backup / label, symlinks=True)
        for path in (self.root / 'releases').iterdir():
            if re.fullmatch('[0-9a-f]{64}', path.name):
                shutil.copytree(path, backup / 'legacy-releases' / path.name, symlinks=True)
        # Ensure backups are durable before any deployment removal.
        for base, dirs, files in os.walk(backup):
            for name in files:
                path = Path(base) / name
                if not path.is_symlink():
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
            sync_dir(Path(base))
        sync_dir(backup.parent)

    def apply(self, preview, confirmation):
        action = preview.get('action')
        expected = 'UNINSTALL' if action == 'uninstall' else 'yes'
        if confirmation != expected:
            raise MaintenanceError('confirmation required')
        with self.locked():
            fresh = self.preview(action, preview.get('target'))
            if fresh['fingerprint'] != preview.get('fingerprint'):
                raise MaintenanceError('preview is stale; inspect and confirm again')
            token = uuid.uuid4().hex
            journal = {'schema_version': 1, 'operation': action, 'phase': 'prepared',
                       'token': token, 'previous': fresh['status']['current'], 'target': preview.get('target'),
                       'inventory': inventory(self.root), 'entrypoints': fresh['status']['entrypoints'],
                       'receipt_sha256': fresh['receipt_sha256'], 'completed_instances': []}
            self.prepare_recovery_capsule()
            if action == 'rollback':
                with self.quiescent_instances(fresh['status']['instances']):
                    if self.declarations() != fresh['status']['instances']:
                        raise MaintenanceError('instance declarations changed; preview again')
                    self.save_journal(journal)
                    print('Switching the active manager release…', file=sys.stderr)
                    temporary = self.root / ('.maintenance-current-' + token)
                    temporary.symlink_to('releases/' + journal['target'])
                    os.replace(temporary, self.root / 'current')
                    sync_dir(self.root)
                    self.write_receipt(self.root / 'releases' / journal['target'])
                    self.clear_journal()
                return {'ok': True, 'action': action, 'current': journal['target'], 'instances_changed': False}
            backup = self.state / 'backups' / ('uninstall-' + token)
            # Block new instance mutations before taking a durable backup.
            self.save_journal(journal)
            print('Backing up declarations, profiles, and logs…', file=sys.stderr)
            with self.quiescent_instances(fresh['status']['instances']):
                if self.declarations() != fresh['status']['instances']:
                    raise MaintenanceError('instance declarations changed; recover and preview again')
                self.backup_user_data(backup)
            try:
                for name in fresh['status']['instances']:
                    print(f'Removing deployment: {name}…', file=sys.stderr)
                    self.remove_instance(name)
                    journal['completed_instances'].append(name)
                    journal['phase'] = 'removing-instances'
                    self.save_journal(journal)
                with self.quiescent_instances(fresh['status']['instances']):
                    if any(data.get('lifecycle') != {'deployment': 'absent', 'runtime': 'stopped'} for data in self.declarations().values()):
                        raise MaintenanceError('instance deployments remain; manager retained')
                    if self.remaining_units():
                        raise MaintenanceError('services still reference this installation; manager retained')
                    if inventory(self.root) != journal['inventory'] or self.entrypoints() != journal['entrypoints']:
                        raise MaintenanceError('program files changed during removal; manager retained')
                    journal['phase'] = 'ready-to-quarantine'
                    self.save_journal(journal)
                    quarantine = self.quarantine(journal)
                    print('Removing the owned distribution…', file=sys.stderr)
                    os.rename(self.root, quarantine)
                    sync_dir(self.root.parent)
                self.finish_uninstall(journal)
                return {'ok': True, 'action': action, 'backup': str(backup), 'instances': 'absent/stopped', 'completed_instances': journal['completed_instances']}
            except Exception as exc:
                # Journal and backup survive for offline recovery/retry.
                completed = ', '.join(journal['completed_instances']) or 'none'
                remaining = ', '.join(name for name in fresh['status']['instances'] if name not in journal['completed_instances']) or 'none'
                raise MaintenanceError(f'{exc}; completed removals: {completed}; remaining: {remaining}; backup: {backup}; recovery: {fresh["recovery_command"]}') from exc

    def quarantine(self, journal):
        return self.root.with_name('.workspace-mcp-manager-uninstall-' + journal['token'])

    def load_journal(self):
        value = read_json(self.journal)
        required = {'schema_version', 'operation', 'phase', 'token', 'previous', 'target', 'inventory', 'entrypoints', 'receipt_sha256', 'completed_instances'}
        if set(value) != required or value['schema_version'] != 1 or value['operation'] not in {'rollback', 'uninstall'}:
            raise MaintenanceError('malformed maintenance journal')
        if not isinstance(value['token'], str) or not re.fullmatch('[0-9a-f]{32}', value['token']):
            raise MaintenanceError('unsafe journal token')
        if not isinstance(value['previous'], str) or not RELEASE.fullmatch(value['previous']):
            raise MaintenanceError('unsafe previous release')
        if value['operation'] == 'rollback' and (not isinstance(value['target'], str) or not RELEASE.fullmatch(value['target'])):
            raise MaintenanceError('unsafe rollback target')
        if value['phase'] not in {'prepared', 'removing-instances', 'ready-to-quarantine'}:
            raise MaintenanceError('unknown maintenance phase')
        if not isinstance(value['inventory'], dict) or not isinstance(value['entrypoints'], dict):
            raise MaintenanceError('invalid journal inventory')
        if value['receipt_sha256'] is not None and (not isinstance(value['receipt_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['receipt_sha256'])):
            raise MaintenanceError('invalid receipt digest')
        if not isinstance(value['completed_instances'], list) or any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name) for name in value['completed_instances']) or len(value['completed_instances']) != len(set(value['completed_instances'])):
            raise MaintenanceError('invalid completed instance list')
        for name in value['inventory']:
            if not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise MaintenanceError('unsafe journal path')
        for name, target in value['entrypoints'].items():
            if name not in (*COMMANDS, SETUP) or not isinstance(target, str):
                raise MaintenanceError('unsafe journal entry point')
            if name != SETUP and target != '../lib/workspace-mcp-manager/current/bin/' + name:
                raise MaintenanceError('unsafe journal entry target')
            if name == SETUP and not re.fullmatch(r'\.\./lib/workspace-mcp-manager/releases/dist-[0-9a-f]{40}/bin/workspace-mcp-manager-setup', target):
                raise MaintenanceError('unsafe setup journal target')
        return value

    def finish_uninstall(self, journal):
        quarantine = self.quarantine(journal)
        if self.root.exists() or self.root.is_symlink():
            raise MaintenanceError('new distribution exists; cannot finish old uninstall')
        # A partially removed quarantine is permitted, but every remaining byte
        # must still match the inventory captured before the commit point.
        if quarantine.exists() or quarantine.is_symlink():
            remaining = inventory(quarantine)
            if any(journal['inventory'].get(name) != value for name, value in remaining.items()):
                raise MaintenanceError('quarantine changed; refusing cleanup')
        receipt = self.state / 'distribution.json'
        if receipt.is_symlink():
            raise MaintenanceError('foreign receipt blocks uninstall')
        if receipt.exists() and hashlib.sha256(receipt.read_bytes()).hexdigest() != journal['receipt_sha256']:
            raise MaintenanceError('receipt changed during uninstall')
        for name, target in journal['entrypoints'].items():
            path = self.prefix / 'bin' / name
            if path.exists() or path.is_symlink():
                if not path.is_symlink() or os.readlink(path) != target:
                    raise MaintenanceError(f'entry point changed during uninstall: {name}')
        for name in journal['entrypoints']:
            (self.prefix / 'bin' / name).unlink(missing_ok=True)
        if (self.prefix / 'bin').is_dir():
            sync_dir(self.prefix / 'bin')
        receipt = self.state / 'distribution.json'
        if receipt.is_symlink():
            raise MaintenanceError('foreign receipt blocks uninstall')
        receipt.unlink(missing_ok=True)
        if quarantine.exists():
            shutil.rmtree(quarantine)
            sync_dir(self.root.parent)
        self.clear_journal()

    def recover(self):
        with self.locked():
            if not self.journal.exists() and not self.journal.is_symlink():
                if list((self.state / 'imports').glob('*.json')):
                    paths = self.manager_paths()
                    from workspace_mcp_manager.registry import InstanceRegistry
                    from workspace_mcp_manager.legacy_import import LegacyImportService
                    return LegacyImportService(paths, InstanceRegistry(paths.registry_dir), lock_held=True).recover()
                return {'ok': True, 'action': 'recover', 'result': 'no-maintenance-transaction'}
            value = self.load_journal()
            if value['operation'] == 'uninstall':
                if self.quarantine(value).is_symlink():
                    raise MaintenanceError('foreign uninstall quarantine')
                if (self.quarantine(value).exists() or not self.root.exists()) and value['phase'] != 'ready-to-quarantine':
                    raise MaintenanceError('uninstall commit authority is ambiguous')
                if self.quarantine(value).exists():
                    self.finish_uninstall(value)
                    return {'ok': True, 'action': 'recover', 'result': 'uninstall-completed'}
                if not self.root.exists():
                    # Cleanup may have finished immediately before interruption.
                    self.finish_uninstall(value)
                    return {'ok': True, 'action': 'recover', 'result': 'uninstall-completed'}
                if inventory(self.root) != value['inventory']:
                    raise MaintenanceError('uncommitted distribution changed during uninstall')
                self.entrypoints()
                remaining = [name for name in self.declarations() if name not in value['completed_instances']]
                self.clear_journal()
                return {'ok': True, 'action': 'recover', 'result': 'manager-retained', 'remaining_instances': remaining, 'completed_instances': value['completed_instances'],
                        'backup': str(self.state / 'backups' / ('uninstall-' + value['token']))}
            current = self.root / 'current'
            if not current.is_symlink():
                raise MaintenanceError('ambiguous rollback authority')
            actual = current.readlink().name
            if os.readlink(current) not in {'releases/' + value['previous'], 'releases/' + value['target']}:
                raise MaintenanceError('ambiguous rollback authority')
            expected = dict(value['inventory'])
            expected['current'] = {**expected['current'], 'target': 'releases/' + actual}
            actual_inventory = inventory(self.root)
            actual_inventory.pop('.maintenance-current-' + value['token'], None)
            if actual_inventory != expected:
                raise MaintenanceError('distribution changed during rollback recovery')
            validate_release(self.root / 'releases' / actual)
            self.entrypoints()
            self.write_receipt(self.root / 'releases' / actual)
            (self.root / ('.maintenance-current-' + value['token'])).unlink(missing_ok=True)
            self.clear_journal()
            return {'ok': True, 'action': 'recover', 'result': 'rollback-converged', 'current': actual}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--account-home', required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--lock-held', action='store_true')
    parser.add_argument('--action', choices=['status', 'preview', 'apply', 'recover'], required=True)
    parser.add_argument('--operation', choices=['rollback', 'uninstall'])
    parser.add_argument('--target')
    parser.add_argument('--confirmation')
    args = parser.parse_args()
    try:
        service = Maintenance(args.account_home, args.prefix, lock_held=args.lock_held)
        if args.action == 'status':
            result = service.status()
        elif args.action == 'preview':
            result = service.preview(args.operation, args.target)
        elif args.action == 'apply':
            result = service.apply(json.load(sys.stdin), args.confirmation)
        else:
            result = service.recover()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # The installer adapter always returns a structured failure, including
        # domain validation and host lifecycle errors from imported workers.
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
