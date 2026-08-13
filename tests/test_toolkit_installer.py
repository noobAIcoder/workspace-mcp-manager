from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "scripts" / "install_toolkit.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("workspace_mcp_manager_installer", INSTALLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_candidate(destination: Path) -> Path:
    destination.mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copytree(ROOT / "src", destination / "src")
    return destination


class ToolkitInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.installer = load_installer()

    def test_source_fingerprint_is_content_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = copy_candidate(Path(directory) / "repo")
            first = self.installer.source_fingerprint(repo)
            target = repo / "src" / "workspace_mcp_manager" / "cli.py"
            os.utime(target, (target.stat().st_atime + 100, target.stat().st_mtime + 100))
            second = self.installer.source_fingerprint(repo)
            self.assertEqual(first, second)
            target.write_text(target.read_text(encoding="utf-8") + "\n# fingerprint-change\n", encoding="utf-8")
            self.assertNotEqual(first, self.installer.source_fingerprint(repo))

    def test_initial_install_creates_atomic_release_and_valid_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            output = io.StringIO()
            with redirect_stdout(output):
                rc = self.installer.main(
                    ["--repo", str(repo), "--account-home", str(home), "--prefix", str(prefix)]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(output.getvalue())
            fingerprint = payload["candidate_fingerprint"]
            release = prefix / "lib" / "workspace-mcp-manager" / "releases" / fingerprint
            self.assertTrue(release.is_dir())
            self.assertEqual(os.readlink(prefix / "lib/workspace-mcp-manager/current"), f"releases/{fingerprint}")
            self.assertEqual(
                os.readlink(prefix / "bin/workspace-mcp-manager"),
                "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager",
            )
            completed = subprocess.run(
                [str(prefix / "bin/workspace-mcp-manager"), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            wrapper = (release / "bin/workspace-mcp-manager").read_text(encoding="utf-8")
            self.assertIn(str(release / "src"), wrapper)
            self.assertNotIn(".stage-", wrapper)

    def test_same_release_reinstall_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            self.assertEqual(
                self.installer.main(["--repo", str(repo), "--account-home", str(home), "--prefix", str(prefix)]),
                0,
            )
            before = self.installer.installed_fingerprint(prefix / "lib/workspace-mcp-manager")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    self.installer.main(["--repo", str(repo), "--account-home", str(home), "--prefix", str(prefix)]),
                    0,
                )
            self.assertEqual(before, self.installer.installed_fingerprint(prefix / "lib/workspace-mcp-manager"))

    def test_different_release_requires_upgrade_and_invalid_config_blocks_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo1 = copy_candidate(root / "repo1")
            repo2 = copy_candidate(root / "repo2")
            home = root / "home"
            prefix = home / ".local"
            self.assertEqual(
                self.installer.main(["--repo", str(repo1), "--account-home", str(home), "--prefix", str(prefix)]),
                0,
            )
            first = self.installer.installed_fingerprint(prefix / "lib/workspace-mcp-manager")
            changed = repo2 / "src/workspace_mcp_manager/cli.py"
            changed.write_text(changed.read_text(encoding="utf-8") + "\n# candidate-two\n", encoding="utf-8")
            second = self.installer.source_fingerprint(repo2)
            self.assertNotEqual(first, second)
            with self.assertRaises(SystemExit):
                self.installer.main(["--repo", str(repo2), "--account-home", str(home), "--prefix", str(prefix)])
            self.assertEqual(first, self.installer.installed_fingerprint(prefix / "lib/workspace-mcp-manager"))

            registry = home / ".config/workspace-mcp-manager/instances"
            registry.mkdir(parents=True)
            (registry / "broken.json").write_text('{"not":"a declaration"}\n', encoding="utf-8")
            with self.assertRaises(SystemExit):
                self.installer.main(
                    ["--repo", str(repo2), "--account-home", str(home), "--prefix", str(prefix), "--upgrade"]
                )
            self.assertEqual(first, self.installer.installed_fingerprint(prefix / "lib/workspace-mcp-manager"))

            shutil.rmtree(registry)
            self.assertEqual(
                self.installer.main(
                    ["--repo", str(repo2), "--account-home", str(home), "--prefix", str(prefix), "--upgrade"]
                ),
                0,
            )
            self.assertEqual(second, self.installer.installed_fingerprint(prefix / "lib/workspace-mcp-manager"))

    def test_check_mode_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    self.installer.main(
                        ["--repo", str(repo), "--account-home", str(home), "--prefix", str(prefix), "--check"]
                    ),
                    0,
                )
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["installed"])
            self.assertFalse((prefix / "lib/workspace-mcp-manager").exists())


if __name__ == "__main__":
    unittest.main()
