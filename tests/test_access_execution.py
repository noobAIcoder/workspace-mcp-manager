from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_access import empty_access_desired, paths_for, write_applied
from workspace_mcp_manager.access import AccessManager, stale_applied_access_resources
from workspace_mcp_manager.cli import build_parser
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ManagerError
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.operator_projection import OperatorProjectionService
from workspace_mcp_manager.registry import InstanceRegistry

MODES = {"ro": "read_only", "rx": "read_execute", "rw": "read_write", "rwx": "read_write_execute"}


class ExecutionAccessTests(unittest.TestCase):
    def test_four_modes_roundtrip_mutations_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_for(Path(directory))
            registry = InstanceRegistry(paths.registry_dir)
            desired = empty_access_desired(Path(directory))
            registry.create(desired)
            manager = AccessManager(paths, registry)
            for mode in MODES:
                manager.add("qual", mode=mode, alias=mode, path=f"/srv/{mode}")
            saved = registry.get("qual")
            self.assertEqual({e["mode"] for e in manager.list("qual")["entries"]}, set(MODES))
            summary = OperatorProjectionService._access_summary(saved)
            for mode, key in MODES.items():
                self.assertEqual(summary[key + "_count"], 1)
                manager.update("qual", existing_alias=mode, mode=mode, alias=mode, path=f"/srv/{mode}")
                manager.remove("qual", alias=mode)
            self.assertEqual(registry.get("qual").fingerprint(), desired.fingerprint())

    def test_optional_groups_preserve_legacy_fingerprint(self):
        desired = empty_access_desired(Path("/tmp/example"))
        raw = desired.to_dict()
        raw["access"].update(read_execute=[], read_write_execute=[])
        self.assertEqual(DesiredInstance.from_dict(raw).fingerprint(), desired.fingerprint())

    def test_cross_mode_alias_and_source_collisions(self):
        for duplicate in ("alias", "path"):
            raw = empty_access_desired(Path("/tmp/example")).to_dict()
            raw["access"]["read_only"] = [{"alias": "Shared-Docs", "path": "/srv/docs"}]
            raw["access"]["read_execute"] = [{
                "alias": "shared_docs" if duplicate == "alias" else "other",
                "path": "/srv/docs" if duplicate == "path" else "/srv/other",
            }]
            with self.subTest(duplicate=duplicate), self.assertRaises(ManagerError):
                DesiredInstance.from_dict(raw)

    def test_mounts_and_noexec_are_independent(self):
        raw = empty_access_desired(Path("/tmp/workspace")).to_dict()
        for mode, key in MODES.items():
            raw["access"][key] = [{"alias": mode, "path": f"/srv/{mode}"}]
        desired = DesiredInstance.from_dict(raw)
        bundle = ResourceGenerator(paths_for(Path("/tmp/account"))).generate(desired)
        unit = next(r.content for r in bundle.resources if r.resource_id == "mcp-unit")
        for mode in MODES:
            directive = "BindReadOnlyPaths" if mode in {"ro", "rx"} else "BindPaths"
            self.assertIn(f"{directive}=/srv/{mode}:", unit)
        restrictions = [line for line in unit.splitlines() if line.startswith("NoExecPaths=")]
        self.assertEqual(len(restrictions), 2)
        self.assertTrue(any(line.endswith("/ro") for line in restrictions))
        self.assertTrue(any(line.endswith("/rw") for line in restrictions))
        ids = [r.resource_id for r in bundle.resources]
        for mode in MODES:
            self.assertEqual(ids.count(f"access-{mode}-{mode}"), 1)

    def test_read_execute_sources_reach_landlock_without_other_access_modes(self):
        raw = empty_access_desired(Path("/tmp/workspace")).to_dict()
        for mode, key in MODES.items():
            raw["access"][key] = [{"alias": mode, "path": f"/srv/{mode}"}]
        raw["mcp"]["external_roots"] = ["/srv/toolchain"]
        desired = DesiredInstance.from_dict(raw)
        generator = ResourceGenerator(paths_for(Path("/tmp/account")))
        roots = generator._effective_external_roots(desired)
        self.assertEqual(roots.count("/srv/rx"), 1)
        self.assertIn("/srv/rwx", roots)
        self.assertNotIn("/srv/ro", roots)
        self.assertNotIn("/srv/rw", roots)
        unit = next(r.content for r in generator.generate(desired).resources if r.resource_id == "mcp-unit")
        self.assertIn("CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=/srv/toolchain:/srv/rx:/srv/rwx", unit)
        self.assertIn("BindReadOnlyPaths=/srv/rx:", unit)
        raw["mcp"]["external_roots"].append("/srv/rx")
        duplicate = DesiredInstance.from_dict(raw)
        self.assertEqual(generator._effective_external_roots(duplicate).count("/srv/rx"), 1)

    def test_mode_changes_keep_mountpoint_and_final_removal_cleans_it(self):
        for old_mode in MODES:
            for new_mode in MODES:
                with self.subTest(old=old_mode, new=new_mode), tempfile.TemporaryDirectory() as directory:
                    paths = paths_for(Path(directory))
                    raw = empty_access_desired(Path(directory)).to_dict()
                    raw["access"][MODES[old_mode]] = [{"alias": "docs", "path": "/srv/docs"}]
                    write_applied(paths, DesiredInstance.from_dict(raw))
                    raw["access"][MODES[old_mode]] = []
                    raw["access"][MODES[new_mode]] = [{"alias": "docs", "path": "/srv/docs"}]
                    desired = DesiredInstance.from_dict(raw)
                    self.assertEqual(stale_applied_access_resources(paths, desired, ResourceGenerator(paths).generate(desired)), ())
                    empty = empty_access_desired(Path(directory))
                    stale = stale_applied_access_resources(paths, empty, ResourceGenerator(paths).generate(empty))
                    self.assertIn(f"access-{old_mode}-docs", {r.resource_id for r in stale})

    def test_execute_resource_labels(self):
        from workspace_mcp_manager.operator_contracts import _resource_label
        for mode in MODES:
            self.assertEqual(_resource_label(f"access-{mode}-docs", "fallback"), f'{mode.upper()} access "docs"')

    def test_cli_accepts_all_modes(self):
        parser = build_parser()
        for mode in MODES:
            args = parser.parse_args(["access", "add-" + mode, "qual", "docs", "/srv/docs"])
            self.assertEqual(args.command, "add-" + mode)
            parser.parse_args(["access", "update", "qual", "docs", mode, "docs", "/srv/docs"])
