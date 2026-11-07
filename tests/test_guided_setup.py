from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path


class GuidedResultTests(unittest.TestCase):
    def test_verification_timeout_is_incomplete(self):
        from workspace_mcp_manager.guided_setup import verify_deployment
        class Clock:
            now = 0.0
            def monotonic(self): return self.now
            def sleep(self, value): self.now += value
        class Client:
            def invoke(self, *args, **kwargs): return SimpleNamespace(payload={'ok': False})
        clock = Clock()
        self.assertFalse(verify_deployment(Client(), 'one', timeout=5,
                                          monotonic=clock.monotonic, sleep=clock.sleep))
        self.assertEqual(clock.now, 5)

    def test_result_rejects_symlink_and_replacement(self):
        from workspace_mcp_manager.guided_setup import GuidedResult
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result'
            path.touch(mode=0o600)
            link = path.with_name('link')
            link.symlink_to(path)
            with self.assertRaises(ValueError): GuidedResult(link)
            result = GuidedResult(path)
            replacement = path.with_name('replacement')
            replacement.touch(mode=0o600)
            replacement.replace(path)
            with self.assertRaises(ValueError): result.write('verified', 'one')

    def test_result_requires_private_owned_regular_file(self):
        from workspace_mcp_manager.guided_setup import GuidedResult
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result'
            path.touch(mode=0o600)
            result = GuidedResult(path)
            result.write('incomplete', 'one')
            self.assertEqual(json.loads(path.read_text()), {'schema_version': 1, 'outcome': 'incomplete', 'instance_id': 'one'})
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                GuidedResult(path)

    def test_unknown_and_failed_checks_never_verify(self):
        from workspace_mcp_manager.guided_setup import deployment_verified
        self.assertFalse(deployment_verified({}, {}))
        snapshot = {'services': {k: {'ActiveState': 'active', 'SubState': 'running'} for k in ('mcp', 'tunnel')},
                    'probes': {'mcp': {k: {'status': 200, 'protocol_version': '2025-11-25'} for k in ('discovery', 'initialize')},
                               'tunnel': {k: {'status': 200} for k in ('health', 'readiness')}}}
        snapshot['desired_fingerprint'] = 'abc'
        status = {'ok': True, 'lifecycle': {'deployment': 'present', 'runtime': 'running'},
                  'desired_fingerprint': 'abc', 'applied': {'desired_fingerprint': 'abc'}}
        self.assertTrue(deployment_verified(status, {'ok': True, 'snapshot': snapshot}))
        status['applied']['desired_fingerprint'] = 'stale'
        self.assertFalse(deployment_verified(status, {'ok': True, 'snapshot': snapshot}))
        status['applied']['desired_fingerprint'] = 'abc'
        status['lifecycle']['deployment'] = 'absent'
        self.assertFalse(deployment_verified(status, {'ok': True, 'snapshot': snapshot}))
        status['lifecycle']['deployment'] = 'present'
        snapshot['probes']['tunnel']['readiness']['status'] = None
        self.assertFalse(deployment_verified(status, {'ok': True, 'snapshot': snapshot}))

