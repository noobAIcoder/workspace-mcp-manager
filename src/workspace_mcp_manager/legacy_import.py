"""Reviewed, journaled transfer of predecessor instances to manager ownership."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat

from .cleanup import LegacyCleanupService, CONFIG_ASSIGNMENT_RE, SYSTEMD_ENV_RE
from .distribution_runtime import executing_distribution_root
from .domain import DesiredInstance, InstanceId
from .errors import ErrorCode, ManagerError
from .generation import ResourceGenerator
from .host_apply import RUNTIME_STATE_FILES, _run
from .legacy_templates import LegacyTemplates
from .maintenance import atomic_json, digest, inventory, read_json, sync_dir, validate_release
from .operator_contracts import operator_template
from .redaction import redact_text
from .registry import import_coordination, import_authority


class ImportBlocked(ManagerError):
    def __init__(self, message):
        super().__init__(ErrorCode.RECONCILIATION_CONFLICT, message)


# Identity of the inspected 0.3.2 predecessor whose launch/guard templates
# and 30s/120s recovery defaults this adapter understands. Never execute it.
LEGACY_PROGRAM_SHA256 = '326f79235cd11ffec99c0356ee27c686c18464412ef613106afbdcbfd259d77b'

CONFIG_KEYS = frozenset('CONFIG_VERSION INSTANCE_ID WORKSPACE_PATH MCP_BINARY MCP_HOST MCP_PORT MCP_PERMISSION_MODE MCP_SHELL_ENV_INHERIT MCP_EXEC_PATH TUNNEL_BINARY TUNNEL_PROFILE TUNNEL_ID TUNNEL_ENV_FILE TUNNEL_HEALTH_HOST TUNNEL_HEALTH_PORT'.split())
TOOLKIT_FILES = frozenset('CHATGPT.md MANIFEST.txt README.md VERSION workspace-mcp workspace-mcp-cleanup workspace-mcp-reboot workspace-mcp-setup workspace-mcp.conf.example'.split())


def exists(path):
    return path.exists() or path.is_symlink()


def safe_location(path, home):
    """Mutation paths must stay inside the account without symlinked ancestors."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ImportBlocked('noncanonical resource path')
    try:
        path.relative_to(home)
    except ValueError as exc:
        raise ImportBlocked('resource is outside the account home') from exc
    for parent in path.parents:
        if parent == home.parent:
            break
        if parent.is_symlink():
            raise ImportBlocked('symlinked resource parent: ' + str(parent))
    return path


