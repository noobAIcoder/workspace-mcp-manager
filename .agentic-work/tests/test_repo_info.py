"""Integration-owned repo-info contract tests; never materialize project facts."""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agentic_repo_info_validate", ROOT / "tools/repo_info_validate.py")
assert SPEC and SPEC.loader
rv = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rv
SPEC.loader.exec_module(rv)

FRAMEWORK_DISTRIBUTION_DIRS = (
    ".work-models",
    ".execution-models",
    ".spec-set",
    ".templates",
    "tools",
    "tests",
)


def framework_distribution_paths(root: Path) -> set[Path]:
    """Enumerate framework-owned installation files, not repository-owned root facts."""
    paths = {Path("AGENTS.md")} if (root / "AGENTS.md").is_file() else set()
    for name in FRAMEWORK_DISTRIBUTION_DIRS:
        base = root / name
        if base.is_dir():
            paths.update(path.relative_to(root) for path in base.rglob("*") if path.is_file())
    return paths


def materialized_repo_info_targets() -> set[Path]:
    return {Path(target) for target, _ in rv.TEMPLATES.values()}


class RepoInfoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = rv.load_catalog(ROOT)

    def sample(self, name: str) -> dict:
        return copy.deepcopy(self.catalog[name][1])

    def codes(self, data: dict, name: str) -> set[str]:
        return {issue.code for issue in rv.validate_document(data, name, self.catalog)}

    def test_all_five_templates_parse_and_match_supported_versions(self) -> None:
        self.assertEqual(5, len(self.catalog))
        for name, (_, document) in self.catalog.items():
            self.assertEqual([], rv.validate_document(document, name, self.catalog))
            self.assertTrue(document["schema"].endswith(".v2"))

    def test_fact_templates_retain_observed_roles_and_provenance(self) -> None:
        for _, doc in self.catalog.values():
            self.assertTrue(doc["role"].startswith("observed_"))
            self.assertEqual("unverified", doc["provenance"]["status"])
            for key in ("repository_commit", "verified_at", "environment", "evidence_source"):
                self.assertIn(key, doc["provenance"])
                self.assertIsNone(doc["provenance"][key])

    def test_claim_scope_coverage_transferred_from_spec_set(self) -> None:
        command = self.sample("validation")["commands"][0]
        for field in ("evidence_kind", "establishes", "does_not_establish", "observed_success_signal", "reproducibility"):
            self.assertIn(field, command)

    def test_normalized_hazard_coverage_transferred_from_spec_set(self) -> None:
        doc = self.sample("hazards")
        self.assertIn("views", doc)
        for key in ("kind", "severity", "severity_scale_ref", "matcher", "evidence"):
            self.assertIn(key, doc["hazards"][0])
        self.assertEqual({"type", "value"}, set(doc["hazards"][0]["matcher"]))
        self.assertNotIn("matcher_semantics", doc)
        self.assertNotIn("dangerous_commands", doc)

    def test_repo_facts_do_not_claim_intended_spec_authority(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("observed implementation evidence is drift", text)
        self.assertIn("accepted specifications", text)
        for _, doc in self.catalog.values():
            for key in ("policy", "selection_policy", "acceptance", "defaults", "default_rule", "maintenance", "validation_minimum"):
                self.assertNotIn(key, doc)

    def test_readme_is_human_facing_and_non_authoritative(self) -> None:
        text = (ROOT / ".templates/repo-info/README.template.md").read_text(encoding="utf-8")
        self.assertIn("Human-facing template", text)
        self.assertIn("not by this README", text)
        self.assertNotIn("Coding agents MUST", text)

    def test_exact_shard_mappings(self) -> None:
        doc = self.sample("repo_info")
        for name in ("runtime", "structure", "validation", "hazards"):
            self.assertEqual(name + ".yaml", doc["shards"][name]["file"])
            self.assertEqual(self.catalog[name][1]["schema"], doc["shards"][name]["schema"])

    def test_old_and_future_versions_are_rejected_before_payload(self) -> None:
        for name in self.catalog:
            for version in ("v1", "v99"):
                doc = {"schema": self.sample(name)["schema"].rsplit(".", 1)[0] + "." + version}
                self.assertEqual({"UNSUPPORTED_SCHEMA"}, self.codes(doc, name))

    def test_wrong_schema_family_is_rejected(self) -> None:
        self.assertEqual({"UNSUPPORTED_SCHEMA"}, self.codes(self.sample("runtime"), "hazards"))

    def test_missing_required_key_is_rejected(self) -> None:
        doc = self.sample("runtime")
        del doc["provenance"]["environment"]
        self.assertIn("MISSING_FIELD", self.codes(doc, "runtime"))

    def test_extra_legacy_policy_is_rejected(self) -> None:
        doc = self.sample("structure")
        doc["defaults"] = {"delete_policy": "safe"}
        self.assertIn("UNKNOWN_FIELD", self.codes(doc, "structure"))

    def test_invalid_field_type_is_rejected(self) -> None:
        doc = self.sample("runtime")
        doc["surfaces"][0]["port"] = True
        self.assertIn("FIELD_TYPE", self.codes(doc, "runtime"))

    def test_bad_enum_is_rejected(self) -> None:
        doc = self.sample("hazards")
        doc["hazards"][0]["matcher"]["type"] = "execute"
        self.assertIn("FIELD_VALUE", self.codes(doc, "hazards"))

    def test_empty_provenance_status_is_not_a_supported_status(self) -> None:
        doc = self.sample("runtime")
        doc["provenance"]["status"] = ""
        self.assertIn("FIELD_VALUE", self.codes(doc, "runtime"))

    def test_empty_authority_kind_is_not_a_source_type(self) -> None:
        doc = self.sample("runtime")
        doc["authority_refs"][0]["kind"] = ""
        self.assertIn("FIELD_VALUE", self.codes(doc, "runtime"))

    def test_unsupported_shard_declaration_is_rejected(self) -> None:
        doc = self.sample("repo_info")
        doc["shards"]["runtime"]["schema"] = "repo_runtime.v1"
        self.assertIn("SHARD_SCHEMA", self.codes(doc, "repo_info"))

    def test_shard_path_escape_is_rejected(self) -> None:
        doc = self.sample("repo_info")
        doc["shards"]["runtime"]["file"] = "../runtime.yaml"
        self.assertIn("SHARD_PATH", self.codes(doc, "repo_info"))

    def test_verified_provenance_requires_concrete_metadata(self) -> None:
        doc = self.sample("runtime")
        doc["provenance"]["status"] = "verified"
        self.assertIn("PROVENANCE_INCOMPLETE", self.codes(doc, "runtime"))
        self.assertIn("VERIFIED_PLACEHOLDER", self.codes(doc, "runtime"))

    def test_verified_provenance_checks_commit_and_timestamp(self) -> None:
        doc = self.sample("runtime")
        doc["provenance"].update(status="verified", repository_commit="not-a-sha", verified_at="yesterday", environment="test", evidence_source="inspection")
        self.assertIn("PROVENANCE_FORMAT", self.codes(doc, "runtime"))

    def test_dangling_local_authority_reference_is_rejected(self) -> None:
        doc = self.sample("structure")
        doc["paths"][0]["policy_refs"] = ["missing"]
        self.assertIn("AUTHORITY_REFERENCE", self.codes(doc, "structure"))

    def test_duplicate_registry_id_is_rejected(self) -> None:
        doc = self.sample("runtime")
        doc["commands"][0]["id"] = "run"
        doc["commands"].append(copy.deepcopy(doc["commands"][0]))
        self.assertIn("DUPLICATE_ID", self.codes(doc, "runtime"))

    def test_duplicate_yaml_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.yaml"
            path.write_text("schema: one\nschema: two\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                rv.read_yaml(path)

    def test_missing_optional_shards_do_not_block_index_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "REPO_INFO.yaml").write_text(json.dumps(self.sample("repo_info")), encoding="utf-8")
            self.assertEqual([], rv.validate_installed(root, self.catalog, []))
            codes = {i.code for i in rv.validate_installed(root, self.catalog, ["runtime"])}
            self.assertEqual({"CONTEXT_MISSING"}, codes)

    def test_unsupported_index_does_not_load_shards_or_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "REPO_INFO.yaml"
            content = "schema: repo_info.v1\nkeep_me: original\n"
            path.write_text(content, encoding="utf-8")
            self.assertEqual(["UNSUPPORTED_SCHEMA"], [i.code for i in rv.validate_installed(root, self.catalog, ["runtime"])])
            self.assertEqual(content, path.read_text(encoding="utf-8"))
            self.assertEqual([path], list(root.iterdir()))

    def test_bad_optional_registration_does_not_hide_independent_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = self.sample("repo_info")
            index["shards"]["hazards"]["schema"] = "repo_hazards.v99"
            runtime = self.sample("runtime")
            runtime["surfaces"][0]["port"] = True
            (root / "REPO_INFO.yaml").write_text(json.dumps(index), encoding="utf-8")
            (root / "runtime.yaml").write_text(json.dumps(runtime), encoding="utf-8")
            codes = {i.code for i in rv.validate_installed(root, self.catalog, ["runtime"])}
            self.assertEqual({"SHARD_SCHEMA", "FIELD_TYPE"}, codes)

    def test_unsupported_shard_registration_is_not_interpreted_or_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = self.sample("repo_info")
            index["shards"]["runtime"]["schema"] = "repo_runtime.v1"
            (root / "REPO_INFO.yaml").write_text(json.dumps(index), encoding="utf-8")
            path = root / "runtime.yaml"
            original = "legacy, deliberately not a v2 payload"
            path.write_text(original, encoding="utf-8")
            self.assertEqual(["SHARD_SCHEMA"], [i.code for i in rv.validate_installed(root, self.catalog, ["runtime"])])
            self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_template_validation_does_not_require_or_create_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "arbitrary-container-name"
            shutil.copytree(ROOT / ".templates/repo-info", root / ".templates/repo-info")
            before = {p.relative_to(root) for p in root.rglob("*")}
            catalog = rv.load_catalog(root)
            self.assertEqual(5, len(catalog))
            self.assertEqual(before, {p.relative_to(root) for p in root.rglob("*")})

    def test_no_distributed_materialized_fact_files(self) -> None:
        overlap = materialized_repo_info_targets() & framework_distribution_paths(ROOT)
        self.assertEqual(set(), overlap, sorted(str(path) for path in overlap))

    def test_installed_materialized_repo_info_is_not_distribution_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".agentic-work"
            root.mkdir()
            shutil.copy2(ROOT / "AGENTS.md", root / "AGENTS.md")
            shutil.copytree(ROOT / ".templates", root / ".templates")
            shutil.copytree(ROOT / "tools", root / "tools")
            catalog = rv.load_catalog(root)
            for name, (target, _) in rv.TEMPLATES.items():
                (root / target).write_text(json.dumps(catalog[name][1]), encoding="utf-8")
            targets = materialized_repo_info_targets()
            self.assertTrue(all((root / target).is_file() for target in targets))
            self.assertEqual(set(), targets & framework_distribution_paths(root))
            self.assertEqual([], rv.validate_installed(root, catalog, ["runtime", "structure", "validation", "hazards"]))


if __name__ == "__main__":
    unittest.main()
