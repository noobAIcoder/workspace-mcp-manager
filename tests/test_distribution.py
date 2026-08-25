from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.sh"
BOOTSTRAP = ROOT / "bootstrap.sh"

A_COMMIT = "a" * 40
B_COMMIT = "b" * 40
TUNNEL_IDENTITY = (
    "0.0.11+8d55683eeef80bc5e360d95abf4692454fafc615 "
    "(git sha: 8d55683eeef80bc5e360d95abf4692454fafc615)"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def add_tar_file(archive: tarfile.TarFile, name: str, body: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mode = mode
    archive.addfile(info, io.BytesIO(body))


def make_python_archive(path: Path) -> str:
    identity = subprocess.check_output([sys.executable, "--version"], text=True).strip()
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"exec {shlex_quote(sys.executable)} \"$@\"\n"
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        add_tar_file(archive, "python/bin/python3", script, 0o755)
    return identity


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def make_uv_archive(path: Path) -> None:
    uv_script = r'''#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'uv 0.12.3\n'
  exit 0
fi
if [[ "${1:-}" != "pip" || "${2:-}" != "install" ]]; then
  printf 'fake uv: unsupported argv: %s\n' "$*" >&2
  exit 90
fi
target=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "--target" ]]; then
    target=${args[$((i+1))]}
    break
  fi
done
[[ -n "$target" ]] || { echo 'fake uv: missing --target' >&2; exit 91; }
if [[ "$target" == *"tui-runtime/site-packages" ]]; then
  mkdir -p "$target/textual"
  printf "__version__ = '8.2.8'\n" > "$target/textual/__init__.py"
  exit 0
fi
if [[ "$target" == *"runtimes/coding-tools-mcp/site-packages" ]]; then
  mkdir -p "$target/coding_tools_mcp" "$target/coding_tools_mcp-0.3.0.dist-info"
  printf '' > "$target/coding_tools_mcp/__init__.py"
  cat > "$target/coding_tools_mcp/server.py" <<'PY'
import argparse

def main():
    parser = argparse.ArgumentParser(prog="coding-tools-mcp")
    parser.parse_args()
    return 0
PY
  cat > "$target/coding_tools_mcp-0.3.0.dist-info/METADATA" <<'EOF'
Metadata-Version: 2.1
Name: coding-tools-mcp
Version: 0.3.0
EOF
  exit 0
fi
printf 'fake uv: unknown target: %s\n' "$target" >&2
exit 92
'''
    uvx_script = "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'uvx 0.12.3\\n'\n"
    with tarfile.open(path, "w:gz") as archive:
        add_tar_file(archive, "uv-x86_64-unknown-linux-gnu/uv", uv_script.encode(), 0o755)
        add_tar_file(archive, "uv-x86_64-unknown-linux-gnu/uvx", uvx_script.encode(), 0o755)


def make_tunnel_archive(path: Path) -> None:
    tunnel = f"#!/usr/bin/env bash\nset -euo pipefail\nif [[ \"${{1:-}}\" == \"--version\" ]]; then printf '%s\\n' {shlex_quote(TUNNEL_IDENTITY)}; exit 0; fi\nexit 0\n"
    cloudflared = "#!/usr/bin/env bash\nexit 0\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body, mode in (
            ("tunnel-client", tunnel.encode(), 0o755),
            ("cloudflared", cloudflared.encode(), 0o755),
            ("cloudflared-manifest.json", b"{}\n", 0o644),
            ("LICENSE", b"qualification fixture\n", 0o644),
        ):
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, body)


