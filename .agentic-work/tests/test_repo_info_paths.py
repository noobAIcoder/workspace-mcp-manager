"""Repository-path validation through document and CLI entry points."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_repo_info as base


class RepoInfoPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = base.rv.load_catalog(base.ROOT)

    def targets(self):
        for name in self.catalog:
            yield name, ('authority_refs', 0, 'source')
        yield 'runtime', ('commands', 0, 'cwd')
        yield 'runtime', ('env', 'files', 0, 'path')
        yield 'structure', ('root', 'workspace_files', 0)
        yield 'structure', ('paths', 0, 'path')
        yield 'structure', ('entrypoints', 0, 'path')
        yield 'validation', ('commands', 0, 'cwd')

    def with_path(self, name, field, value):
        doc = copy.deepcopy(self.catalog[name][1])
        doc['authority_refs'][0]['kind'] = 'repository'
        if name == 'structure':
            doc['root']['workspace_files'] = ['workspace.yaml']
        row = doc
        for part in field[:-1]:
            row = row[part]
        row[field[-1]] = value
        return doc

    def test_repository_path_fields_reject_absolute_and_escaping_values(self):
        invalid = ['../outside', 'src/../../outside', '/outside', '//server/share',
                   r'C:\outside', 'C:/outside', 'C:outside', r'\outside',
                   r'\\server\share', r'..\outside', r'src\..\..\outside',
                   'https://example.test/policy', 'file:///outside', '', '   ', 'bad\0path']
        for name, field in self.targets():
            for value in invalid:
                with self.subTest(name=name, field=field, value=value):
                    issues = base.rv.validate_document(self.with_path(name, field, value), name, self.catalog)
                    self.assertEqual(['REPOSITORY_PATH'], [i.code for i in issues])
                    self.assertIn(field[-1] if isinstance(field[-1], str) else 'workspace_files', issues[0].path)

    def test_repository_path_fields_accept_local_paths_and_unknowns(self):
        for name, field in self.targets():
            for value in ['.', './src', 'src/app.py', 'src/../README.md', r'src\app.py',
                          'docs/policy with spaces.md', None, '<repository-path>']:
                if value is None and field == ('root', 'workspace_files', 0):
                    continue  # This schema's string-list rows are not nullable.
                with self.subTest(name=name, field=field, value=value):
                    self.assertEqual([], base.rv.validate_document(self.with_path(name, field, value), name, self.catalog))

    def test_external_sources_and_descriptive_records_are_not_local_paths(self):
        for name in self.catalog:
            doc = copy.deepcopy(self.catalog[name][1])
            doc['authority_refs'][0].update(kind='external', source='https://example.test/policy')
            doc['provenance']['evidence_source'] = 'Inspection from /external/workspace'
            if name == 'runtime':
                doc['commands'][0]['run'] = 'DO_NOT_EXECUTE /external/command'
                doc['deployment']['public_entrypoint'] = 'https://example.test/app'
                doc['surfaces'][0]['entrypoint'] = 'module:application'
            self.assertEqual([], base.rv.validate_document(doc, name, self.catalog))

    def test_installed_cli_reports_path_failure_without_modifying_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = copy.deepcopy(self.catalog['repo_info'][1])
            runtime = self.with_path('runtime', ('commands', 0, 'cwd'), '../outside')
            runtime['commands'][0]['run'] = 'THIS_COMMAND_MUST_NOT_EXECUTE'
            (root / 'REPO_INFO.yaml').write_text(json.dumps(index), encoding='utf-8')
            (root / 'runtime.yaml').write_text(json.dumps(runtime), encoding='utf-8')
            # Unselected optional context remains isolated, even if malformed.
            (root / 'hazards.yaml').write_text('not: [parseable', encoding='utf-8')
            def hashes():
                return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
            before = hashes()
            args = [sys.executable, str(base.ROOT / 'tools/repo_info_validate.py'),
                    'installed', '--facts-root', str(root)]
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
            independent = subprocess.run([*args, '--json'], cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(0, independent.returncode, independent.stderr)
            self.assertTrue(json.loads(independent.stdout)['valid'])
            text_run = subprocess.run([*args, '--shard', 'runtime'], cwd=root, env=env, text=True, capture_output=True)
            json_run = subprocess.run([*args, '--shard', 'runtime', '--json'], cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(1, text_run.returncode, text_run.stderr)
            self.assertEqual(1, json_run.returncode, json_run.stderr)
            payload = json.loads(json_run.stdout)
            self.assertFalse(payload['valid'])
            self.assertFalse(payload['facts_verified'])
            self.assertEqual(['REPOSITORY_PATH'], [i['code'] for i in payload['issues']])
            self.assertIn('REPOSITORY_PATH', text_run.stdout)
            self.assertEqual(before, hashes())

    def test_relocated_template_cli_requires_no_facts_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'arbitrary component name'
            shutil.copytree(base.ROOT / '.templates/repo-info', root / '.templates/repo-info')
            shutil.copytree(base.ROOT / 'tools', root / 'tools')
            def hashes():
                return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in root.rglob('*') if p.is_file()}
            before = hashes()
            run = subprocess.run([sys.executable, str(root / 'tools/repo_info_validate.py'), 'templates', '--json'],
                                 cwd=tmp, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, text=True, capture_output=True)
            self.assertEqual(0, run.returncode, run.stderr)
            self.assertTrue(json.loads(run.stdout)['valid'])
            self.assertEqual(before, hashes())


if __name__ == '__main__':
    unittest.main()
