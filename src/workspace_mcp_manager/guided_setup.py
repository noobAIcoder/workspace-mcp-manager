"""Private setup handoff and conservative deployment verification."""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Mapping


class GuidedResult:
    def __init__(self, path: Path):
        self.path = path
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('guided result must be a private, owned mode-0600 regular file')
        self.identity = (info.st_dev, info.st_ino)

    def write(self, outcome: str, instance_id: str | None) -> None:
        if outcome not in {'verified', 'incomplete', 'cancelled'}:
            raise ValueError('invalid guided outcome')
        fd = os.open(self.path, os.O_WRONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'w') as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != self.identity or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError('guided result changed')
            stream.truncate(0)
            json.dump({'schema_version': 1, 'outcome': outcome, 'instance_id': instance_id}, stream)
            stream.flush()
            os.fsync(stream.fileno())


def deployment_verified(status: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> bool:
    if status.get('ok') is not True or diagnostics.get('ok') is not True:
        return False
    lifecycle = status.get('lifecycle', {})
    if not isinstance(lifecycle, Mapping) or lifecycle.get('deployment') != 'present' or lifecycle.get('runtime') != 'running':
        return False
    snapshot = diagnostics.get('snapshot', {})
    try:
        fingerprint = status.get('desired_fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint or snapshot.get('desired_fingerprint') != fingerprint:
            return False
        if status.get('applied', {}).get('desired_fingerprint') != fingerprint:
            return False
        for name in ('mcp', 'tunnel'):
            service = snapshot['services'][name]
            if service['ActiveState'] != 'active' or service['SubState'] != 'running':
                return False
        probes = snapshot['probes']
        for group, names in (('mcp', ('discovery', 'initialize')), ('tunnel', ('health', 'readiness'))):
            for name in names:
                probe = probes[group][name]
                code = probe.get('status')
                if type(code) is not int or not 200 <= code < 300 or probe.get('error'):
                    return False
                if group == 'mcp' and not probe.get('protocol_version'):
                    return False
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def verify_deployment(client: Any, instance_id: str, *, timeout: float = 120,
                      monotonic=time.monotonic, sleep=time.sleep, cancelled=lambda: False) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if cancelled():
            return False
        try:
            status = client.invoke('instance', 'status', instance_id, timeout=max(0.1, min(15, deadline-monotonic()))).payload
            if monotonic() >= deadline or cancelled():
                return False
            diagnostics = client.invoke('instance', 'diagnose', instance_id, timeout=max(0.1, min(15, deadline-monotonic()))).payload
            if deployment_verified(status, diagnostics):
                return True
        except Exception:
            # Failed, unreadable, and timed-out probes are all incomplete.
            pass
        sleep(min(2, max(0, deadline-monotonic())))
    return False
