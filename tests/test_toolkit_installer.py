from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.sh"
COMPAT_INSTALLER = ROOT / "scripts" / "install_toolkit.sh"
LEGACY_PYTHON_INSTALLER = ROOT / "scripts" / "install_toolkit.py"


def copy_candidate(destination: Path) -> Path:
    destination.mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copy2(ROOT / "requirements-tui.lock", destination / "requirements-tui.lock")
    (destination / "scripts").mkdir()
    shutil.copy2(INSTALLER, destination / "scripts" / "install.sh")
    shutil.copy2(COMPAT_INSTALLER, destination / "scripts" / "install_toolkit.sh")
    shutil.copytree(ROOT / "src", destination / "src")
    return destination


def make_python_shim(root: Path) -> tuple[Path, Path]:
    bindir = root / "fake-bin"
    bindir.mkdir(parents=True)
    log = root / "python-shim.log"
    shim = bindir / "python3"
    real_python = Path(sys.executable).resolve()
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"REAL_PYTHON={str(real_python)!r}\n"
        "if [[ -n \"${FAKE_PY_LOG:-}\" ]]; then printf '%q ' \"$@\" >>\"$FAKE_PY_LOG\"; printf '\\n' >>\"$FAKE_PY_LOG\"; fi\n"
        "if [[ \" $* \" == *\"pip._internal.cli.main\"* ]]; then\n"
        "  target=''\n"
        "  args=(\"$@\")\n"
        "  for ((i=0; i<${#args[@]}; i++)); do\n"
        "    if [[ \"${args[$i]}\" == '--target' ]]; then target=${args[$((i+1))]}; break; fi\n"
        "  done\n"
        "  [[ -n \"$target\" ]] || { echo 'fake pip missing --target' >&2; exit 91; }\n"
        "  mkdir -p \"$target/textual\"\n"
        "  printf \"__version__ = '8.2.8'\\n\" >\"$target/textual/__init__.py\"\n"
        "  exit 0\n"
        "fi\n"
        "exec \"$REAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bindir, log


def installer_env(root: Path) -> dict[str, str]:
    bindir, log = make_python_shim(root)
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["FAKE_PY_LOG"] = str(log)
    return env


def run_installer(
    repo: Path,
    home: Path,
    prefix: Path,
    env: dict[str, str],
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(repo / "scripts" / "install.sh"),
            "--repo",
            str(repo),
            "--account-home",
            str(home),
            "--prefix",
            str(prefix),
            *extra,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )


