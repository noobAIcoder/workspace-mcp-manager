from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import specset_validate as sv  # noqa: E402


class SpecSetReviewRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = ROOT / "tests/fixtures/specset/workspace"
        cls.codegen = ROOT / ".templates/codegen"
        cls.service = ROOT / ".templates/service"

    def assert_has(self, issues: list[sv.Issue], code: str) -> None:
        self.assertTrue(any(issue.code == code for issue in issues), [i.code for i in issues])

    def copy_workspace(self, destination: Path) -> Path:
        workspace = destination / "workspace"
        shutil.copytree(self.workspace, workspace)
        return workspace

    def copy_framework(self, destination: Path) -> Path:
        root = destination / "repo"
        root.mkdir()
        shutil.copy2(ROOT / "AGENTS.md", root / "AGENTS.md")
        shutil.copytree(ROOT / ".templates", root / ".templates")
        shutil.copytree(ROOT / "tools", root / "tools")
        shutil.copytree(ROOT / "tests", root / "tests")
        return root

    def test_required_sections_do_not_claim_concrete_content_mechanics(self) -> None:
        for name in ("PRODUCT_SPEC.specset.template.md", "DOMAIN_SPEC.specset.template.md", "TECHNICAL_SPEC.specset.template.md", "CODEBASE_SPEC.specset.template.md", "SLICES_MAP.specset.template.md", "SLICE_XXX.specset.template.md"):
            contract, issues = sv.authoring_contract(self.codegen / name)
            self.assertEqual([], issues, name)
            for rule in contract["readiness"]:
                if rule.get("rule") == "required_sections":
                    self.assertNotIn("content", rule, name)

    def test_manifest_required_fields_are_consumed_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "SPEC_FILES_INDEX.specset.md"
            text = (self.codegen / "SPEC_FILES_INDEX.specset.md").read_text()
            marker = "| MF-PRJ | project_slug,status,generated_at | project_identity |"
            self.assertIn(marker, text)
            index_path.write_text(text.replace(marker, "| MF-PRJ | project_slug,status,generated_at,build_id | project_identity |", 1))
            index = sv.parse_index(index_path)
            _, issues = sv.parse_manifest(self.workspace / "docs/SPEC_FILES_INDEX.alpha-ui.md", "alpha-ui", index=index)
            self.assertTrue(any(issue.code == "MANIFEST_FIELD" and "build_id" in issue.message for issue in issues), issues)

    def test_global_link_field_is_required_from_index_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/DOMAIN_SPEC.alpha-ui.md"
            text = path.read_text()
            row = "| source_docs | (none) |\n"
            path.write_text(text.replace(row, "", 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "GLOBAL_FIELD_MISSING")

    def test_document_graph_is_owned_by_index_edges(self) -> None:
        index = sv.parse_index(self.codegen / "SPEC_FILES_INDEX.specset.md")
        self.assertEqual(["PS"], sv.downstream_review_keys(index, "PB"))
        self.assertEqual(["TS", "UI", "CS", "SM", "SX"], sv.downstream_review_keys(index, "DS"))
        self.assertFalse(any("materialized_path" in (self.codegen / name).read_text() for name in ("PRODUCT_SPEC.specset.template.md", "DOMAIN_SPEC.specset.template.md", "TECHNICAL_SPEC.specset.template.md", "UI_SPEC.specset.template.md", "CODEBASE_SPEC.specset.template.md", "SLICES_MAP.specset.template.md", "SLICE_XXX.specset.template.md")))

    def test_slice_dependency_graph_has_one_owner(self) -> None:
        map_text = (self.codegen / "SLICES_MAP.specset.template.md").read_text()
        slice_text = (self.codegen / "SLICE_XXX.specset.template.md").read_text()
        self.assertNotIn("DEPENDENCY_KEYS", map_text.split("specset-authoring:end -->", 1)[1].split("## SM-DEP", 1)[0])
        self.assertIn("SM-DEP-EDGRT", map_text)
        self.assertIn('"rule": "slice_dependency_inputs"', slice_text)

    def test_slice_dependency_mirror_detects_stale_slice_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/slices/SLICE_002.alpha-ui.md"
            path.write_text(path.read_text().replace("| dependencies | SLICE_001 |", "| dependencies | (none) |", 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "SLICE_DEPENDENCY_MIRROR")

    def test_acceptance_mapping_requires_materialized_test_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/slices/SLICE_001.alpha-ui.md"
            text = path.read_text()
            row = "**Registry key:** `TST_A001`\n"
            path.write_text(text.replace(row, "", 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "SLICE_TEST_DETAIL_MISSING")

    def test_slice_file_ownership_is_directional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/slices/SLICE_001.alpha-ui.md"
            row = "| touched_files | src/app/**,src/data/**,tests/** |"
            path.write_text(path.read_text().replace(row, "| touched_files | src/**,src/data/**,tests/** |", 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "SLICE_FILE_OWNER")

    def test_required_slice_registry_header_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/slices/SLICE_001.alpha-ui.md"
            header = "| CHANGE_KEY | CHANGE_KIND | SUMMARY | REFERENCES | DETAILS |"
            path.write_text(path.read_text().replace(header, "| CHANGE_ID | CHANGE_KIND | SUMMARY | REFERENCES | DETAILS |", 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "REGISTRY_SCHEMA")

    def test_unknown_typed_discriminator_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.copy_workspace(Path(tmp))
            path = workspace / "docs/CODEBASE_SPEC.alpha-ui.md"
            row = "| TS-ARC-ARCRG-LOCAL_ARCH | module | MOD_APP | architecture target |"
            path.write_text(path.read_text().replace(row, "| TS-ARC-ARCRG-LOCAL_ARCH | mystery | MOD_APP | architecture target |", 1))
            self.assert_has(sv.validate_project(workspace, ROOT, "alpha-ui"), "TYPED_DISCRIMINATOR")

    def test_unknown_deterministic_readiness_rule_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.copy_framework(Path(tmp))
            path = root / ".templates/codegen/PRODUCT_SPEC.specset.template.md"
            text = path.read_text()
            marker = '{"rule": "ancestry_mode", "value": "origin"}'
            path.write_text(text.replace(marker, '{"rule": "future_deterministic_rule"}', 1))
            self.assert_has(sv.validate_framework(root), "READINESS_RULE_UNSUPPORTED")

    def test_unknown_product_brief_readiness_rule_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.copy_framework(Path(tmp))
            path = root / ".templates/codegen/PRODUCT_BRIEF.specset.template.md"
            text = path.read_text()
            marker = '{"rule": "forbid_readiness_gates"}'
            self.assertIn(marker, text)
            path.write_text(text.replace(marker, '{"rule": "future_brief_rule"}', 1))
            self.assert_has(sv.validate_framework(root), "BRIEF_READINESS_UNSUPPORTED")

    def test_applicability_vocabulary_is_central_and_used(self) -> None:
        index_text = (self.codegen / "SPEC_FILES_INDEX.specset.md").read_text()
        self.assertIn("optional_omittable", index_text)
        self.assertIn("required_with_not_applicable_branch", index_text)
        for name in ("PRODUCT_BRIEF.specset.template.md", "DOMAIN_SPEC.specset.template.md", "UI_SPEC.specset.template.md"):
            contract, issues = sv.authoring_contract(self.codegen / name)
            self.assertEqual([], issues)
            self.assertIn("applicability", contract)

    def test_classification_axes_are_orthogonal(self) -> None:
        agent = (ROOT / "AGENTS.md").read_text()
        self.assertIn("semantic_impact", agent)
        self.assertIn("work_kind", agent)
        self.assertIn("scope", agent)
        spec = (self.service / "SPEC_PRE.template.md").read_text()
        self.assertIn("Semantic impact / work kind / scope class:", spec)

    def test_slice_preflight_distinguishes_implementable_delta(self) -> None:
        text = (self.service / "SLICE_PRE.template.md").read_text()
        self.assertIn("MATCHES", text)
        self.assertIn("IMPLEMENTABLE_DELTA", text)
        self.assertIn("BLOCKING_DIVERGENCE", text)
        self.assertIn("Overall `READY` requires artifact and repository readiness plus the applicable lifecycle gate", text)
        self.assertIn("readiness alone is not acceptance", text)

    def test_spec_workflow_does_not_require_undefined_cr_namespace(self) -> None:
        for name in ("SPEC_PRE.template.md", "SPEC_TODO.template.md"):
            text = (self.service / name).read_text()
            self.assertNotIn("Governing CR", text, name)
            self.assertNotIn("new CR required", text, name)


if __name__ == "__main__":
    unittest.main()