def identity(path):
    if not exists(path):
        return None
    metadata = path.lstat()
    if metadata.st_uid != os.getuid():
        raise ImportBlocked('resource is owned by another account: ' + str(path))
    mode = stat.S_IMODE(metadata.st_mode)
    if path.is_symlink():
        return {'type': 'symlink', 'mode': mode, 'target': os.readlink(path)}
    if path.is_file():
        return {'type': 'file', 'mode': mode, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    if path.is_dir():
        return {'type': 'directory', 'mode': mode, 'inventory': inventory(path)}
    raise ImportBlocked('unsupported filesystem resource: ' + str(path))


def regular_text(path):
    value = identity(path)
    if not value or value['type'] != 'file':
        raise ImportBlocked('expected regular file: ' + str(path))
    return path.read_text(encoding='utf-8')


def references_toolkit(text, toolkit):
    return re.search(re.escape(str(toolkit)) + r'(?=/|[\s"\';}]|$)', text) is not None


def template_lines(text, *, profile=False):
    lines = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not profile:
            lines.append(line.strip())
            continue
        key, separator, scalar = line.partition(': ')
        if separator:
            scalar = scalar.strip()
            if scalar.startswith('"'):
                scalar = json.loads(scalar)
            elif scalar.startswith("'") and scalar.endswith("'"):
                scalar = scalar[1:-1].replace("''", "'")
            elif scalar in {'true','false'}:
                scalar = scalar == 'true'
            elif re.fullmatch(r'-?[0-9]+', scalar):
                scalar = int(scalar)
            elif any(c in scalar for c in ('#', '&', '*', '!', '{', '}')):
                raise ImportBlocked('unsupported tunnel profile scalar')
            lines.append((key, type(scalar).__name__, scalar))
        else:
            lines.append(line.rstrip())
    return lines


class ImportHost:
    def states_for(self, units):
        result = {}
        for unit in units:
            completed = _run(['systemctl', '--user', 'show', unit, '--property=LoadState,ActiveState,UnitFileState,FragmentPath,DropInPaths'], timeout=10, check=False)
            values = dict(line.split('=', 1) for line in completed.stdout.splitlines() if '=' in line)
            if values.get('LoadState') not in {'loaded', 'not-found'}:
                raise ImportBlocked('unsupported unit load state: ' + unit)
            if values.get('ActiveState') not in {'active', 'inactive', 'failed'}:
                raise ImportBlocked('unit is transitioning: ' + unit)
            enabled = values.get('UnitFileState', '')
            if enabled not in {'enabled', 'disabled', 'static', ''}:
                raise ImportBlocked('unsupported unit enablement: ' + unit)
            result[unit] = {'enabled': enabled == 'enabled', 'active': values.get('ActiveState') == 'active',
                            'fragment': values.get('FragmentPath', ''), 'dropins': values.get('DropInPaths', ''), 'failed': values.get('ActiveState') == 'failed', 'loaded': values.get('LoadState') == 'loaded'}
        return result

    def action(self, action, unit=None):
        _run(['systemctl', '--user', action, *([unit] if unit else [])], timeout=30)

    def references(self, toolkit):
        result = _run(['systemctl', '--user', 'list-units', '--all', '--plain', '--no-legend', '--no-pager'], timeout=10)
        names = [line.split()[0] for line in result.stdout.splitlines() if line.split()]
        refs = []
        for unit in names:
            if not unit.endswith('.service'):
                continue
            data = _run(['systemctl', '--user', 'show', unit, '--property=ExecStart,ExecStop,ExecStartPre'], timeout=10).stdout
            if references_toolkit(data, toolkit):
                refs.append(unit)
        return refs


class LegacyImportService:
    def __init__(self, paths, registry, *, host=None, release=None, lock_held=False):
        self.paths, self.registry = paths, registry
        self.home = paths.account_home
        self.prefix = paths.manager_executable.parent.parent
        roots = list(dict.fromkeys([self.home / '.local/lib/workspace-mcp', self.prefix / 'lib/workspace-mcp']))
        present = [path for path in roots if exists(path)]
        if len(present) > 1:
            raise ImportBlocked('multiple legacy toolkit roots require review')
        self.toolkit = present[0] if present else roots[0]
        self.legacy_prefix = self.toolkit.parent.parent
        self.root = paths.state_root / 'imports'
        self.host = host or ImportHost()
        self.release = release or executing_distribution_root()
        self.lock_held = lock_held
        self.audit = LegacyCleanupService(paths, registry)
        if paths.registry_dir != self.home / '.config/workspace-mcp-manager/instances':
            raise ImportBlocked('legacy import requires the canonical account registry')
        for directory in (paths.config_root, paths.registry_dir, paths.state_root, self.root, self.root / 'backups', self.root / 'receipts'):
            safe_location(directory, self.home)
            if directory.is_symlink():
                raise ImportBlocked('unsafe import state directory')

    def journal_path(self, iid):
        return self.root / (InstanceId(iid).value + '.json')

    def receipt_path(self, iid):
        return self.root / 'receipts' / (InstanceId(iid).value + '.json')

    def pending(self):
        if self.root.is_symlink():
            raise ImportBlocked('symlinked import journal directory')
        return sorted(self.root.glob('*.json'))

    @contextmanager
    def locked(self):
        from .maintenance import Maintenance
        if (self.paths.state_root / 'distribution.lock').is_symlink():
            raise ImportBlocked('symlinked distribution coordination lock')
        with Maintenance(self.home, self.prefix, lock_held=self.lock_held).locked():
            for directory in (self.root, self.root / 'backups', self.root / 'receipts'):
                safe_location(directory, self.home)
                if directory.is_symlink():
                    raise ImportBlocked('unsafe import state path')
                directory.mkdir(exist_ok=True, mode=0o700)
                if directory.stat().st_uid != os.getuid():
                    raise ImportBlocked('foreign import state owner')
                os.chmod(directory, 0o700)
                sync_dir(directory.parent)
            for name in ('distribution-transaction.json', 'maintenance-transaction.json'):
                if exists(self.paths.state_root / name):
                    raise ImportBlocked('recover pending distribution maintenance first')
            yield

    def legacy_ids(self):
        result = []
        for iid in self.audit._discover_candidates():
            if exists(self.receipt_path(iid)):
                paths = [self.audit._config_path(iid), self.audit._config_backup_path(iid), self.audit._profile_path(iid)]
                legacy = any(exists(p) for p in paths)
                for prefix in ('coding-tools-mcp-', 'tunnel-client-'):
                    unit = self.paths.user_unit_dir / (prefix + iid + '.service')
                    if exists(unit) and ('# workspace-mcp-instance=' + iid) in regular_text(unit).splitlines():
                        legacy = True
                launcher = self.audit._launcher_path(iid)
                if exists(launcher) and ('# workspace-mcp-manager-instance=' + iid) not in regular_text(launcher).splitlines():
                    legacy = True
                if not legacy:
                    continue
            result.append(iid)
        return result

    def list(self):
        candidates = []
        for iid in self.legacy_ids():
            try:
                row = self.candidate(iid)
                candidates.append({'instance_id': iid, 'state': 'ready', 'workspace_path': row['desired']['workspace_path']})
            except Exception as exc:
                candidates.append({'instance_id': iid, 'state': 'blocked', 'reason': redact_text(str(exc))})
        imported = []
        for path in sorted((self.root / 'receipts').glob('*.json')):
            imported.append({'instance_id': path.stem, 'state': 'imported'})
        return {'ok': True, 'account_home':str(self.home), 'manager_prefix':str(self.prefix), 'candidates': candidates, 'imported': imported, 'recovery_required': bool(self.pending())}

    def parse_config(self, iid):
        path = self.audit._config_path(iid)
        safe_location(path, self.home)
        values = {}
        for line in regular_text(path).splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            match = CONFIG_ASSIGNMENT_RE.fullmatch(line.strip())
            if not match or match[1] not in CONFIG_KEYS or match[1] in values:
                raise ImportBlocked('unknown, duplicate or malformed configuration field in ' + path.name)
            values[match[1]] = match[2]
        if set(values) - {'MCP_EXEC_PATH'} != CONFIG_KEYS - {'MCP_EXEC_PATH'}:
            raise ImportBlocked('required legacy configuration fields are missing')
        if values['CONFIG_VERSION'] != '1' or values['INSTANCE_ID'] != iid:
            raise ImportBlocked('unsupported legacy configuration version or ID')
        return values

    def enablement_links(self, units):
        links = {}
        for directory in self.paths.user_unit_dir.iterdir():
            if not directory.name.endswith(('.wants', '.requires')):
                continue
            safe_location(directory / 'probe', self.home)
            if not directory.is_dir():
                raise ImportBlocked('unsafe unit enablement directory')
            for unit in units:
                path = directory / unit
                if exists(path):
                    if not path.is_symlink() or path.resolve(strict=False) != self.paths.user_unit_dir / unit:
                        raise ImportBlocked('unknown unit enablement link')
                    links[str(path)] = identity(path)
        return links

    def restore_enablement_links(self, journal):
        originals = journal.get('enablement_links', {})
        current = self.enablement_links(journal['units'])
        for name in current.keys() - originals.keys():
            path = Path(name)
            if path.parent.name not in {'default.target.wants','timers.target.wants'}:
                raise ImportBlocked('unexpected enablement link appeared during recovery')
            path.unlink(); sync_dir(path.parent)
        for name, expected in originals.items():
            path = safe_location(Path(name), self.home)
            if path.parent.parent != self.paths.user_unit_dir or not path.parent.name.endswith(('.wants','.requires')) or path.name not in journal['units']:
                raise ImportBlocked('unsafe enablement recovery path')
            target = expected.get('target', '')
            if expected.get('type') != 'symlink' or (path.parent / target).resolve(strict=False) != self.paths.user_unit_dir / path.name:
                raise ImportBlocked('unsafe enablement recovery target')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name('.import-restore-' + path.name)
            if exists(temporary):
                if not temporary.is_symlink() or os.readlink(temporary) != target:
                    raise ImportBlocked('enablement recovery temporary path changed')
            else:
                os.symlink(target, temporary)
            os.replace(temporary, path); sync_dir(path.parent)

    def validate_legacy_program(self):
        program = safe_location(self.toolkit / 'workspace-mcp', self.home)
        value = identity(program)
        if regular_text(self.toolkit / 'VERSION').strip() != '0.3.2' or not value or value.get('sha256') != LEGACY_PROGRAM_SHA256:
            raise ImportBlocked('unsupported or modified legacy 0.3.2 program; review its launch/recovery semantics first')

    def candidate(self, iid, resolutions=None):
        InstanceId(iid)
        resolution = (resolutions or {}).get(iid)
        if exists(self.receipt_path(iid)):
            receipt = read_json(self.receipt_path(iid))
            desired = self.registry.get(iid)
            if desired.fingerprint() != receipt['desired_fingerprint']:
                raise ImportBlocked('imported declaration has changed: ' + iid)
            if exists(self.audit._config_path(iid)):
                raise ImportBlocked('legacy config reappeared for an imported ID: ' + iid)
            return {'instance_id': iid, 'action': 'already-imported', 'desired': desired.to_dict(), 'receipt': digest(receipt)}
        if exists(self.registry.path_for(iid)):
            raise ImportBlocked('manager instance ID already exists: ' + iid)
        if self.release is None:
            raise ImportBlocked('install the standalone manager before importing')
        if exists(self.paths.state_root / 'applied' / (iid + '.json')):
            raise ImportBlocked('manager applied-state receipt already exists for this ID')
        self.validate_legacy_program()
        values = self.parse_config(iid)
        unresolved = []
        # The main service carries the effective PATH when MCP_EXEC_PATH was omitted.
        mcp_unit = self.paths.user_unit_dir / f'coding-tools-mcp-{iid}.service'
        if 'MCP_EXEC_PATH' not in values:
            paths = re.findall(r'^Environment="PATH=(.*)"$', regular_text(mcp_unit), re.M)
            if len(paths) != 1 or any(c in paths[0] for c in ('%', '\\')):
                raise ImportBlocked('effective legacy PATH requires explicit review')
            values['MCP_EXEC_PATH'] = paths[0]
        templates = LegacyTemplates(self.paths, values, script_path=self.toolkit / 'workspace-mcp')
        tunnel_unit_path = self.paths.user_unit_dir / f'tunnel-client-{iid}.service'
        prestart = re.findall(r'^ExecStartPre=(.*)$', regular_text(tunnel_unit_path), re.M)
        if len(prestart) == 1:
            command = shlex.split(prestart[0])
            if command and Path(command[0]).is_absolute() and Path(command[0]).name == 'curl':
                templates.curl_binary = command[0]
        resources = [self.audit._config_path(iid)]
        files = templates.files()
        units = []
        for path, expected in files.items():
            safe_location(path, self.home)
            guard = 'admission-guard' in path.name
            if not exists(path) and guard:
                continue
            actual_text = regular_text(path)
            if ('# workspace-mcp-instance=' + iid) not in actual_text.splitlines() or template_lines(actual_text, profile=path.suffix == '.yaml') != template_lines(expected, profile=path.suffix == '.yaml'):
                if path.suffix == '.yaml':
                    raise ImportBlocked('tunnel profile differs from the supported env-reference template; inline credentials and custom profile settings require review: ' + path.name)
                raise ImportBlocked('customized legacy resource needs review: ' + path.name)
            resources.append(path)
            if path.parent == self.paths.user_unit_dir:
                units.append(path.name)
        states = self.host.states_for(units)
        for unit, state in states.items():
            fragment = state.get('fragment')
            if fragment and fragment != str(self.paths.user_unit_dir / unit):
                raise ImportBlocked('loaded unit fragment differs: ' + unit)
            if unit != mcp_unit.name and exists(self.paths.user_unit_dir / (unit + '.d')):
                raise ImportBlocked('unsupported legacy unit drop-in directory: ' + unit)
            # Only the recognized MCP drop-ins below can be preserved.
            loaded = shlex.split(state.get('dropins', ''))
            expected_dropins = sorted(str(p) for p in self.audit._mcp_dropin_dir(iid).glob('*.conf')) if unit == mcp_unit.name else []
            if 'dropins' in state and sorted(loaded) != expected_dropins:
                raise ImportBlocked('unknown loaded unit drop-ins: ' + unit)
        enablement_links = self.enablement_links(units)
        mcp_state = states[mcp_unit.name]
        tunnel_state = states[f'tunnel-client-{iid}.service']
        if resolution is None and (mcp_state['active'] != tunnel_state['active'] or mcp_state['enabled'] != tunnel_state['enabled']):
            unresolved.append('mixed MCP/tunnel service intent: review lifecycle in a declaration map')
        if resolution is None and any(state.get('failed') for state in states.values()):
            unresolved.append('failed legacy service: review lifecycle before importing')
        data = operator_template(self.paths)['defaults']
        data['instance_id'], data['workspace_path'] = iid, values['WORKSPACE_PATH']
        data['lifecycle']['runtime'] = 'running' if mcp_state['active'] else 'stopped'
        data['mcp'].update(binary=str(self.release / 'runtimes/coding-tools-mcp/bin/coding-tools-mcp'), host=values['MCP_HOST'], port=int(values['MCP_PORT']), permission_mode=values['MCP_PERMISSION_MODE'], shell_env_inherit=values['MCP_SHELL_ENV_INHERIT'], exec_path=values['MCP_EXEC_PATH'])
        data['tunnel'].update(binary=str(self.release / 'runtimes/tunnel-client/tunnel-client'), id=values['TUNNEL_ID'], profile=values['TUNNEL_PROFILE'], env_file=values['TUNNEL_ENV_FILE'], health_host=values['TUNNEL_HEALTH_HOST'], health_port=int(values['TUNNEL_HEALTH_PORT']))
        dropins = self.audit._mcp_dropin_dir(iid)
        effective_env = {}
        if exists(dropins):
            audited = self.audit._audit_mcp_dropins(iid)
            if audited['ownership'] != 'legacy-owned':
                raise ImportBlocked('unknown or unsafe legacy MCP drop-ins')
            for path in sorted(dropins.iterdir()):
                for line in regular_text(path).splitlines():
                    match = SYSTEMD_ENV_RE.fullmatch(line.strip())
                    if match:
                        if any(c in match[2] for c in ('%', '\\', '"')):
                            raise ImportBlocked('escaped legacy environment needs review')
                        effective_env[match[1]] = match[2]
                    elif line.startswith(('BindPaths=', 'BindReadOnlyPaths=')):
                        mode, _, value = line.partition('=')
                        source, target, *_ = value.split(':')
                        data['access']['read_only' if mode == 'BindReadOnlyPaths' else 'read_write'].append({'alias': Path(target).name, 'path': source})
            resources.append(dropins)
        if effective_env.get('GH_CONFIG_DIR'):
            data['github'] = {'mode': 'external', 'config_dir': effective_env['GH_CONFIG_DIR'], 'binary': None}
        data['mcp']['external_roots'] = effective_env.get('CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS', '').split(os.pathsep) if effective_env.get('CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS') else []
        guard_timer = f'workspace-mcp-admission-guard-{iid}.timer'
        if guard_timer in states:
            data['recovery']['admission_guard_enabled'] = states[guard_timer]['enabled']
            if resolution is None and states[guard_timer]['active'] != (states[guard_timer]['enabled'] and mcp_state['active']):
                unresolved.append('mixed legacy guard intent: review recovery policy in a declaration map')
        for path, audit in [(self.audit._config_backup_path(iid), self.audit._audit_config_backup), (self.audit._launcher_path(iid), self.audit._audit_launcher)]:
            if exists(path):
                if audit(iid)['ownership'] != 'legacy-owned':
                    raise ImportBlocked('unrecognized legacy resource: ' + path.name)
                if path == self.audit._launcher_path(iid):
                    commands = [line.strip() for line in regular_text(path).splitlines() if line.strip() and not line.lstrip().startswith('#')]
                    expected = ['exec', 'env', 'WORKSPACE_MCP_CONFIG=' + str(self.audit._config_path(iid)), str(self.toolkit / 'workspace-mcp'), '$@']
                    if len(commands) != 1 or shlex.split(commands[0]) != expected:
                        raise ImportBlocked('custom legacy launcher needs review')
                else:
                    keys = set()
                    for line in regular_text(path).splitlines():
                        if not line.strip() or line.lstrip().startswith('#'):
                            continue
                        match = CONFIG_ASSIGNMENT_RE.fullmatch(line.strip())
                        if not match or match[1] not in CONFIG_KEYS or match[1] in keys:
                            raise ImportBlocked('unknown legacy backup configuration field')
                        keys.add(match[1])
                resources.append(path)
        if resolution is not None:
            resolved = DesiredInstance.from_dict(resolution).to_dict()
            if resolved['lifecycle']['deployment'] != 'present':
                raise ImportBlocked('import must retain a present deployment')
            # Explicit review can resolve service intent only, never discard
            # unknown launch directives or change ownership/runtime settings.
            comparable = deepcopy(resolved)
            comparable['lifecycle'] = data['lifecycle']
            comparable['recovery'] = data['recovery']
            if comparable != data:
                raise ImportBlocked('reviewed declaration may resolve lifecycle/recovery only')
            data = resolved
        desired = DesiredInstance.from_dict(data)
        access_root = Path(desired.workspace_path) / '.workspace-mcp-access'
        if (desired.access.read_only or desired.access.read_write) and exists(access_root):
            safe_location(access_root, self.home)
            aliases = {folder.alias: folder.path for folder in (*desired.access.read_only, *desired.access.read_write)}
            if access_root.is_symlink() or not access_root.is_dir():
                raise ImportBlocked('unsafe legacy access directory')
            for child in access_root.iterdir():
                if child.lstat().st_uid != os.getuid():
                    raise ImportBlocked('foreign legacy access entry owner')
                if child.name in aliases:
                    if child.is_symlink():
                        if child.resolve() != Path(aliases[child.name]).resolve():
                            raise ImportBlocked('legacy access symlink target differs')
                    elif not child.is_dir() or any(child.iterdir()):
                        raise ImportBlocked('legacy access mountpoint contains data')
                elif child.name == '.owner':
                    if regular_text(child).strip() not in {'workspace-mcp-instance=' + iid, 'workspace-mcp-manager-instance=' + iid}:
                        raise ImportBlocked('unknown legacy access ownership')
                elif child.name == '.gitignore':
                    if regular_text(child) != '*\n':
                        raise ImportBlocked('custom legacy access ignore policy')
                else:
                    raise ImportBlocked('unknown legacy access directory contents')
            resources.append(access_root)
        # Existing access directories may be reused only when already manager-owned.
        # Otherwise migration must not silently take ownership of arbitrary data.
        bundle = ResourceGenerator(self.paths).generate(desired)
        moved = {str(path) for path in resources}
        for item in bundle.resources:
            path = safe_location(Path(item.path), self.home)
            if item.content is None and not item.remove_when_absent and path.is_dir() and not path.is_symlink():
                continue
            if exists(path) and not any(str(path) == root or str(path).startswith(root + '/') for root in moved):
                raise ImportBlocked('manager target already exists and needs review: ' + str(path))
        credential_refs = [Path(desired.tunnel.env_file)]
        if desired.github.config_dir:
            credential_refs.append(Path(desired.github.config_dir))
        for path in resources:
            if any(ref.resolve().is_relative_to(path.resolve()) for ref in credential_refs):
                raise ImportBlocked('credential reference overlaps a resource that migration must replace')
        preserve_state = any(ref.resolve().is_relative_to(self.audit._state_path(iid).resolve()) for ref in credential_refs)
        if exists(self.audit._state_path(iid)) and not preserve_state:
            self.validate_legacy_state(self.audit._state_path(iid))
        identities = {str(path): identity(safe_location(path, self.home)) for path in resources}
        return {'instance_id': iid, 'action': 'import', 'desired': desired.to_dict(), 'resources': identities, 'units': states, 'enablement_links':enablement_links,
                'enabled': mcp_state['enabled'], 'unit_enablement': {unit: (data['recovery']['admission_guard_enabled'] if unit.endswith('.timer') else state['enabled']) for unit,state in states.items()}, 'runtime_upgrade': {'coding_tools_mcp': '0.5.0', 'tunnel_client': '0.0.15'},
                'preserve_legacy_state': preserve_state, 'legacy_binaries': {'mcp': values['MCP_BINARY'], 'tunnel': values['TUNNEL_BINARY']}, 'unresolved': unresolved}

    def validate_candidate(self, row):
        if row['action'] == 'already-imported':
            return
        desired = DesiredInstance.from_dict(row['desired'])
        for other in self.registry.list():
            if self._collides(desired, other):
                raise ImportBlocked('import conflicts with existing manager instance: ' + other.instance_id.value)
        release_data = validate_release(self.release)
        if release_data['components'].get('coding_tools_mcp', {}).get('version') != '0.5.0' or release_data['components'].get('tunnel_client', {}).get('version') != '0.0.15':
            raise ImportBlocked('installed release does not contain the selected migration runtimes')
        from .planning import ReconciliationPlanner
        planner = ReconciliationPlanner(self.paths, self.registry)
        problems = planner._host_preconditions(desired)
        if problems:
            raise ImportBlocked('; '.join(item.reason for item in problems))
        if not os.access(desired.tunnel.env_file, os.R_OK):
            raise ImportBlocked('tunnel credential reference is not readable')
        identity_output = _run([desired.tunnel.binary, '--version'], timeout=10).stdout.strip()
        if not identity_output.startswith('0.0.15+'):
            raise ImportBlocked('unexpected tunnel runtime identity')
        help_output = _run([desired.mcp.binary, '--help'], timeout=10).stdout
        if not all(flag in help_output for flag in ('--workspace', '--permission-mode', '--shell-env-inherit')):
            raise ImportBlocked('bundled MCP does not expose required launch capabilities')

    def preview(self, ids, *, resolutions=None):
        if self.release is None:
            raise ImportBlocked('install the standalone manager before importing or retiring the toolkit')
        if not isinstance(ids, list) or any(not isinstance(iid, str) for iid in ids):
            raise ImportBlocked('selected instances must be a list of IDs')
        if resolutions is not None and (not isinstance(resolutions, dict) or not set(resolutions).issubset(ids)):
            raise ImportBlocked('reviewed declarations must map selected instance IDs')
        if (not ids and self.legacy_ids()) or len(ids) != len(set(ids)):
            raise ImportBlocked('select at least one unique legacy instance ID')
        rows, blockers = [], []
        for iid in ids:
            try:
                row = self.candidate(iid, resolutions)
                self.validate_candidate(row)
                rows.append(row)
                blockers.extend({'instance_id': iid, 'reason': reason} for reason in row.get('unresolved', []))
            except Exception as exc:
                blockers.append({'instance_id': iid, 'reason': redact_text(str(exc))})
        existing = {item.instance_id.value: item for item in self.registry.list()}
        candidates = [DesiredInstance.from_dict(row['desired']) for row in rows if row['action'] == 'import']
        for index, desired in enumerate(candidates):
            others = list(existing.values()) + candidates[:index]
            for other in others:
                if self._collides(desired, other):
                    blockers.append({'instance_id': desired.instance_id.value, 'reason': 'workspace, tunnel or endpoint conflicts with ' + other.instance_id.value})
        value = {'schema_version': 1, 'ok': not blockers, 'selected': ids, 'instances': rows, 'blockers': blockers,
                 'account_home':str(self.home), 'release': str(self.release), 'release_identity': identity(self.release / 'install.json') if self.release else None, 'resolutions': resolutions or {}, 'interruption': 'Selected running MCP and tunnel services will restart; clients must reconnect.',
                 'retirement': self.retirement_preview()}
        value['fingerprint'] = digest(value)
        return value

    @staticmethod
    def _collides(one, two):
        if Path(one.workspace_path).resolve() == Path(two.workspace_path).resolve() or one.tunnel.id == two.tunnel.id or one.tunnel.profile == two.tunnel.profile:
            return True
        first = [(one.mcp.host, one.mcp.port), (one.tunnel.health_host, one.tunnel.health_port)]
        second = [(two.mcp.host, two.mcp.port), (two.tunnel.health_host, two.tunnel.health_port)]
        for host, port in first:
            for other_host, other_port in second:
                if port == other_port and (host == other_host or host in {'0.0.0.0','::'} or other_host in {'0.0.0.0','::'}):
                    return True
        return False

    def validate_legacy_state(self, state):
        safe_location(state, self.home)
        if state.is_symlink() or not state.is_dir():
            raise ImportBlocked('unsafe legacy state directory')
        known = {'mcp.log','tunnel.log','tunnel-supervisor.log','admission-guard.log','admission-recovery.json','reboot-checkpoint.json','codex','diagnostics'}
        if any(path.name not in known for path in state.iterdir()):
            raise ImportBlocked('unknown legacy state entries require review')
        for path in state.rglob('*'):
            if path.is_symlink() or path.lstat().st_uid != os.getuid() or not (path.is_file() or path.is_dir()):
                raise ImportBlocked('unsafe legacy state entry')

    def apply_candidate(self, desired, row):
        from .lifecycle import HostLifecycleWorker
        worker = HostLifecycleWorker(self.paths, self.registry)
        result = worker.run('apply', desired.instance_id.value)
        if not result.get('ok'):
            raise ImportBlocked('manager lifecycle apply failed')
        # The declaration schema always enables present units. Restore explicit
        # disabled legacy intent after apply; running intent remains unchanged.
        generated_units = {r.unit_name for r in ResourceGenerator(self.paths).generate(desired).resources if r.unit_name}
        for unit, state in row['units'].items():
            if unit in generated_units and not row.get('unit_enablement', {}).get(unit, state['enabled']) and unit.endswith(('.service', '.timer')):
                self.host.action('disable', unit)
        generated = [r for r in ResourceGenerator(self.paths).generate(desired).resources if r.unit_name]
        observed = self.host.states_for([r.unit_name for r in generated])
        for resource in generated:
            state = observed[resource.unit_name]
            enabled = row.get('unit_enablement', {}).get(resource.unit_name, resource.enable_when_present)
            active = resource.active_when_running and desired.lifecycle.runtime.value == 'running'
            if state['enabled'] != enabled or state['active'] != active or not state.get('loaded', True):
                raise ImportBlocked('manager service state did not match the reviewed intent: ' + resource.unit_name)
            if state.get('fragment') and state['fragment'] != resource.path:
                raise ImportBlocked('manager unit fragment ownership differs after handover')
        if desired.lifecycle.runtime.value == 'running':
            from .github_access import GithubAccessService
            url = GithubAccessService._local_mcp_url(desired)
            initialized, session = GithubAccessService._http_json(url, {'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{'protocolVersion':'2025-11-25', 'capabilities':{}, 'clientInfo':{'name':'workspace-manager-import','version':'1'}}}, timeout=10)
            if initialized.get('result', {}).get('serverInfo', {}).get('version') != '0.5.0':
                raise ImportBlocked('MCP endpoint is not the selected runtime')
            if session:
                GithubAccessService._http_json(url, {'jsonrpc':'2.0', 'method':'notifications/initialized','params':{}}, session_id=session, timeout=10)
            catalog, _ = GithubAccessService._http_json(url, {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}, session_id=session, timeout=10)
            tools = catalog.get('result', {}).get('tools')
            if not isinstance(tools, list) or not tools or any(not isinstance(t, dict) or not t.get('name') for t in tools):
                raise ImportBlocked('MCP tool discovery did not succeed')
        return result

    def _record(self, journal):
        atomic_json(self.journal_path(journal['instance_id']), journal)

    def _generated(self, desired):
        bundle = ResourceGenerator(self.paths).generate(desired)
        return [{'path': item.path, 'kind': item.kind.value, 'content': item.content, 'mode': item.mode, 'unit': item.unit_name} for item in bundle.resources if item.content is not None or item.remove_when_absent]

    def _transfer(self, row):
        iid = row['instance_id']
        desired = DesiredInstance.from_dict(row['desired'])
        backup = self.root / 'backups' / iid
        if exists(backup):
            raise ImportBlocked('previous import backup exists; inspect or recover: ' + iid)
        backup.mkdir(mode=0o700)
        sync_dir(backup.parent)
        originals = [{'path': path, 'identity': value, 'backup': str(backup / str(n))} for n,(path,value) in enumerate(row['resources'].items())]
        journal = {'schema_version':1, 'instance_id':iid, 'phase':'prepared', 'desired':desired.to_dict(), 'units':row['units'], 'enablement_links':row['enablement_links'], 'enabled':row['enabled'], 'unit_enablement':row['unit_enablement'], 'originals':originals, 'generated':self._generated(desired)}
        self._record(journal)
        try:
            for unit in [f'workspace-mcp-admission-guard-{iid}.timer', f'workspace-mcp-admission-guard-{iid}.service', f'tunnel-client-{iid}.service', f'coding-tools-mcp-{iid}.service']:
                if unit in row['units']:
                    self.host.action('stop', unit)
                    if row['units'][unit]['enabled']:
                        self.host.action('disable', unit)
            # Preserve mutable predecessor state after its writers have stopped.
            state = safe_location(self.audit._state_path(iid), self.home)
            if exists(state) and not row['preserve_legacy_state']:
                self.validate_legacy_state(state)
                entry = {'path':str(state), 'identity':identity(state), 'backup':str(backup / 'state')}
                journal['originals'].append(entry)
                self._record(journal)
            for original in journal['originals']:
                path = Path(original['path'])
                if identity(path) != original['identity']:
                    raise ImportBlocked('legacy resource changed during handover')
                os.replace(path, original['backup']); sync_dir(path.parent); sync_dir(backup)
            journal['phase'] = 'applying'; self._record(journal)
            self.host.action('daemon-reload')
            self.registry.create(desired)
            result = self.apply_candidate(desired, row)
            if not result.get('ok'):
                raise ImportBlocked('manager apply returned failure')
            receipt = {'schema_version':1, 'instance_id':iid, 'desired_fingerprint':desired.fingerprint(), 'release':str(self.release), 'backup':str(backup), 'enabled':row['enabled'], 'unit_enablement':row['unit_enablement'], 'preserved_legacy_state':row['preserve_legacy_state']}
            atomic_json(self.receipt_path(iid), receipt)  # Durable commit point.
            journal['phase'] = 'committed'; self._record(journal)
            self._finish_commit(journal, converge=False)
            return 'completed'
        except Exception:
            if exists(self.receipt_path(iid)):
                raise
            self._restore(journal)
            raise

    def _clear(self, iid):
        self.journal_path(iid).unlink(); sync_dir(self.root)

    def _finish_commit(self, journal, *, converge=True):
        desired = self.registry.get(journal['instance_id'])
        receipt = read_json(self.receipt_path(journal['instance_id']))
        if desired.fingerprint() != receipt['desired_fingerprint']:
            raise ImportBlocked('committed import declaration has changed')
        if converge:
            self.apply_candidate(desired, {'units':journal['units'], 'enabled':journal['enabled'], 'unit_enablement':journal['unit_enablement']})
        self._clear(journal['instance_id'])

    def _restore(self, journal):
        iid = journal['instance_id']
        desired = DesiredInstance.from_dict(journal['desired'])
        # Backups prove which originals were actually displaced. Never remove
        # original resources that the transaction had not reached.
        moved = {item['path'] for item in journal['originals'] if exists(Path(item['backup']))}
        generated = journal['generated']
        for item in generated:
            path = Path(item['path']); safe_location(path, self.home)
            if any(str(path) == root or str(path).startswith(root + '/') for root in journal_original_paths(journal) if root not in moved):
                continue
            if not exists(path):
                continue
            if item['content'] is not None:
                if path.is_symlink() or regular_text(path) != item['content']:
                    raise ImportBlocked('import-created resource changed; recovery retained: ' + str(path))
        declaration = self.registry.path_for(iid)
        if exists(declaration) and self.registry.get(iid).fingerprint() != desired.fingerprint():
            raise ImportBlocked('import declaration changed; recovery retained')
        units = set(journal['units']) | {item['unit'] for item in generated if item['unit']}
        observed = self.host.states_for(sorted(units))
        for unit, state in observed.items():
            if state.get('fragment') and state['fragment'] != str(self.paths.user_unit_dir / unit):
                raise ImportBlocked('unit ownership changed during import recovery')
        for unit, state in observed.items():
            if state.get('loaded', True) or state['active'] or state['enabled']:
                self.host.action('stop', unit)
                if state['enabled']:
                    self.host.action('disable', unit)
        # Remove files before directories; original resources without backups
        # remain untouched. Unexpected directory content is always a conflict.
        for item in sorted(generated, key=lambda x: len(Path(x['path']).parts), reverse=True):
            path = Path(item['path'])
            if any(str(path) == root or str(path).startswith(root + '/') for root in journal_original_paths(journal) if root not in moved):
                continue
            if not exists(path):
                continue
            if item['content'] is not None:
                path.unlink(); sync_dir(path.parent)
            elif path.is_dir() and not path.is_symlink():
                if path == self.paths.state_root / iid:
                    for child in path.iterdir():
                        if child.name not in RUNTIME_STATE_FILES or child.is_symlink() or not child.is_file():
                            raise ImportBlocked('unexpected imported state file; recovery retained')
                    for child in path.iterdir():
                        child.unlink()
                path.rmdir(); sync_dir(path.parent)
            else:
                raise ImportBlocked('unexpected import directory type')
        applied = self.paths.state_root / 'applied' / (iid + '.json')
        if exists(applied):
            if read_json(applied).get('desired_fingerprint') != desired.fingerprint():
                raise ImportBlocked('applied receipt changed; recovery retained')
            applied.unlink(); sync_dir(applied.parent)
        if exists(declaration):
            declaration.unlink(); sync_dir(declaration.parent)
        for original in journal['originals']:
            path, backup = Path(original['path']), Path(original['backup'])
            if exists(backup):
                if exists(path) or identity(backup) != original['identity']:
                    raise ImportBlocked('original/backup conflict; recovery retained')
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, path); sync_dir(path.parent); sync_dir(backup.parent)
            elif identity(path) != original['identity']:
                if journal['phase'] == 'restoring' and Path(original['backup']).name == 'state' and path.is_dir() and not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) == original['identity']['mode']:
                    # Restarted legacy processes may append logs before a crash
                    # or a readiness failure. Retain that new runtime evidence.
                    self.validate_legacy_state(path)
                else:
                    raise ImportBlocked('original resource changed; recovery retained')
        journal['phase'] = 'restoring'
        self._record(journal)
        self.host.action('daemon-reload')
        for unit, state in journal['units'].items():
            self.host.action('enable' if state['enabled'] else 'disable', unit)
        self.restore_enablement_links(journal)
        for prefix in ('coding-tools-mcp-', 'tunnel-client-', 'workspace-mcp-admission-guard-'):
            for unit, state in journal['units'].items():
                if unit.startswith(prefix) and state['active']:
                    self.host.action('start', unit)
        restored_states = self.host.states_for(list(journal['units']))
        for unit, expected in journal['units'].items():
            actual = restored_states[unit]
            if actual['enabled'] != expected['enabled'] or actual['active'] != expected['active']:
                raise ImportBlocked('legacy service states did not restore; recover again: ' + unit)
        backup = self.root / 'backups' / iid
        if backup.exists() and not any(backup.iterdir()):
            backup.rmdir(); sync_dir(backup.parent)
        self._clear(iid)

    def apply(self, reviewed, expected_fingerprint):
        if not reviewed.get('ok') or reviewed.get('fingerprint') != expected_fingerprint or digest({k:v for k,v in reviewed.items() if k != 'fingerprint'}) != expected_fingerprint:
            raise ImportBlocked('a successful reviewed preview and its fingerprint are required')
        completed, restored = [], []
        ids = reviewed.get('selected', [])
        with self.locked():
            if self.pending():
                raise ImportBlocked('recover pending imports first')
            current = self.preview(ids, resolutions=reviewed.get('resolutions', {}))
            if current != reviewed or current['fingerprint'] != expected_fingerprint:
                raise ImportBlocked('migration preview is stale; review again')
            for index, row in enumerate(current['instances']):
                iid = row['instance_id']
                try:
                    with import_coordination(self.paths.state_root, iid, exclusive=True), import_authority(self.registry, iid):
                        fresh = self.candidate(iid, reviewed.get('resolutions', {}))
                        if digest(fresh) != digest(row):
                            raise ImportBlocked('instance changed after preview')
                        if row['action'] != 'already-imported':
                            self.validate_candidate(fresh)
                            self._transfer(row)
                    completed.append(iid)
                except Exception as exc:
                    if not exists(self.journal_path(iid)) and not exists(self.receipt_path(iid)):
                        restored.append(iid)
                    return {'ok':False, 'completed':completed, 'restored':restored, 'failed':iid, 'unattempted':ids[index+1:], 'error':redact_text(str(exc)), 'recovery_required':bool(self.pending())}
            try:
                retirement = self.retire_if_ready(current['retirement'])
            except Exception as exc:
                return {'ok':False, 'completed':completed, 'restored':restored, 'failed':'toolkit-retirement', 'unattempted':[], 'error':redact_text(str(exc)), 'recovery_required':bool(self.pending())}
            return {'ok':True, 'completed':completed, 'restored':restored, 'unattempted':[], 'retirement':retirement}

    def recover(self):
        recovered = []
        with self.locked():
            for path in self.pending():
                if path.name == '_retirement.json':
                    self._recover_retirement(read_json(path)); recovered.append('toolkit-retirement'); continue
                journal = read_json(path)
                iid = InstanceId(journal['instance_id']).value
                if path != self.journal_path(iid) or journal.get('schema_version') != 1:
                    raise ImportBlocked('invalid import recovery journal')
                self._validate_journal(journal)
                with import_coordination(self.paths.state_root, iid, exclusive=True), import_authority(self.registry, iid):
                    if exists(self.receipt_path(iid)):
                        self._finish_commit(journal)
                    else:
                        self._restore(journal)
                recovered.append(iid)
        return {'ok':True, 'action':'recover', 'result':'imports-recovered', 'instances':recovered}

    def _validate_journal(self, journal):
        if journal.get('phase') not in {'prepared','applying','committed','restoring'}:
            raise ImportBlocked('unsupported import recovery phase')
        iid = journal['instance_id']
        desired = DesiredInstance.from_dict(journal['desired'])
        if desired.instance_id.value != iid or journal['generated'] != self._generated(desired):
            raise ImportBlocked('import recovery targets differ from generated declaration')
        allowed = {str(p) for p in (self.audit._config_path(iid), self.audit._config_backup_path(iid), self.audit._launcher_path(iid), self.audit._state_path(iid), self.audit._mcp_dropin_dir(iid))}
        allowed.update(str(self.paths.user_unit_dir / (prefix + iid + suffix)) for prefix,suffix in [('coding-tools-mcp-','.service'),('tunnel-client-','.service'),('workspace-mcp-admission-guard-','.service'),('workspace-mcp-admission-guard-','.timer')])
        allowed.add(str(self.home / '.config/tunnel-client' / (desired.tunnel.profile + '.yaml')))
        if desired.access.read_only or desired.access.read_write:
            allowed.add(str(Path(desired.workspace_path) / '.workspace-mcp-access'))
        for original in journal['originals']:
            safe_location(Path(original['path']), self.home)
            safe_location(Path(original['backup']), self.home)
            if original['path'] not in allowed or Path(original['backup']).parent != self.root / 'backups' / iid or not re.fullmatch(r'\d+|state', Path(original['backup']).name):
                raise ImportBlocked('unsafe import recovery path')
        expected_units = {Path(p).name for p in allowed if Path(p).parent == self.paths.user_unit_dir and not p.endswith('.d')}
        if not isinstance(journal['units'], dict) or any(not isinstance(state,dict) or type(state.get('enabled')) is not bool or type(state.get('active')) is not bool for state in journal['units'].values()):
            raise ImportBlocked('invalid import unit state records')
        if not set(journal['units']).issubset(expected_units):
            raise ImportBlocked('unsafe import unit names')

    def retirement_preview(self):
        toolkit = self.toolkit
        if not exists(toolkit):
            return {'state':'absent'}
        try:
            safe_location(toolkit, self.home)
            if toolkit.is_symlink() or not toolkit.is_dir() or set(p.name for p in toolkit.iterdir()) != TOOLKIT_FILES:
                raise ImportBlocked('unrecognized shared toolkit contents')
            if regular_text(toolkit / 'VERSION').strip() != '0.3.2':
                raise ImportBlocked('unrecognized legacy toolkit version')
            for p in toolkit.iterdir():
                regular_text(p)
            links = {}
            for name in ('workspace-mcp','workspace-mcp-setup','workspace-mcp-cleanup','workspace-mcp-reboot'):
                path = self.legacy_prefix / 'bin' / name
                if not exists(path):
                    continue
                # Manager may already own the overlapping reboot helper.
                if name == 'workspace-mcp-reboot' and path.is_symlink() and path.resolve() == self.paths.manager_executable.resolve().parent / name:
                    continue
                if not path.is_symlink() or path.resolve() != toolkit / name:
                    raise ImportBlocked('unrecognized legacy entrypoint: ' + name)
                links[str(path)] = identity(path)
            return {'state':'ready', 'toolkit':identity(toolkit), 'links':links}
        except Exception as exc:
            return {'state':'blocked', 'reason':redact_text(str(exc))}

    def retirement_blocker(self, toolkit):
        if self.legacy_ids():
            return 'legacy instances remain'
        if self.host.references(toolkit):
            return 'services still reference the legacy toolkit'
        for desired in self.registry.list():
            refs = [Path(desired.tunnel.env_file)] + ([Path(desired.github.config_dir)] if desired.github.config_dir else [])
            if any(ref.resolve().is_relative_to(toolkit.resolve()) for ref in refs):
                return 'credential references still use the legacy toolkit directory'
        for path in self.paths.user_unit_dir.rglob('*'):
            if path.name.endswith(('.service','.timer','.conf')) and path.is_file() and references_toolkit(path.read_text(errors='replace'), toolkit):
                return 'unit files still reference the legacy toolkit'
        return None

    def retire_if_ready(self, reviewed):
        if self.legacy_ids():
            return {'state':'retained', 'reason':'legacy instances remain'}
        current = self.retirement_preview()
        if current != reviewed or current['state'] != 'ready':
            return current if current['state'] in {'absent','blocked'} else {'state':'blocked','reason':'toolkit changed after preview'}
        toolkit = self.toolkit
        reason = self.retirement_blocker(toolkit)
        if reason:
            return {'state':'blocked', 'reason':reason}
        journal = {'schema_version':1, 'toolkit':str(toolkit), 'identity':current['toolkit'], 'links':current['links'], 'archive':str(self.root / 'retired-toolkit')}
        atomic_json(self.root / '_retirement.json', journal)
        self._recover_retirement(journal)
        return {'state':'retired','archive':journal['archive']}

    def _recover_retirement(self, journal):
        from .maintenance import Maintenance
        names = [desired.instance_id.value for desired in self.registry.list()]
        with Maintenance(self.home, self.prefix, lock_held=True).quiescent_instances(names):
            self._finish_retirement(journal)

    def _finish_retirement(self, journal):
        toolkit, archive = Path(journal['toolkit']), Path(journal['archive'])
        if toolkit != self.toolkit or archive != self.root / 'retired-toolkit':
            raise ImportBlocked('unsafe toolkit retirement paths')
        safe_location(toolkit, self.home); safe_location(archive, self.home)
        if exists(toolkit):
            reason = self.retirement_blocker(toolkit)
            if reason:
                raise ImportBlocked(reason)
            if identity(toolkit) != journal['identity'] or exists(archive) or self.host.references(toolkit):
                raise ImportBlocked('toolkit retirement changed; recovery retained')
            os.replace(toolkit, archive); sync_dir(toolkit.parent); sync_dir(archive.parent)
        elif identity(archive) != journal['identity']:
            raise ImportBlocked('retired toolkit archive changed')
        for value, expected in journal['links'].items():
            path = Path(value)
            if path.parent != self.legacy_prefix / 'bin' or path.name not in {'workspace-mcp','workspace-mcp-setup','workspace-mcp-cleanup','workspace-mcp-reboot'}:
                raise ImportBlocked('unsafe retirement entrypoint')
            safe_location(path, self.home)
            if expected.get('type') != 'symlink' or (path.parent / expected.get('target', '')).resolve(strict=False) != (toolkit / path.name).resolve(strict=False):
                raise ImportBlocked('unsafe retirement link target')
            if exists(path):
                if identity(path) != expected:
                    raise ImportBlocked('legacy command changed during retirement')
                path.unlink(); sync_dir(path.parent)
        atomic_json(self.root / 'retirement-receipt.json.saved', {'schema_version':1, 'archive':str(archive)})
        (self.root / '_retirement.json').unlink(); sync_dir(self.root)


def journal_original_paths(journal):
    return [item['path'] for item in journal['originals']]
