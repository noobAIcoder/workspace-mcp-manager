from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.tooling import ToolingService


def paths_for(root: Path) -> ManagerPaths:
    return ManagerPaths(
        account_home=root,
        config_root=root / ".config/workspace-mcp-manager",
        registry_dir=root / ".config/workspace-mcp-manager/instances",
        state_root=root / ".local/state/workspace-mcp-manager",
        user_unit_dir=root / ".config/systemd/user",
        tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
        manager_executable=root / ".local/bin/workspace-mcp-manager",
    )


def executable(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o755)
    return path


class ToolingAuditTests(unittest.TestCase):
    def test_audit_uses_explicit_user_paths_and_nvm_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable(root / ".local/bin/uv")
            executable(root / ".local/bin/tunnel-client")
            executable(root / ".local/bin/coding-tools-mcp")
            nvm = root / ".nvm/versions/node/v24.15.0"
            executable(nvm / "bin/node")
            executable(nvm / "bin/npm")
            executable(nvm / "bin/codex")
            service = ToolingService(paths_for(root))
            with patch.object(service, "_version", return_value="synthetic-version"):
                payload = service.audit()
            self.assertEqual(payload["tools"]["uv"]["path"], str(root / ".local/bin/uv"))
            self.assertTrue(payload["tools"]["uv"]["present"])
            self.assertEqual(len(payload["node_roots"]), 1)
            self.assertEqual(payload["node_roots"][0]["root"], str(nvm))
            self.assertTrue(payload["node_roots"][0]["codex"]["present"])

    def test_agents_configure_defers_to_pm1_instance_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ToolingService(paths_for(Path(directory)))
            with self.assertRaises(ManagerError) as caught:
                service.agents_configure()
            self.assertEqual(caught.exception.code, ErrorCode.FEATURE_NOT_IMPLEMENTED)
            self.assertIn("config_version=2", caught.exception.message)


class CodexToolingTests(unittest.TestCase):
    def test_existing_codex_is_noop_and_reports_p10_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_root = root / ".nvm/versions/node/v24.15.0"
            executable(node_root / "bin/node")
            executable(node_root / "bin/npm")
            executable(node_root / "bin/codex")
            service = ToolingService(paths_for(root))

            def version(path, _args=("--version",), **_kwargs):
                return "codex-cli 0.147.0" if path.name == "codex" else "synthetic"

            with patch.object(service, "_version", side_effect=version):
                payload = service.codex(node_root=str(node_root), version=None, upgrade=False)
            self.assertEqual(payload["result"], "noop")
            self.assertEqual(payload["codex_version"], "0.147.0")
            self.assertEqual(payload["required_exec_path"], str(node_root / "bin"))
            self.assertEqual(payload["required_external_root"], str(node_root))

    def test_requested_different_version_requires_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_root = root / "node"
            for name in ("node", "npm", "codex"):
                executable(node_root / "bin" / name)
            service = ToolingService(paths_for(root))
            with patch.object(service, "_version", return_value="codex-cli 0.147.0"):
                with self.assertRaises(ManagerError) as caught:
                    service.codex(node_root=str(node_root), version="0.148.0", upgrade=False)
            self.assertEqual(caught.exception.code, ErrorCode.RECONCILIATION_CONFLICT)

    def test_install_uses_selected_npm_root_and_exact_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_root = root / "node"
            node = executable(node_root / "bin/node")
            npm = executable(node_root / "bin/npm")
            codex = node_root / "bin/codex"
            service = ToolingService(paths_for(root))
            observed: list[tuple[list[str], dict[str, str]]] = []

            def run(argv, *, env=None, timeout=120.0):
                observed.append((list(argv), dict(env or {})))
                if argv[0] == str(npm):
                    executable(codex)
                return subprocess.CompletedProcess(argv, 0, "", "")

            def version(path, _args=("--version",), **_kwargs):
                if path == codex and codex.exists():
                    return "codex-cli 0.148.0"
                return "synthetic"

            with patch.object(service, "_run", side_effect=run), patch.object(service, "_version", side_effect=version):
                payload = service.codex(node_root=str(node_root), version="0.148.0", upgrade=False)
            self.assertEqual(payload["result"], "installed")
            self.assertEqual(observed[0][0], [str(npm), "install", "-g", "@openai/codex@0.148.0"])
            self.assertTrue(observed[0][1]["PATH"].startswith(str(node_root / "bin")))
            self.assertEqual(observed[0][1]["NPM_CONFIG_PREFIX"], str(node_root))
            self.assertEqual(payload["codex_version"], "0.148.0")


class GhToolingTests(unittest.TestCase):
    def test_system_gh_is_noop_when_no_source_is_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ToolingService(paths_for(Path(directory)))
            with patch("workspace_mcp_manager.tooling._is_regular_executable") as executable_check, patch.object(
                service, "_version", return_value="gh version 2.45.0"
            ):
                executable_check.side_effect = lambda path: path == Path("/usr/bin/gh")
                payload = service.gh(source=None, sha256=None, upgrade=False)
            self.assertEqual(payload["result"], "noop")
            self.assertEqual(payload["path"], "/usr/bin/gh")

    def test_verified_source_is_copied_to_manager_owned_release_and_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = executable(root / "artifact/gh", "#!/bin/sh\necho 'gh version 9.9.9'\n")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            service = ToolingService(paths_for(root))
            payload = service.gh(source=str(source), sha256=digest, upgrade=False)
            local = root / ".local/bin/gh"
            self.assertTrue(local.is_symlink())
            self.assertEqual(payload["sha256"], digest)
            self.assertIn("gh version 9.9.9", payload["version"])
            installed = local.resolve(strict=True)
            self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), digest)
            self.assertIn("workspace-mcp-manager/tools/gh/releases", str(installed))

    def test_digest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = executable(root / "artifact/gh")
            service = ToolingService(paths_for(root))
            with self.assertRaises(ManagerError) as caught:
                service.gh(source=str(source), sha256="0" * 64, upgrade=False)
            self.assertEqual(caught.exception.code, ErrorCode.RECONCILIATION_CONFLICT)
            self.assertFalse((root / ".local/bin/gh").exists())

    def test_foreign_user_local_gh_requires_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable(root / ".local/bin/gh", "#!/bin/sh\necho foreign\n")
            source = executable(root / "artifact/gh", "#!/bin/sh\necho 'gh version 9.9.9'\n")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            service = ToolingService(paths_for(root))
            with self.assertRaises(ManagerError) as caught:
                service.gh(source=str(source), sha256=digest, upgrade=False)
            self.assertEqual(caught.exception.code, ErrorCode.RECONCILIATION_CONFLICT)


if __name__ == "__main__":
    unittest.main()
