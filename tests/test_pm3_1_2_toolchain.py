from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.development_toolchain import (
    DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION,
    SemVer,
    node_range_satisfied,
    project_development_toolchain,
)
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.endpoint_projection import ListenerObservation, ListenerState
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.setup_projection import PortProjectionService, SetupProjectionService

from tests.helpers import sample_v2_instance


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


def executable(path: Path, output: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' {output!r}\n", encoding="utf-8")
    path.chmod(0o755)


def nvm_root(root: Path, version: str, *, pnpm: str) -> Path:
    selected = root / ".nvm/versions/node" / f"v{version}"
    executable(selected / "bin/node", f"v{version}")
    executable(selected / "bin/npm", "10.8.2")
    executable(selected / "bin/corepack", "0.34.0")
    executable(selected / "bin/pnpm", pnpm)
    return selected


def package_json(workspace: Path, *, node: str = ">=24.18.0 <25", pnpm: str = "11.17.0") -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text(
        json.dumps({"engines": {"node": node}, "packageManager": f"pnpm@{pnpm}"}) + "\n",
        encoding="utf-8",
    )


def desired(root: Path, workspace: Path, instance_id: str = "sample") -> DesiredInstance:
    raw = sample_v2_instance()
    raw["instance_id"] = instance_id
    raw["workspace_path"] = str(workspace)
    raw["mcp"]["exec_path"] = f"{root}/.local/bin:/usr/local/bin:/usr/bin:/bin"
    raw["mcp"]["external_roots"] = []
    raw["tunnel"]["profile"] = f"workspace-mcp-{instance_id}"
    raw["access"] = {"read_only": [], "read_write": []}
    raw["git"] = {"identity": None, "remote": None}
    raw["github"] = {"mode": "disabled", "config_dir": None, "binary": None}
    raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
    return DesiredInstance.from_dict(raw)


class SemverContractTests(unittest.TestCase):
    def test_electrocad_range_accepts_24_18_and_rejects_24_15(self) -> None:
        self.assertTrue(node_range_satisfied(">=24.18.0 <25", SemVer(24, 18, 0)))
        self.assertTrue(node_range_satisfied(">=24.18.0 <25", SemVer(24, 99, 0)))
        self.assertFalse(node_range_satisfied(">=24.18.0 <25", SemVer(24, 15, 0)))
        self.assertFalse(node_range_satisfied(">=24.18.0 <25", SemVer(25, 0, 0)))
        self.assertIsNone(node_range_satisfied("^24.18.0", SemVer(24, 18, 0)))


class ProjectionTests(unittest.TestCase):
    def test_incompatible_host_is_setup_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "ElectroCAD"
            package_json(workspace)
            nvm_root(root, "24.15.0", pnpm="11.6.0")
            payload = project_development_toolchain(paths_for(root), workspace)
            self.assertEqual(payload["development_toolchain_projection_version"], DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION)
            self.assertEqual(payload["selection"]["state"], "setup_required")
            self.assertEqual(payload["selection"]["reason_code"], "NODE_VERSION_UNAVAILABLE")
            self.assertFalse(payload["installed"]["node_roots"][0]["node"]["compatible"])

    def test_compatible_host_projects_exact_pnpm_and_declaration_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "ElectroCAD"
            package_json(workspace)
            selected = nvm_root(root, "24.18.0", pnpm="11.17.0")
            payload = project_development_toolchain(paths_for(root), workspace, desired=desired(root, workspace))
            self.assertEqual(payload["selection"]["state"], "ready")
            self.assertEqual(payload["selection"]["node_version"], "24.18.0")
            self.assertEqual(payload["selection"]["package_manager_version"], "11.17.0")
            self.assertEqual(payload["declaration"]["state"], "not_ready")
            self.assertTrue(payload["declaration"]["recommended_exec_path"].startswith(str(selected / "bin") + os.pathsep))
            self.assertEqual(payload["declaration"]["recommended_external_roots"][0], str(selected))

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_new_candidate_adopts_compatible_nvm_root_but_existing_declaration_is_not_silently_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            workspace = root / "repo"
            package_json(workspace)
            subprocess = __import__("subprocess")
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            selected = nvm_root(root, "24.18.0", pnpm="11.17.0")
            registry = InstanceRegistry(paths.registry_dir)
            ports = PortProjectionService(registry, listener_observation=ListenerObservation(ListenerState.AVAILABLE, ()))
            service = SetupProjectionService(paths, registry, ports=ports)
            request = {
                "candidate_request_version": 1,
                "workspace_path": str(workspace),
                "field_edits": [],
                "access_edits": [],
            }
            created = service.candidate(request)
            effective = created["effective_declaration"]
            self.assertTrue(effective["mcp"]["exec_path"].startswith(str(selected / "bin") + os.pathsep))
            self.assertEqual(effective["mcp"]["external_roots"][0], str(selected))
            provenance = {item["path"]: item for item in created["field_provenance"]}
            self.assertEqual(provenance["/mcp/exec_path"]["source"], "workspace_discovery")

            registered = desired(root, workspace, "repo")
            registry.create(registered)
            existing = service.candidate(request)
            self.assertEqual(existing["effective_declaration"]["mcp"]["exec_path"], registered.mcp.exec_path)
            declaration = existing["discovery"]["development_toolchain"]["declaration"]
            self.assertEqual(declaration["state"], "not_ready")
            self.assertEqual(declaration["reason_code"], "DECLARATION_TOOLCHAIN_MISMATCH")


if __name__ == "__main__":
    unittest.main()
