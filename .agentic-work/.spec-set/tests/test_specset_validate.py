from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import specset_validate as sv  # noqa: E402


class SpecSetValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = ROOT / "tests/fixtures/specset/workspace"
        cls.codegen = ROOT / ".templates/codegen"
        cls.service = ROOT / ".templates/service"

    def assert_has(self, issues: list[sv.Issue], code: str) -> None:
        self.assertTrue(any(issue.code == code for issue in issues), [i.code for i in issues])

    def assert_lacks(self, issues: list[sv.Issue], code: str) -> None:
        self.assertFalse(any(issue.code == code for issue in issues), [i.code for i in issues])

    def copy_framework(self, destination: Path) -> Path:
        root = destination / "repo"
        root.mkdir()
        shutil.copy2(ROOT / "AGENTS.md", root / "AGENTS.md")
        shutil.copytree(ROOT / ".templates", root / ".templates")
        shutil.copytree(ROOT / "tools", root / "tools")
        shutil.copytree(ROOT / "tests", root / "tests")
        return root

    def copy_workspace(self, destination: Path) -> Path:
        workspace = destination / "workspace"
        shutil.copytree(self.workspace, workspace)
        return workspace

    def test_framework_positive(self) -> None:
        self.assertEqual([], sv.validate_framework(ROOT))

    def test_framework_is_independent_of_installation_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.copy_framework(Path(tmp))
            moved = Path(tmp) / "arbitrary-component-name"
            root.rename(moved)
            self.assertFalse((moved.parent / "REPO_INFO.yaml").exists())
            self.assertEqual([], sv.validate_framework(moved))

    def test_alpha_ui_positive(self) -> None:
        self.assertEqual([], sv.validate_project(self.workspace, ROOT, "alpha-ui"))

    def test_beta_integration_positive(self) -> None:
        self.assertEqual([], sv.validate_project(self.workspace, ROOT, "beta-integration"))

    def test_framework_uses_single_agent_policy(self) -> None:
        self.assertTrue((ROOT / "AGENTS.md").is_file())
        self.assertFalse((ROOT / "AGENTS.specset.md").exists())
        self.assertFalse((ROOT / ".agents").exists())

    def test_specset_registry_is_narrow_and_consumer_free(self) -> None:
        index = sv.parse_index(self.codegen / "SPEC_FILES_INDEX.specset.md")
        rows = [row for row in index.framework.values() if row.get("STATUS") == "active"]
        self.assertTrue(rows)
        self.assertTrue(all(row["CLASS"] in {"specset_authority", "specset_template"} for row in rows))
        self.assertNotIn("VAL", index.framework)
        self.assertNotIn("TST", index.framework)

    def test_registered_templates_have_valid_local_contracts(self) -> None:
        index = sv.parse_index(self.codegen / "SPEC_FILES_INDEX.specset.md")
        rows = [row for row in index.framework.values() if row.get("CLASS") == "specset_template" and row.get("STATUS") == "active"]
        expected = {key for key, row in index.documents.items() if row.get("SPECSET_SOURCE", "").endswith(".specset.template.md")}
        self.assertEqual(expected, {row["SPECSET_KEY"] for row in rows})
        for row in rows:
            path = self.codegen / row["ACTIVE_PATH"]
            contract, issues = sv.authoring_contract(path)
            self.assertEqual([], issues, path)
            self.assertIsNotNone(contract, path)
            self.assertEqual(row["SPECSET_KEY"], contract["document_key"])
            self.assertIn("applicability", contract)

    def test_contract_parser_exposes_local_metadata(self) -> None:
        contract, issues = sv.authoring_contract(self.codegen / "SLICE_XXX.specset.template.md")
        self.assertEqual([], issues)
        self.assertIsNotNone(contract)
        self.assertTrue(contract["readiness"])
        self.assertTrue(contract["typed_fields"])
        self.assertTrue(contract["applicability"])
        self.assertIn("composition", contract)
        self.assertEqual([], contract["change_areas"])

    def test_screen_requirement_namespace_is_narrow(self) -> None:
        index = sv.parse_index(self.codegen / "SPEC_FILES_INDEX.specset.md")
        self.assertEqual("requirements:DS-SCR-", index.namespaces["screen_requirement_id"]["DECLARATION"])
        contract, issues = sv.authoring_contract(self.codegen / "UI_SPEC.specset.template.md")
        self.assertEqual([], issues)
        binding = next(entry for entry in contract["typed_fields"] if entry.get("field") == "DS_SCREEN_REF")
        self.assertEqual("screen_requirement_id", binding["namespace"])

    def test_domain_and_technical_persistence_ownership_is_split(self) -> None:
        domain = (self.codegen / "DOMAIN_SPEC.specset.template.md").read_text()
        technical = (self.codegen / "TECHNICAL_SPEC.specset.template.md").read_text()
        self.assertIn("Logical persistence registry", domain)
        self.assertNotIn("Physical index registry", domain)
        self.assertIn("Physical index registry", technical)
        self.assertIn("STORAGE_TECHNOLOGY", technical)

    def test_ui_has_real_no_ui_branch(self) -> None:
        template = (self.codegen / "UI_SPEC.specset.template.md").read_text()
        self.assertIn("### UI-OVR-NOUIR || Declare no interactive UI", template)
        self.assertIn("UI-OVR-NOUIR-NOUI_TBD", template)
        self.assertEqual([], sv.validate_project(self.workspace, ROOT, "beta-integration"))

    def test_placeholder_requirement_requires_tbd_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.copy_framework(Path(tmp))
            path = root / ".templates/codegen/PRODUCT_SPEC.specset.template.md"
            text = path.read_text()
            self.assertIn("PS-JRN-JRNRG-TBD_JOURNEY", text)
            path.write_text(text.replace("PS-JRN-JRNRG-TBD_JOURNEY", "PS-JRN-JRNRG-USER_JOURNEY", 1))
            self.assert_has(sv.validate_framework(root), "PLACEHOLDER_REQ_ID")

    def test_materialized_self_path_mirror_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PRODUCT_SPEC.alpha-ui.md"
            text = (self.workspace / "docs/PRODUCT_SPEC.alpha-ui.md").read_text()
            marker = "| source_docs | docs/PRODUCT_BRIEF.alpha-ui.md |\n"
            path.write_text(text.replace(marker, marker + "| materialized_path | docs/PRODUCT_SPEC.alpha-ui.md |\n", 1))
            self.assert_has(sv.parse_normative(path, "materialized").issues, "SELF_PATH_MIRROR")

    def test_materialized_document_pattern_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/DOMAIN_SPEC.alpha-ui.md"
            text = path.read_text().replace("docs/slices/SLICE_001.alpha-ui.md,docs/slices/SLICE_002.alpha-ui.md", "docs/slices/SLICE_<NNN>.alpha-ui.md", 1)
            path.write_text(text)
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "DOCUMENT_PATTERN")

    def test_scalar_none_is_legal_when_typed_field_allows_empty(self) -> None:
        binding = {"context": "X", "field": "Y", "kind": "scalar", "namespace": "migration_key", "empty": "allowed"}
        issues: list[sv.Issue] = []
        sv._validate_reference_value("(none)", binding, {"migration_key": {"MIG_X"}}, Path("x.md"), 1, "materialized", issues)
        self.assertEqual([], issues)

    def test_pipe_escape_decodes_literal_pipe(self) -> None:
        self.assertEqual(["A | B", "C"], sv.cells(r"| A \| B | C |"))

    def test_command_prerequisite_cycle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/TECHNICAL_SPEC.alpha-ui.md"
            text = path.read_text().replace("| SETUP | python -m pip install -e . | (none) |", "| SETUP | python -m pip install -e . | RUN |", 1)
            path.write_text(text)
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "COMMAND_PREREQUISITE_CYCLE")

    def test_module_dependency_mirror_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/CODEBASE_SPEC.alpha-ui.md"
            text = path.read_text()
            text = text.replace("| MODULE_KEY | OWNED_PATHS | PUBLIC_ENTRYPOINTS | LAYER | RESPONSIBILITY |", "| MODULE_KEY | OWNED_PATHS | PUBLIC_ENTRYPOINTS | DEPENDS_ON_MODULES | LAYER | RESPONSIBILITY |", 1).replace("| MOD_APP | src/app/** | src/app/main.* | prod | application module |", "| MOD_APP | src/app/** | src/app/main.* | MOD_DAT | prod | application module |", 1)
            path.write_text(text)
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "DEPENDENCY_DUPLICATE_AUTHORITY")

    def test_dependency_allow_deny_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/CODEBASE_SPEC.alpha-ui.md"
            text = path.read_text()
            marker = "| MOD_APP | MOD_DAT | ALLOW | application persists state |\n"
            path.write_text(text.replace(marker, marker + "| MOD_APP | MOD_DAT | DENY | conflicting edge |\n", 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "DEPENDENCY_EDGE_CONFLICT")

    def test_slice_catalog_dependency_mirror_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/SLICES_MAP.alpha-ui.md"
            text = path.read_text().replace("| MIGRATION_KEYS | TOUCHED_SCREENS |", "| MIGRATION_KEYS | DEPENDENCY_KEYS | TOUCHED_SCREENS |", 1)
            path.write_text(text)
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "SLICE_DEPENDENCY_DUPLICATE")

    def test_slice_order_and_dependency_topology_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/SLICES_MAP.alpha-ui.md"
            text = path.read_text().replace("| SLI_002 | SLICE_002 | docs/slices/SLICE_002.alpha-ui.md | Fixture slice 2 | PRODUCT | 2 |", "| SLI_002 | SLICE_002 | docs/slices/SLICE_002.alpha-ui.md | Fixture slice 2 | PRODUCT | 1 |", 1)
            path.write_text(text)
            issues = sv.validate_project(workspace, ROOT, "alpha-ui")
            self.assert_has(issues, "SLICE_ORDER_DUP")
            self.assert_has(issues, "SLICE_DEPENDENCY_ORDER")

    def test_slice_coverage_projection_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/SLICES_MAP.alpha-ui.md"
            text = path.read_text().replace("| SCR_HOME | Status home | SLICE_001,SLICE_002 |", "| SCR_HOME | Status home | SLICE_001 |", 1)
            path.write_text(text)
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "SLICE_COVERAGE_MIRROR")

    def test_slice_dependency_input_mirrors_edge_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/slices/SLICE_002.alpha-ui.md"
            text = path.read_text().replace("| dependencies | SLICE_001 |", "| dependencies | (none) |", 1)
            path.write_text(text)
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "SLICE_DEPENDENCY_MIRROR")

    def test_narrow_screen_requirement_reference_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/UI_SPEC.alpha-ui.md"
            text = path.read_text()
            row = "| SCR_HOME | Status home | / | LAY_STATUS | IFACE_STATUS | DS-SCR-SCRRG-STAT_VIEW | entry screen |"
            replacement = "| SCR_HOME | Status home | / | LAY_STATUS | IFACE_STATUS | TS-ARC-ARCRG-LOCAL_ARCH | entry screen |"
            self.assertIn(row, text)
            path.write_text(text.replace(row, replacement, 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "TYPED_REF")

    def test_no_ui_project_rejects_ui_coverage_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/SLICES_MAP.beta-integration.md"
            text = path.read_text() + "\n### SM-COV-SCRCV || Screen coverage\n\n| SCREEN_KEY | COVERED_BY_SLICE_KEYS |\n| --- | --- |\n| SCR_FAKE | SLICE_001 |\n"
            path.write_text(text)
            self.assert_has(sv.validate_project(workspace, ROOT, "beta-integration"), "SLICE_UI_COVERAGE_NA")

    def test_manifest_timestamp_format_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SPEC_FILES_INDEX.alpha-ui.md"
            source = (self.workspace / "docs/SPEC_FILES_INDEX.alpha-ui.md").read_text()
            path.write_text(source.replace("2026-08-30T00:00:00Z", "2026-08-30", 1))
            _, issues = sv.parse_manifest(path, "alpha-ui", index=sv.parse_index(self.codegen / "SPEC_FILES_INDEX.specset.md"))
            self.assert_has(issues, "MANIFEST_TIMESTAMP")

    def test_patch_requires_accepted_specification_authority(self) -> None:
        text = (self.service / "PATCH_TODO.template.md").read_text()
        self.assertIn("Governing accepted specification IDs", text)
        self.assertIn("use the lifecycle's failure routing", text)
        self.assertIn("not an independent development lifecycle", text)
        self.assertIn("Semantic impact: `none`", text)

    def test_named_review_dimensions_replace_opaque_pass_codes(self) -> None:
        agent = (ROOT / "AGENTS.md").read_text()
        report = (self.service / "REPORT_GAP.template.md").read_text()
        self.assertIn("semantic_architecture", agent)
        self.assertIn("behavioral_qualification", report)
        self.assertNotIn("R1-R6", agent + report)

    def test_service_templates_do_not_duplicate_common_recovery_commands(self) -> None:
        forbidden = ("git stash", "git reset --hard", "git checkout .", "git clean")
        for path in self.service.glob("*.md"):
            text = path.read_text()
            for command in forbidden:
                self.assertNotIn(command, text, path)
            self.assertIn(".spec-set/AGENTS.md", text, path)

    def test_validator_report_declares_deterministic_scope(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(0, sv.report([], False))
        output = stream.getvalue()
        self.assertIn("DETERMINISTIC VALID", output)
        self.assertIn("semantic review not performed", output)

    def test_validator_json_report_declares_deterministic_scope(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(0, sv.report([], True))
        output = stream.getvalue()
        self.assertIn('"validation_scope": "deterministic_only"', output)
        self.assertIn('"semantic_review": "not_performed"', output)

    def test_invalid_project_slug_is_rejected(self) -> None:
        self.assert_has(sv.validate_project(self.workspace, ROOT, "Bad_Slug"), "PROJECT_SLUG")


if __name__ == "__main__":
    unittest.main()