class ToolkitInstallerTests(unittest.TestCase):
    def test_shell_is_the_only_canonical_installer(self) -> None:
        self.assertTrue(INSTALLER.is_file())
        self.assertTrue(COMPAT_INSTALLER.is_file())
        self.assertFalse(LEGACY_PYTHON_INSTALLER.exists())
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("stage_release", text)
        self.assertIn("atomic_symlink", text)
        compat = COMPAT_INSTALLER.read_text(encoding="utf-8")
        self.assertIn('exec "$SCRIPT_DIR/install.sh" "$@"', compat)
        self.assertNotIn("stage_release", compat)

    def test_source_fingerprint_is_content_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            env = installer_env(root)
            first = run_installer(repo, home, prefix, env, "--check")
            self.assertEqual(first.returncode, 0, first.stderr)
            first_fp = json.loads(first.stdout)["candidate_fingerprint"]

            target = repo / "src/workspace_mcp_manager/cli.py"
            os.utime(target, (target.stat().st_atime + 100, target.stat().st_mtime + 100))
            second = run_installer(repo, home, prefix, env, "--check")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first_fp, json.loads(second.stdout)["candidate_fingerprint"])

            target.write_text(target.read_text(encoding="utf-8") + "\n# fingerprint-change\n", encoding="utf-8")
            third = run_installer(repo, home, prefix, env, "--check")
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertNotEqual(first_fp, json.loads(third.stdout)["candidate_fingerprint"])

    def test_initial_install_creates_atomic_release_and_valid_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            env = installer_env(root)
            completed = run_installer(repo, home, prefix, env)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            fingerprint = payload["candidate_fingerprint"]
            release = prefix / "lib/workspace-mcp-manager/releases" / fingerprint
            self.assertTrue(release.is_dir())
            self.assertEqual(os.readlink(prefix / "lib/workspace-mcp-manager/current"), f"releases/{fingerprint}")
            self.assertEqual(
                os.readlink(prefix / "bin/workspace-mcp-manager"),
                "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager",
            )
            self.assertEqual(
                os.readlink(prefix / "bin/workspace-mcp-manager-tui"),
                "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager-tui",
            )
            manager = subprocess.run(
                [str(prefix / "bin/workspace-mcp-manager"), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            tui = subprocess.run(
                [str(prefix / "bin/workspace-mcp-manager-tui"), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(manager.returncode, 0, manager.stderr)
            self.assertEqual(tui.returncode, 0, tui.stderr)
            manifest = json.loads((release / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["installer"], "bash")
            self.assertEqual(manifest["tui_runtime"]["textual_version"], "8.2.8")
            self.assertTrue((release / "install.sh").is_file())

    def test_tui_runtime_install_is_hash_locked_and_wheel_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            env = installer_env(root)
            completed = run_installer(repo, home, prefix, env)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            log = (root / "python-shim.log").read_text(encoding="utf-8")
            self.assertIn("--require-hashes", log)
            self.assertIn("--only-binary=:all:", log)
            self.assertIn("--target", log)

    def test_manager_and_reboot_remain_available_if_tui_runtime_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            env = installer_env(root)
            installed = run_installer(repo, home, prefix, env)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            fingerprint = json.loads(installed.stdout)["installed_fingerprint"]
            textual_dir = prefix / f"lib/workspace-mcp-manager/releases/{fingerprint}/tui-runtime/site-packages/textual"
            shutil.rmtree(textual_dir)

            manager = subprocess.run(
                [str(prefix / "bin/workspace-mcp-manager"), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            reboot = subprocess.run(
                [str(prefix / "bin/workspace-mcp-reboot"), "--invalid"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            tui = subprocess.run(
                [str(prefix / "bin/workspace-mcp-manager-tui"), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(manager.returncode, 0, manager.stderr)
            self.assertEqual(reboot.returncode, 2, reboot.stderr)
            self.assertNotEqual(tui.returncode, 0)
            self.assertIn("isolated Textual runtime is unavailable", tui.stderr)

    def test_same_release_reinstall_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            env = installer_env(root)
            first = run_installer(repo, home, prefix, env)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = run_installer(repo, home, prefix, env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                json.loads(first.stdout)["installed_fingerprint"],
                json.loads(second.stdout)["installed_fingerprint"],
            )

    def test_different_release_requires_upgrade_and_invalid_config_blocks_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo1 = copy_candidate(root / "repo1")
            repo2 = copy_candidate(root / "repo2")
            home = root / "home"
            prefix = home / ".local"
            env = installer_env(root)
            first = run_installer(repo1, home, prefix, env)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_fp = json.loads(first.stdout)["installed_fingerprint"]

            changed = repo2 / "src/workspace_mcp_manager/cli.py"
            changed.write_text(changed.read_text(encoding="utf-8") + "\n# candidate-two\n", encoding="utf-8")
            rejected = run_installer(repo2, home, prefix, env)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("requires explicit --upgrade", rejected.stderr)
            self.assertEqual(os.readlink(prefix / "lib/workspace-mcp-manager/current"), f"releases/{first_fp}")

            registry = home / ".config/workspace-mcp-manager/instances"
            registry.mkdir(parents=True)
            (registry / "broken.json").write_text('{"not":"a declaration"}\n', encoding="utf-8")
            invalid = run_installer(repo2, home, prefix, env, "--upgrade")
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("existing manager declaration validation failed", invalid.stderr)
            self.assertEqual(os.readlink(prefix / "lib/workspace-mcp-manager/current"), f"releases/{first_fp}")

            shutil.rmtree(registry)
            upgraded = run_installer(repo2, home, prefix, env, "--upgrade")
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
            second_fp = json.loads(upgraded.stdout)["installed_fingerprint"]
            self.assertNotEqual(first_fp, second_fp)
            self.assertEqual(os.readlink(prefix / "lib/workspace-mcp-manager/current"), f"releases/{second_fp}")

    def test_check_mode_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = copy_candidate(root / "repo")
            home = root / "home"
            prefix = home / ".local"
            env = installer_env(root)
            completed = run_installer(repo, home, prefix, env, "--check")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["installed"])
            self.assertFalse((prefix / "lib/workspace-mcp-manager").exists())


if __name__ == "__main__":
    unittest.main()
