from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.paths import ManagerPaths

from helpers import sample_instance


class GenerationTests(unittest.TestCase):
    def _paths(self, root: Path) -> ManagerPaths:
        return ManagerPaths(
            account_home=root,
            config_root=root / ".config/workspace-mcp-manager",
            registry_dir=root / ".config/workspace-mcp-manager/instances",
            state_root=root / ".local/state/workspace-mcp-manager",
            user_unit_dir=root / ".config/systemd/user",
            tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
            manager_executable=root / ".local/bin/workspace-mcp-manager",
        )

    def test_render_preserves_proven_unit_and_profile_semantics(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            desired = DesiredInstance.from_dict(sample_instance())
            bundle = ResourceGenerator(self._paths(root)).generate(desired)
            resources = {item.resource_id: item for item in bundle.resources}
            mcp = resources["mcp-unit"].content or ""
            tunnel = resources["tunnel-unit"].content or ""
            profile = resources["tunnel-profile"].content or ""
            self.assertIn("Restart=on-failure", mcp)
            self.assertIn("KillMode=mixed", mcp)
            self.assertIn("PrivateUsers=true", mcp)
            self.assertIn("BindReadOnlyPaths=", mcp)
            self.assertIn("BindPaths=", mcp)
            self.assertIn("CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=", mcp)
            self.assertIn("GH_CONFIG_DIR=", mcp)
            self.assertIn("Requires=coding-tools-mcp-sample.service", tunnel)
            self.assertIn("Restart=always", tunnel)
            self.assertIn('"--retry" "30"', tunnel)
            self.assertIn('"--retry-delay" "1"', tunnel)
            self.assertIn('"--retry-connrefused"', tunnel)
            self.assertIn('"--retry-max-time" "30"', tunnel)
            self.assertIn('"--connect-timeout" "1"', tunnel)
            self.assertIn('api_key: "env:CONTROL_PLANE_API_KEY"', profile)
            self.assertIn("tunnel_abc123", profile)

    def test_nvm_toolchain_path_and_version_root_are_rendered(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            raw = sample_instance()
            nvm_root = "/home/operator/.nvm/versions/node/v24.16.0"
            nvm_bin = f"{nvm_root}/bin"
            raw["mcp"]["exec_path"] = f"{nvm_bin}:/home/operator/.local/bin:/usr/bin:/bin"
            raw["mcp"]["external_roots"] = [nvm_root]
            raw["access"] = {"read_only": [], "read_write": []}
            raw["github"] = {"config_dir": None}
            raw["recovery"]["admission_guard_enabled"] = False
            desired = DesiredInstance.from_dict(raw)
            bundle = ResourceGenerator(self._paths(root)).generate(desired)
            resources = {item.resource_id: item for item in bundle.resources}
            mcp = resources["mcp-unit"].content or ""
            self.assertIn(f"PATH={nvm_bin}:/home/operator/.local/bin:/usr/bin:/bin", mcp)
            self.assertIn(f"CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS={nvm_root}", mcp)
            self.assertNotIn("nvm.sh", mcp)

    def test_guard_resources_are_conditional(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            raw = sample_instance()
            desired = DesiredInstance.from_dict(raw)
            enabled = {item.resource_id for item in ResourceGenerator(self._paths(root)).generate(desired).resources}
            self.assertIn("admission-guard-unit", enabled)
            self.assertIn("admission-guard-timer", enabled)
            raw["recovery"]["admission_guard_enabled"] = False
            desired = DesiredInstance.from_dict(raw)
            disabled = {item.resource_id for item in ResourceGenerator(self._paths(root)).generate(desired).resources}
            self.assertNotIn("admission-guard-unit", disabled)
            self.assertNotIn("admission-guard-timer", disabled)

    def test_generation_is_deterministic(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            desired = DesiredInstance.from_dict(sample_instance())
            generator = ResourceGenerator(self._paths(root))
            first = generator.generate(desired).to_dict(include_content=True)
            second = generator.generate(desired).to_dict(include_content=True)
            self.assertEqual(first, second)

    def test_resource_fingerprints_match_golden_contract(self) -> None:
        root = Path("/home/operator")
        desired = DesiredInstance.from_dict(sample_instance())
        bundle = ResourceGenerator(self._paths(root)).generate(desired)
        actual = {item.resource_id: item.fingerprint() for item in bundle.resources}
        golden_path = Path(__file__).parent / "golden" / "sample-resource-fingerprints.json"
        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is unavailable")
    def test_generated_core_units_pass_systemd_verify(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            raw = sample_instance()
            raw["workspace_path"] = str(workspace)
            raw["mcp"].update(
                {
                    "binary": "/bin/true",
                    "exec_path": "/usr/bin:/bin",
                    "external_roots": [],
                }
            )
            raw["tunnel"]["binary"] = "/bin/true"
            raw["access"] = {"read_only": [], "read_write": []}
            raw["github"] = {"config_dir": None}
            raw["recovery"]["admission_guard_enabled"] = False
            desired = DesiredInstance.from_dict(raw)
            paths = ManagerPaths(
                account_home=root,
                config_root=root / ".config/workspace-mcp-manager",
                registry_dir=root / ".config/workspace-mcp-manager/instances",
                state_root=root / ".local/state/workspace-mcp-manager",
                user_unit_dir=root / "units",
                tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
                manager_executable=Path("/bin/true"),
            )
            state_dir = paths.state_root / "instances" / desired.instance_id.value
            state_dir.mkdir(parents=True)
            paths.user_unit_dir.mkdir(parents=True)
            bundle = ResourceGenerator(paths, curl_binary="/bin/true").generate(desired)
            unit_paths: list[str] = []
            for resource in bundle.resources:
                if resource.unit_name:
                    path = paths.user_unit_dir / resource.unit_name
                    path.write_text(resource.content or "", encoding="utf-8")
                    unit_paths.append(str(path))
            completed = subprocess.run(
                ["systemd-analyze", "--user", "verify", *unit_paths],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                check=False,
                timeout=10,
            )
            if (
                completed.returncode != 0
                and "Failed to setup working directory: Permission denied" in completed.stderr
            ):
                self.skipTest("systemd-analyze verify is blocked by the current MCP sandbox")
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
