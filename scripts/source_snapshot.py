"""Capture distribution inputs without writing to the source repository."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

source, target = map(Path, sys.argv[1:3])
env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_OPTIONAL_LOCKS='0')
def git(root, *args, data=None):
    return subprocess.check_output(['git', '-C', str(root), *args], input=data, env=env)
base = git(source, 'rev-parse', 'HEAD').decode().strip()
fixed = ['bootstrap.sh', 'pyproject.toml', 'requirements-tui.lock', 'requirements-coding-tools.lock',
         'install-versions.lock', 'install-versions.aarch64.lock']
fixed += ['scripts/' + name for name in ('install.sh', 'upgrade.sh', 'bootstrap-python.sh', 'lock_validate.py', 'source_snapshot.py')]
def capture():
    names = set(fixed)
    listed = git(source, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'src/workspace_mcp_manager')
    names.update(n.decode() for n in listed.split(b'\0') if n and n.endswith(b'.py'))
    result = {}
    for name in sorted(names):
        path = source / name
        if any(p.is_symlink() for p in [path, *path.parents] if p != source.parent):
            raise SystemExit('Symlinked snapshot input: ' + name)
        if not path.exists():
            if name in fixed: raise SystemExit('Missing snapshot input: ' + name)
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode): raise SystemExit('Nonregular snapshot input: ' + name)
        result[name] = (0o755 if info.st_mode & 0o111 else 0o644, path.read_bytes())
    return result
inputs = capture()
if inputs != capture() or base != git(source, 'rev-parse', 'HEAD').decode().strip():
    raise SystemExit('Source changed during capture; retry setup')
digest = hashlib.sha256()
for name, (mode, data) in inputs.items():
    digest.update(json.dumps([name, mode, hashlib.sha256(data).hexdigest()], separators=(',', ':')).encode() + b'\n')
inputs['source-provenance.json'] = (0o644, (json.dumps({'schema_version': 1, 'kind': 'local_snapshot', 'original_head': base, 'input_sha256': digest.hexdigest()}, sort_keys=True) + '\n').encode())
target.mkdir(parents=True, exist_ok=True)
git(target, 'init', '-q', '--template=')
entries = []
for name, (mode, data) in inputs.items():
    path = target / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    blob = git(target, 'hash-object', '-w', '--stdin', data=data).decode().strip()
    entries.append(('%o %s\t%s\0' % (0o100000 | mode, blob, name)).encode())
git(target, 'update-index', '-z', '--index-info', data=b''.join(entries))
tree = git(target, 'write-tree').decode().strip()
env.update(GIT_AUTHOR_NAME='Local Snapshot', GIT_AUTHOR_EMAIL='snapshot@invalid', GIT_COMMITTER_NAME='Local Snapshot', GIT_COMMITTER_EMAIL='snapshot@invalid', GIT_AUTHOR_DATE='2000-01-01T00:00:00+0000', GIT_COMMITTER_DATE='2000-01-01T00:00:00+0000')
commit = git(target, '-c', 'commit.gpgSign=false', 'commit-tree', tree, data=b'Workspace MCP local snapshot\n').decode().strip()
git(target, 'update-ref', 'refs/heads/local-snapshot', commit)
git(target, 'symbolic-ref', 'HEAD', 'refs/heads/local-snapshot')
git(target, 'config', 'remote.origin.url', source.resolve().as_uri())
print(commit)