class DistributionFixture:
    def __init__(self, root: Path, *, commit: str = A_COMMIT) -> None:
        self.root = root
        self.repo = root / "repo"
        self.artifacts = root / "artifacts"
        self.home = root / "home"
        self.prefix = self.home / ".local"
        self.commit = commit
        self.artifacts.mkdir(parents=True)
        self._build_artifacts()
        self._build_repo()

    def _build_artifacts(self) -> None:
        self.python_archive = self.artifacts / "python-test.tar.gz"
        self.python_identity = make_python_archive(self.python_archive)
        self.uv_archive = self.artifacts / "uv-test.tar.gz"
        make_uv_archive(self.uv_archive)
        self.coding_wheel = self.artifacts / "coding_tools_mcp-0.3.0-py3-none-any.whl"
        self.coding_wheel.write_bytes(b"qualification coding-tools wheel\n")
        self.pyjwt_wheel = self.artifacts / "PyJWT-2.10.1-py3-none-any.whl"
        self.pyjwt_wheel.write_bytes(b"qualification pyjwt wheel\n")
        self.tunnel_archive = self.artifacts / "tunnel-test.zip"
        make_tunnel_archive(self.tunnel_archive)

    def _build_repo(self) -> None:
        self.repo.mkdir(parents=True)
        shutil.copytree(ROOT / "src", self.repo / "src")
        shutil.copy2(ROOT / "pyproject.toml", self.repo / "pyproject.toml")
        shutil.copy2(ROOT / "requirements-tui.lock", self.repo / "requirements-tui.lock")
        shutil.copy2(ROOT / "bootstrap.sh", self.repo / "bootstrap.sh")
        (self.repo / "scripts").mkdir()
        for name in ("install.sh", "install_toolkit.sh", "upgrade.sh"):
            shutil.copy2(ROOT / "scripts" / name, self.repo / "scripts" / name)

        coding_lock = (
            "coding-tools-mcp==0.3.0 \\\n"
            f"    --hash=sha256:{sha256(self.coding_wheel)}\n"
            "PyJWT==2.10.1 \\\n"
            f"    --hash=sha256:{sha256(self.pyjwt_wheel)}\n"
        )
        (self.repo / "requirements-coding-tools.lock").write_text(coding_lock, encoding="utf-8")
        coding_lock_sha = hashlib.sha256(coding_lock.encode()).hexdigest()
        lock = {
            "schema_version": 1,
            "platform": "linux",
            "architecture": "x86_64",
            "components": {
                "python": {
                    "version": self.python_identity.removeprefix("Python "),
                    "source_kind": "github_release_asset",
                    "source_identity": "qualification/python-test.tar.gz",
                    "acquisition_location": "https://qualification.invalid/python-test.tar.gz",
                    "sha256": sha256(self.python_archive),
                    "platform": "linux",
                    "architecture": "x86_64",
                    "dependency_lock_identity": None,
                    "dependencies": [],
                    "validator_id": "cpython-3.12",
                    "expected_observable_identity": self.python_identity,
                },
                "uv": {
                    "version": "0.12.3",
                    "source_kind": "github_release_asset",
                    "source_identity": "qualification/uv-test.tar.gz",
                    "acquisition_location": "https://qualification.invalid/uv-test.tar.gz",
                    "sha256": sha256(self.uv_archive),
                    "platform": "linux",
                    "architecture": "x86_64",
                    "dependency_lock_identity": None,
                    "dependencies": [],
                    "validator_id": "uv-cli",
                    "expected_observable_identity": "uv 0.12.3",
                },
                "coding_tools_mcp": {
                    "version": "0.3.0",
                    "source_kind": "pypi_wheel",
                    "source_identity": "qualification/coding_tools_mcp-0.3.0-py3-none-any.whl",
                    "acquisition_location": "https://qualification.invalid/coding_tools_mcp-0.3.0-py3-none-any.whl",
                    "sha256": sha256(self.coding_wheel),
                    "platform": "linux",
                    "architecture": "x86_64",
                    "dependency_lock_identity": f"requirements-coding-tools.lock@sha256:{coding_lock_sha}",
                    "dependencies": [
                        {
                            "name": "PyJWT",
                            "version": "2.10.1",
                            "source_identity": "qualification/PyJWT-2.10.1-py3-none-any.whl",
                            "acquisition_location": "https://qualification.invalid/PyJWT-2.10.1-py3-none-any.whl",
                            "sha256": sha256(self.pyjwt_wheel),
                        }
                    ],
                    "validator_id": "coding-tools-mcp-0.3",
                    "expected_observable_identity": "0.3.0",
                },
                "tunnel_client": {
                    "version": "0.0.11",
                    "source_kind": "github_release_asset",
                    "source_identity": "qualification/tunnel-test.zip",
                    "acquisition_location": "https://qualification.invalid/tunnel-test.zip",
                    "sha256": sha256(self.tunnel_archive),
                    "platform": "linux",
                    "architecture": "x86_64",
                    "dependency_lock_identity": None,
                    "dependencies": [],
                    "validator_id": "tunnel-client-0.0.11",
                    "expected_observable_identity": TUNNEL_IDENTITY,
                },
            },
        }
        (self.repo / "install-versions.lock").write_text(
            json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def args(self, *extra: str, commit: str | None = None) -> list[str]:
        return [
            "bash",
            str(self.repo / "scripts/install.sh"),
            "--qualification",
            "--repo",
            str(self.repo),
            "--account-home",
            str(self.home),
            "--prefix",
            str(self.prefix),
            "--repository",
            "https://qualification.invalid/workspace-mcp-manager.git",
            "--release-ref",
            "refs/heads/main",
            "--release-commit",
            commit or self.commit,
            "--qualification-artifact-dir",
            str(self.artifacts),
            *extra,
        ]

    def run(self, *extra: str, commit: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        return subprocess.run(
            self.args(*extra, commit=commit),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
            timeout=timeout,
        )

    def recover(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        return subprocess.run(
            [
                "bash",
                str(self.repo / "scripts/install.sh"),
                "--recover-only",
                "--account-home",
                str(self.home),
                "--prefix",
                str(self.prefix),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
            timeout=30,
        )

    @property
    def current(self) -> Path:
        return self.prefix / "lib/workspace-mcp-manager/current"

    def current_target(self) -> str | None:
        return os.readlink(self.current) if self.current.is_symlink() else None

    def create_recognized_predecessor(self) -> Path:
        predecessor_id = "c" * 64
        root = self.prefix / "lib/workspace-mcp-manager"
        release = root / "releases" / predecessor_id
        (release / "src/workspace_mcp_manager").mkdir(parents=True)
        (release / "requirements-tui.lock").write_text("# predecessor\n", encoding="utf-8")
        (release / "install.json").write_text('{"schema_version":2}\n', encoding="utf-8")
        (release / "tui-runtime/site-packages/textual").mkdir(parents=True)
        for name in ("workspace-mcp-manager", "workspace-mcp-manager-tui", "workspace-mcp-reboot"):
            write_executable(release / "bin" / name, "#!/usr/bin/env bash\nexit 0\n")
        root.mkdir(parents=True, exist_ok=True)
        (root / "current").symlink_to(Path("releases") / predecessor_id)
        (self.prefix / "bin").mkdir(parents=True, exist_ok=True)
        for name in ("workspace-mcp-manager", "workspace-mcp-manager-tui", "workspace-mcp-reboot"):
            (self.prefix / "bin" / name).symlink_to(
                Path("../lib/workspace-mcp-manager/current/bin") / name
            )
        return release

    def write_release_a_declaration(self) -> tuple[Path, bytes]:
        release = self.prefix / f"lib/workspace-mcp-manager/releases/dist-{A_COMMIT}"
        data = json.loads((ROOT / "examples/manager.json").read_text(encoding="utf-8"))
        data["workspace_path"] = str(self.repo)
        data["mcp"]["binary"] = str(release / "runtimes/coding-tools-mcp/bin/coding-tools-mcp")
        data["tunnel"]["binary"] = str(release / "runtimes/tunnel-client/tunnel-client")
        registry = self.home / ".config/workspace-mcp-manager/instances"
        registry.mkdir(parents=True, exist_ok=True)
        declaration = registry / "manager.json"
        body = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
        declaration.write_bytes(body)
        return declaration, body


def snapshot_tree(path: Path) -> dict[str, tuple[str, str, int, int]]:
    result: dict[str, tuple[str, str, int, int]] = {}
    if not path.exists() and not path.is_symlink():
        return result
    roots = [path]
    while roots:
        current = roots.pop()
        rel = "." if current == path else current.relative_to(path).as_posix()
        st = current.lstat()
        mode = st.st_mode & 0o7777
        if current.is_symlink():
            result[rel] = ("symlink", os.readlink(current), mode, st.st_mtime_ns)
        elif current.is_dir():
            result[rel] = ("dir", "", mode, st.st_mtime_ns)
            roots.extend(sorted(current.iterdir(), reverse=True))
        elif current.is_file():
            result[rel] = ("file", sha256(current), mode, st.st_mtime_ns)
    return result


class DistributionInputTests(unittest.TestCase):
    def test_valid_strict_lock_is_accepted_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            completed = fixture.run("--check")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["ownership"], "absent")
            self.assertEqual(payload["candidate_distribution_id"], f"dist-{A_COMMIT}")
            self.assertTrue(payload["install_required"])
            self.assertFalse(fixture.prefix.exists())

    def test_duplicate_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            lock = fixture.repo / "install-versions.lock"
            text = lock.read_text(encoding="utf-8")
            lock.write_text(text.replace("{", '{"schema_version": 1,', 1), encoding="utf-8")
            completed = fixture.run("--check")
            self.assertEqual(completed.returncode, 2)
            self.assertIn("lock validation failed", completed.stderr)

    def test_unknown_lock_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            lock = fixture.repo / "install-versions.lock"
            data = json.loads(lock.read_text(encoding="utf-8"))
            data["unexpected"] = True
            lock.write_text(json.dumps(data), encoding="utf-8")
            completed = fixture.run("--check")
            self.assertEqual(completed.returncode, 2)

    def test_lock_text_is_inert_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            marker = Path(directory) / "executed"
            lock = fixture.repo / "install-versions.lock"
            data = json.loads(lock.read_text(encoding="utf-8"))
            data["components"]["uv"]["source_identity"] = f"$(touch {marker})"
            lock.write_text(json.dumps(data), encoding="utf-8")
            completed = fixture.run("--check")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())

    def test_wrong_platform_and_dependency_lock_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            lock = fixture.repo / "install-versions.lock"
            data = json.loads(lock.read_text(encoding="utf-8"))
            data["components"]["uv"]["architecture"] = "arm64"
            lock.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(fixture.run("--check").returncode, 2)

            fixture = DistributionFixture(Path(directory) / "second")
            dep_lock = fixture.repo / "requirements-coding-tools.lock"
            dep_lock.write_text(dep_lock.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
            self.assertEqual(fixture.run("--check").returncode, 2)


class DistributionTransactionTests(unittest.TestCase):
    def test_clean_install_builds_complete_release_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            completed = fixture.run()
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["action"], "install")
            self.assertEqual(fixture.current_target(), f"releases/dist-{A_COMMIT}")
            release = fixture.prefix / f"lib/workspace-mcp-manager/releases/dist-{A_COMMIT}"
            for relative in (
                "bin/workspace-mcp-manager",
                "bin/workspace-mcp-manager-tui",
                "bin/workspace-mcp-reboot",
                "bin/workspace-mcp-manager-upgrade",
                "manager/src/workspace_mcp_manager/cli.py",
                "manager/tui-runtime/site-packages/textual/__init__.py",
                "runtimes/python/bin/python3",
                "runtimes/coding-tools-mcp/bin/coding-tools-mcp",
                "runtimes/tunnel-client/tunnel-client",
                "tools/uv/uv",
                "distribution/install.sh",
                "distribution/upgrade.sh",
                "distribution/install-versions.lock",
                "install.json",
            ):
                self.assertTrue((release / relative).exists(), relative)
            receipt = fixture.home / ".local/state/workspace-mcp-manager/distribution.json"
            self.assertTrue(receipt.is_file())
            evidence = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(evidence["distribution_id"], f"dist-{A_COMMIT}")
            self.assertEqual(evidence["install_json_sha256"], sha256(release / "install.json"))
            for name in (
                "workspace-mcp-manager",
                "workspace-mcp-manager-tui",
                "workspace-mcp-reboot",
                "workspace-mcp-manager-upgrade",
            ):
                link = fixture.prefix / "bin" / name
                self.assertTrue(link.is_symlink())
                self.assertEqual(os.readlink(link), f"../lib/workspace-mcp-manager/current/bin/{name}")

    def test_same_release_reinstall_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            first = fixture.run()
            self.assertEqual(first.returncode, 0, first.stderr)
            receipt = fixture.home / ".local/state/workspace-mcp-manager/distribution.json"
            before = receipt.read_bytes()
            second = fixture.run()
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["action"], "noop")
            self.assertEqual(receipt.read_bytes(), before)

    def test_digest_failure_rolls_back_clean_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            fixture.python_archive.write_bytes(fixture.python_archive.read_bytes() + b"corrupt")
            completed = fixture.run()
            self.assertEqual(completed.returncode, 2)
            self.assertIn("artifact digest mismatch", completed.stderr)
            self.assertIsNone(fixture.current_target())
            self.assertFalse((fixture.prefix / "bin/workspace-mcp-manager").exists())
            self.assertFalse((fixture.home / ".local/state/workspace-mcp-manager/distribution-transaction.json").exists())

    def test_foreign_entrypoint_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            foreign = fixture.prefix / "bin/workspace-mcp-manager"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("foreign\n", encoding="utf-8")
            completed = fixture.run()
            self.assertEqual(completed.returncode, 2)
            self.assertIn("foreign or ambiguous", completed.stderr)
            self.assertEqual(foreign.read_text(encoding="utf-8"), "foreign\n")

    def test_manifest_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            installed = fixture.run()
            self.assertEqual(installed.returncode, 0, installed.stderr)
            release = fixture.prefix / f"lib/workspace-mcp-manager/releases/dist-{A_COMMIT}"
            (release / "manager/src/workspace_mcp_manager/cli.py").write_text("tampered\n", encoding="utf-8")
            checked = fixture.run("--check")
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("foreign_or_ambiguous", checked.stdout)

    def test_a_to_b_upgrade_retains_a(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            first = fixture.run()
            self.assertEqual(first.returncode, 0, first.stderr)
            second = fixture.run("--upgrade", commit=B_COMMIT)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["action"], "upgrade")
            self.assertEqual(fixture.current_target(), f"releases/dist-{B_COMMIT}")
            releases = fixture.prefix / "lib/workspace-mcp-manager/releases"
            self.assertTrue((releases / f"dist-{A_COMMIT}").is_dir())
            self.assertTrue((releases / f"dist-{B_COMMIT}").is_dir())

    def test_upgrade_preserves_existing_declaration_and_release_a_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            first = fixture.run()
            self.assertEqual(first.returncode, 0, first.stderr)
            declaration, before = fixture.write_release_a_declaration()
            data_before = json.loads(before)
            self.assertTrue(Path(data_before["mcp"]["binary"]).is_file())
            self.assertTrue(Path(data_before["tunnel"]["binary"]).is_file())
            upgraded = fixture.run("--upgrade", commit=B_COMMIT)
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
            self.assertEqual(declaration.read_bytes(), before)
            data_after = json.loads(declaration.read_text(encoding="utf-8"))
            self.assertEqual(data_after["mcp"]["binary"], data_before["mcp"]["binary"])
            self.assertEqual(data_after["tunnel"]["binary"], data_before["tunnel"]["binary"])
            self.assertTrue(Path(data_after["mcp"]["binary"]).is_file())
            self.assertTrue(Path(data_after["tunnel"]["binary"]).is_file())

    def test_recognized_predecessor_migrates_and_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            predecessor = fixture.create_recognized_predecessor()
            before = snapshot_tree(predecessor)
            migrated = fixture.run()
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assertEqual(json.loads(migrated.stdout)["action"], "migration")
            self.assertEqual(fixture.current_target(), f"releases/dist-{A_COMMIT}")
            self.assertEqual(snapshot_tree(predecessor), before)
            self.assertTrue((fixture.prefix / "bin/workspace-mcp-manager-upgrade").is_symlink())

    def test_recognized_predecessor_pre_and_postcommit_recovery(self) -> None:
        cases = (
            ("after_candidate_finalization", "previous_distribution", "c" * 64),
            ("after_current_commit", "committed_candidate", f"dist-{A_COMMIT}"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (failpoint, recovery_result, expected_name) in enumerate(cases):
                with self.subTest(failpoint=failpoint):
                    fixture = DistributionFixture(root / str(index))
                    predecessor = fixture.create_recognized_predecessor()
                    interrupted = fixture.run("--qualification-failpoint", failpoint)
                    self.assertLess(interrupted.returncode, 0)
                    recovered = fixture.recover()
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    self.assertEqual(json.loads(recovered.stdout)["recovery_result"], recovery_result)
                    self.assertEqual(Path(fixture.current_target() or "").name, expected_name)
                    self.assertTrue(predecessor.is_dir())

    def test_precommit_hard_interruption_recovers_a(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            self.assertEqual(fixture.run().returncode, 0)
            interrupted = fixture.run(
                "--upgrade",
                "--qualification-failpoint",
                "after_candidate_finalization",
                commit=B_COMMIT,
            )
            self.assertLess(interrupted.returncode, 0)
            self.assertEqual(fixture.current_target(), f"releases/dist-{A_COMMIT}")
            recovered = fixture.recover()
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(json.loads(recovered.stdout)["recovery_result"], "previous_distribution")
            self.assertEqual(fixture.current_target(), f"releases/dist-{A_COMMIT}")
            self.assertFalse((fixture.prefix / f"lib/workspace-mcp-manager/releases/dist-{B_COMMIT}").exists())

    def test_postcommit_hard_interruption_recovers_b(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            self.assertEqual(fixture.run().returncode, 0)
            interrupted = fixture.run(
                "--upgrade",
                "--qualification-failpoint",
                "after_current_commit",
                commit=B_COMMIT,
            )
            self.assertLess(interrupted.returncode, 0)
            self.assertEqual(fixture.current_target(), f"releases/dist-{B_COMMIT}")
            recovered = fixture.recover()
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(json.loads(recovered.stdout)["recovery_result"], "committed_candidate")
            self.assertEqual(fixture.current_target(), f"releases/dist-{B_COMMIT}")
            receipt = fixture.home / ".local/state/workspace-mcp-manager/distribution.json"
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["distribution_id"], f"dist-{B_COMMIT}")

    def test_all_hard_interruption_boundaries_converge_deterministically(self) -> None:
        cases = (
            ("after_journal_persistence", A_COMMIT, "previous_distribution"),
            ("during_candidate_construction", A_COMMIT, "previous_distribution"),
            ("after_candidate_finalization", A_COMMIT, "previous_distribution"),
            ("after_receipt_preparation", A_COMMIT, "previous_distribution"),
            ("during_current_commit", A_COMMIT, "previous_distribution"),
            ("after_current_commit", B_COMMIT, "committed_candidate"),
            ("during_receipt_publication", B_COMMIT, "committed_candidate"),
            ("after_receipt_publication", B_COMMIT, "committed_candidate"),
            ("before_journal_resolution", B_COMMIT, "committed_candidate"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (failpoint, expected_commit, recovery_result) in enumerate(cases):
                with self.subTest(failpoint=failpoint):
                    fixture = DistributionFixture(root / str(index))
                    installed = fixture.run()
                    self.assertEqual(installed.returncode, 0, installed.stderr)
                    interrupted = fixture.run(
                        "--upgrade",
                        "--qualification-failpoint",
                        failpoint,
                        commit=B_COMMIT,
                    )
                    self.assertLess(interrupted.returncode, 0)
                    checked = fixture.run("--check", commit=B_COMMIT)
                    self.assertEqual(checked.returncode, 0, checked.stderr)
                    projection = json.loads(checked.stdout)
                    self.assertTrue(projection["recovery_required"])
                    self.assertEqual(projection["ownership"], "standalone_distribution")
                    recovered = fixture.recover()
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    self.assertEqual(json.loads(recovered.stdout)["recovery_result"], recovery_result)
                    self.assertEqual(fixture.current_target(), f"releases/dist-{expected_commit}")
                    self.assertFalse(
                        (fixture.home / ".local/state/workspace-mcp-manager/distribution-transaction.json").exists()
                    )

    def test_check_is_byte_and_metadata_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            installed = fixture.run()
            self.assertEqual(installed.returncode, 0, installed.stderr)
            before_prefix = snapshot_tree(fixture.prefix)
            before_state = snapshot_tree(fixture.home / ".local/state/workspace-mcp-manager")
            checked = fixture.run("--check")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(snapshot_tree(fixture.prefix), before_prefix)
            self.assertEqual(snapshot_tree(fixture.home / ".local/state/workspace-mcp-manager"), before_state)

    def test_configure_failure_occurs_after_commit_and_does_not_roll_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            configured = fixture.run("--configure")
            self.assertEqual(configured.returncode, 2)
            self.assertEqual(fixture.current_target(), f"releases/dist-{A_COMMIT}")
            receipt = fixture.home / ".local/state/workspace-mcp-manager/distribution.json"
            self.assertTrue(receipt.is_file())
            self.assertFalse(
                (fixture.home / ".local/state/workspace-mcp-manager/distribution-transaction.json").exists()
            )

    def test_invalid_existing_declaration_prevents_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            registry = fixture.home / ".config/workspace-mcp-manager/instances"
            registry.mkdir(parents=True)
            declaration = registry / "broken.json"
            declaration.write_text('{"not":"valid"}\n', encoding="utf-8")
            before = declaration.read_bytes()
            completed = fixture.run()
            self.assertEqual(completed.returncode, 2)
            self.assertIsNone(fixture.current_target())
            self.assertEqual(declaration.read_bytes(), before)

    def test_distribution_mutation_lock_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DistributionFixture(Path(directory))
            state = fixture.home / ".local/state/workspace-mcp-manager"
            state.mkdir(parents=True)
            lock_path = state / "distribution.lock"
            with lock_path.open("w") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                completed = fixture.run(timeout=15)
                self.assertEqual(completed.returncode, 2)
                self.assertIn("holds the account-level lock", completed.stderr)


class DistributionAcquisitionTests(unittest.TestCase):
    def _git_repository(self, root: Path) -> tuple[DistributionFixture, Path, Path]:
        fixture = DistributionFixture(root / "fixture")
        source = root / "source"
        shutil.copytree(fixture.repo, source)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Qualification", "-c", "user.email=qualification@example.invalid", "commit", "-qm", "A"],
            cwd=source,
            check=True,
        )
        remote = root / "remote.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
        return fixture, source, remote

    def test_bootstrap_establishes_exact_detached_git_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, _source, remote = self._git_repository(root)
            home = root / "home"
            home.mkdir(parents=True)
            (home / ".gitconfig").write_text(
                '[url "file:///definitely-not-the-qualified-remote/"]\n\tinsteadOf = file://\n',
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = str(home)
            completed = subprocess.run(
                [
                    "bash",
                    str(BOOTSTRAP),
                    "--qualification",
                    "--repository",
                    remote.as_uri(),
                    "--ref",
                    "refs/heads/main",
                    "--account-home",
                    str(home),
                    "--qualification-artifact-dir",
                    str(fixture.artifacts),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            remote_sha = subprocess.check_output(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"], text=True).strip()
            self.assertEqual(payload["release_commit"], remote_sha)
            current = home / ".local/lib/workspace-mcp-manager/current"
            self.assertEqual(os.readlink(current), f"releases/dist-{remote_sha}")

    def test_installed_upgrader_uses_newly_acquired_installer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, source, remote = self._git_repository(root)
            home = root / "home"
            env = os.environ.copy()
            env["HOME"] = str(home)
            base = [
                "bash",
                str(BOOTSTRAP),
                "--qualification",
                "--repository",
                remote.as_uri(),
                "--ref",
                "refs/heads/main",
                "--account-home",
                str(home),
                "--qualification-artifact-dir",
                str(fixture.artifacts),
            ]
            first = subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, env=env, timeout=60)
            self.assertEqual(first.returncode, 0, first.stderr)
            a_sha = json.loads(first.stdout)["release_commit"]

            marker = source / "qualification-b.txt"
            marker.write_text("B\n", encoding="utf-8")
            subprocess.run(["git", "add", "qualification-b.txt"], cwd=source, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Qualification", "-c", "user.email=qualification@example.invalid", "commit", "-qm", "B"],
                cwd=source,
                check=True,
            )
            subprocess.run(["git", "push", "-q", remote.as_uri(), "main:main"], cwd=source, check=True)
            b_sha = subprocess.check_output(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"], text=True).strip()
            self.assertNotEqual(a_sha, b_sha)

            upgrader = home / ".local/bin/workspace-mcp-manager-upgrade"
            upgraded = subprocess.run(
                [
                    str(upgrader),
                    "--qualification",
                    "--repository",
                    remote.as_uri(),
                    "--ref",
                    "refs/heads/main",
                    "--account-home",
                    str(home),
                    "--qualification-artifact-dir",
                    str(fixture.artifacts),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
                timeout=60,
            )
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
            self.assertEqual(json.loads(upgraded.stdout)["release_commit"], b_sha)
            current = home / ".local/lib/workspace-mcp-manager/current"
            self.assertEqual(os.readlink(current), f"releases/dist-{b_sha}")
            self.assertTrue((home / f".local/lib/workspace-mcp-manager/releases/dist-{a_sha}").is_dir())


if __name__ == "__main__":
    unittest.main()
