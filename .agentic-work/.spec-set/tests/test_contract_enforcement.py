from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
from tools import specset_validate as sv  # noqa: E402


class ContractEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.codegen = ROOT / ".templates/codegen"
        cls.index = sv.parse_index(cls.codegen / "SPEC_FILES_INDEX.specset.md")

    def assert_has(self, issues: list[sv.Issue], code: str) -> None:
        self.assertTrue(any(issue.code == code for issue in issues), [i.code for i in issues])

    def test_path_glob_supported_containment(self) -> None:
        cases = [
            ("src/app/main.py", ["src/app/**"], "contained"),
            ("src/app/test_*.py", ["src/app/*.py"], "contained"),
            ("src/app/foreign.js", ["src/app/*.py"], "not_contained"),
            ("src/a/[ab].py", ["src/a/[a-z].py"], "contained"),
            ("src/a/[a-z].py", ["src/a/[ab].py"], "not_contained"),
            ("src/a/b.py", ["src/?/b.py"], "contained"),
            ("src/a/b.py", ["src/*"], "not_contained"),
            ("src/**/x.py", ["src/**"], "contained"),
            ("src/app/[ab]*.py", ["src/app/a*.py", "src/app/b*.py"], "contained"),
            ("Src/app/a.py", ["src/**"], "not_contained"),
        ]
        for touched, owners, expected in cases:
            with self.subTest(touched=touched, owners=owners):
                self.assertEqual(expected, sv.glob_subset(touched, owners)[0])

    def test_path_glob_invalid_unsupported_and_unestablished_are_distinct(self) -> None:
        self.assertEqual("invalid", sv.path_glob_status("src/[abc")[0])
        self.assertEqual("unsupported", sv.path_glob_status("src/[!a]")[0])
        self.assertEqual("invalid", sv.path_glob_status("src/a**b")[0])
        self.assertEqual("unsupported", sv.path_glob_status("src/{a,b}")[0])
        self.assertEqual("unestablished", sv.glob_subset("src/**", ["src/**"], state_limit=0)[0])

    def test_slice_ownership_contract_declares_union_containment(self) -> None:
        contract, issues = sv.authoring_contract(self.codegen / "SLICE_XXX.specset.template.md")
        self.assertEqual([], issues)
        rule = next(item for item in contract["readiness"] if item.get("rule") == "field_ownership")
        self.assertEqual("path_language_subset_of_selected_owner_union", rule.get("containment"))
        self.assertEqual("invalid", rule.get("unestablished"))

    def test_field_ownership_uses_full_glob_containment_and_selected_union(self) -> None:
        rule = {
            "scope_context": "SX-INP-INPRT",
            "scope_field": "touched_modules",
            "path_field": "touched_files",
            "owner_document_key": "CS",
            "owner_context": "CS-MOD-MODRT",
            "owner_key_field": "MODULE_KEY",
            "owner_path_field": "OWNED_PATHS",
        }
        owner = sv.NormDoc(
            Path("codebase.md"),
            "materialized",
            [],
            [sv.MdTable(["MODULE_KEY", "OWNED_PATHS"], [["MOD_A", "src/app/a*.py"], ["MOD_B", "src/app/b*.py"]], 1, "CS-MOD-MODRT")],
            {}, {}, [],
        )

        def issues_for(touched: str, scopes: str = "MOD_A,MOD_B") -> list[sv.Issue]:
            slice_doc = sv.NormDoc(
                Path("slice.md"),
                "materialized",
                [],
                [sv.MdTable(["FIELD", "VALUE"], [["touched_modules", scopes], ["touched_files", touched]], 1, "SX-INP-INPRT")],
                {}, {}, [],
            )
            issues: list[sv.Issue] = []
            sv._validate_field_ownership(slice_doc, {"CS": [owner]}, rule, issues)
            return issues

        self.assertEqual([], issues_for("src/app/[ab]*.py"))
        self.assert_has(issues_for("src/app/foreign.js", "MOD_A"), "SLICE_FILE_OWNER")
        self.assert_has(issues_for("src/app/[abc", "MOD_A"), "SLICE_FILE_OWNER_GLOB_INVALID")
        self.assert_has(issues_for("src/app/[!a]", "MOD_A"), "SLICE_FILE_OWNER_GLOB_UNSUPPORTED")

    def _write_manifest(self, path: Path, rows: list[str], generated_at: str = "2026-08-30T00:00:00Z", extra_project_rows: str = "") -> None:
        path.write_text(
            "# Spec Set Manifest: alpha-ui\n\n"
            "| FIELD | VALUE |\n| --- | --- |\n"
            "| project_slug | alpha-ui |\n"
            "| status | ready |\n"
            f"| generated_at | {generated_at} |\n"
            f"{extra_project_rows}\n"
            "| DOCUMENT_KEY | ROLE | PATH | STATUS | SOURCE_DOCS | UPSTREAM_DOCS | DOWNSTREAM_DOCS |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            + "\n".join(rows)
            + "\n",
            encoding="utf-8",
        )

    def test_manifest_slice_identity_uses_concrete_ordinal(self) -> None:
        expected = sv._manifest_expected_rows(
            self.index,
            "alpha-ui",
            {"SX": ["docs/slices/SLICE_001.alpha-ui.md", "docs/slices/SLICE_010.alpha-ui.md"]},
        )
        self.assertIn("SX:001", expected)
        self.assertIn("SX:010", expected)
        single = sv._manifest_expected_rows(
            self.index,
            "alpha-ui",
            {"SX": ["docs/slices/SLICE_010.alpha-ui.md"]},
        )
        self.assertIn("SX:010", single)
        self.assertNotIn("SX:001", single)

    def test_manifest_accepts_valid_nonconsecutive_slice_keys(self) -> None:
        rows = [
            "| SX:001 | normative_derived | docs/slices/SLICE_001.alpha-ui.md | ready | (none) | (none) | (none) |",
            "| SX:010 | normative_derived | docs/slices/SLICE_010.alpha-ui.md | ready | (none) | (none) | (none) |",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "valid.md"
            self._write_manifest(path, rows)
            _, issues = sv.parse_manifest(path, "alpha-ui", self.index)
            self.assertEqual([], issues)

    def test_manifest_rejects_bad_key_duplicate_rows_and_impossible_timestamp(self) -> None:
        base_row = "| SX:001 | normative_derived | docs/slices/SLICE_001.alpha-ui.md | ready | (none) | (none) | (none) |"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_key = root / "bad-key.md"
            self._write_manifest(bad_key, [base_row.replace("SX:001", "SX:099")])
            _, issues = sv.parse_manifest(bad_key, "alpha-ui", self.index)
            self.assert_has(issues, "MANIFEST_KEY_PATH")

            duplicate = root / "duplicate.md"
            self._write_manifest(duplicate, [base_row, base_row])
            _, issues = sv.parse_manifest(duplicate, "alpha-ui", self.index)
            self.assert_has(issues, "MANIFEST_ROW_DUP")
            self.assert_has(issues, "MANIFEST_KEY_DUP")
            self.assert_has(issues, "MANIFEST_PATH_DUP")

            duplicate_path = root / "duplicate-path.md"
            second_key_same_path = base_row.replace("SX:001", "SX:002")
            self._write_manifest(duplicate_path, [base_row, second_key_same_path])
            _, issues = sv.parse_manifest(duplicate_path, "alpha-ui", self.index)
            self.assert_has(issues, "MANIFEST_PATH_DUP")
            self.assert_has(issues, "MANIFEST_KEY_PATH")

            impossible = root / "impossible.md"
            self._write_manifest(impossible, [base_row], generated_at="2026-02-30T00:00:00Z")
            _, issues = sv.parse_manifest(impossible, "alpha-ui", self.index)
            self.assert_has(issues, "MANIFEST_TIMESTAMP")

            duplicate_field = root / "duplicate-field.md"
            self._write_manifest(duplicate_field, [base_row], extra_project_rows="| status | ready |")
            _, issues = sv.parse_manifest(duplicate_field, "alpha-ui", self.index)
            self.assert_has(issues, "MANIFEST_FIELD_DUP")

    def test_materialized_whole_word_tbd_and_registry_scope(self) -> None:
        base = (
            "# AA || Example\n\n"
            "## AA-BBB || Structure\n\n"
            "### AA-BBB-CCCC || Requirements\n\n"
            "| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |\n"
            "| --- | --- | --- | --- |\n"
            "| AA-BBB-CCCC-REQ_ITEM | Thing MUST work. | note | (origin) |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tbd = root / "tbd.md"
            tbd.write_text(base + "\nThe value is (TBD).\n", encoding="utf-8")
            self.assert_has(sv.parse_normative(tbd, "materialized").issues, "PLACEHOLDER")

            fenced = root / "fenced.md"
            fenced.write_text(base + "\n```text\nTBD\n| KEY | VALUE |\n| --- | --- |\n```\n", encoding="utf-8")
            self.assertFalse(any(i.code in {"PLACEHOLDER", "REG_REQUIREMENT_GROUP"} for i in sv.parse_normative(fenced, "materialized").issues))

            registry = root / "registry.md"
            registry.write_text(base + "\n| KEY | VALUE |\n| --- | --- |\n| A | B |\n", encoding="utf-8")
            self.assert_has(sv.parse_normative(registry, "materialized").issues, "REG_REQUIREMENT_GROUP")

            structural = root / "structural.md"
            structural.write_text(base + "\n## AA-DDD || Registry\n\n| KEY | VALUE |\n| --- | --- |\n| A | B |\n", encoding="utf-8")
            self.assertFalse(any(i.code == "REG_REQUIREMENT_GROUP" for i in sv.parse_normative(structural, "materialized").issues))

    def test_malformed_section_identifier_is_rejected_directly(self) -> None:
        source = "# AA || Example\n\n## AA-BBB || Structure\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "section.md"
            for mode in ("template", "materialized"):
                path.write_text(source, encoding="utf-8")
                self.assertEqual([], sv.parse_normative(path, mode).issues)
                path.write_text(source.replace("AA-BBB", "AA-BB"), encoding="utf-8")
                issues = sv.parse_normative(path, mode).issues
                self.assertEqual(["SECTION_ID"], [issue.code for issue in issues])
                self.assertEqual(3, issues[0].line)

    def test_malformed_requirement_identifier_is_rejected_directly(self) -> None:
        source = (
            "# AA || Example\n\n## AA-BBB || Structure\n\n### AA-BBB-CCCC || Requirements\n\n"
            "| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |\n"
            "| --- | --- | --- | --- |\n"
            "| AA-BBB-CCCC-REQ_ITEM | Thing MUST work. | note | (origin) |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirement.md"
            for mode in ("template", "materialized"):
                path.write_text(source, encoding="utf-8")
                self.assertEqual([], sv.parse_normative(path, mode).issues)
                path.write_text(source.replace("REQ_ITEM", "req_item"), encoding="utf-8")
                issues = sv.parse_normative(path, mode).issues
                self.assertEqual(["REQ_ID"], [issue.code for issue in issues])
                self.assertEqual(9, issues[0].line)

    def test_template_placeholder_tbd_remains_permitted(self) -> None:
        text = (
            "# AA || Example\n\n## AA-BBB || Structure\n\n### AA-BBB-CCCC || Requirements\n\n"
            "| REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM |\n"
            "| --- | --- | --- | --- |\n"
            "| AA-BBB-CCCC-REQ_TBD | Thing MUST describe {{thing}}. | note | (origin) |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.md"
            path.write_text(text, encoding="utf-8")
            self.assertEqual([], sv.parse_normative(path, "template").issues)

    def test_expected_input_failures_are_diagnostic_and_preserve_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.md"
            self.assert_has(sv.parse_normative(missing, "materialized").issues, "DOCUMENT_MISSING")

            invalid_contract = root / "contract.md"
            invalid_contract.write_text(
                "# AA || Example\n<!-- specset-authoring:start\n"
                '{"schema_version":1,"document_key":"AA","purpose":"x","authoritative_inputs":[],"non_goals":[],"readiness":null,"applicability":[],"typed_fields":[],"change_areas":[],"materialization":{},"semantic_review":[]}\n'
                "specset-authoring:end -->\n",
                encoding="utf-8",
            )
            before = hashlib.sha256(invalid_contract.read_bytes()).digest()
            contract, issues = sv.authoring_contract(invalid_contract)
            after = hashlib.sha256(invalid_contract.read_bytes()).digest()
            self.assertIsNotNone(contract)
            self.assertEqual([], contract["readiness"])
            self.assert_has(issues, "CONTRACT_FIELD")
            self.assertEqual(before, after)

            unsupported = root / "unsupported.md"
            unsupported.write_text(
                "# AA || Example\n<!-- specset-authoring:start\n{\"schema_version\":2}\nspecset-authoring:end -->\n",
                encoding="utf-8",
            )
            contract, issues = sv.authoring_contract(unsupported)
            self.assertIsNone(contract)
            self.assert_has(issues, "CONTRACT_VERSION")

    def test_missing_template_is_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MISSING.specset.template.md"
            contract, issues = sv.authoring_contract(path)
            self.assertIsNone(contract)
            self.assert_has(issues, "CONTRACT_FILE_MISSING")

    def test_unknown_graph_endpoint_is_reported_without_dependent_crash(self) -> None:
        source = (self.codegen / "SPEC_FILES_INDEX.specset.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.md"
            path.write_text(source.replace("| EDG_002 | PS | DS |", "| EDG_002 | ZZ | DS |", 1), encoding="utf-8")
            index = sv.parse_index(path)
            self.assert_has(index.issues, "INDEX_EDGE_KEY")
            upstream, _, _ = sv.expected_links(index, "DS")
            self.assertNotIn("ZZ", upstream)

    def test_text_and_json_reports_agree(self) -> None:
        issues = [sv.Issue("A", "x", 1, "one"), sv.Issue("B", "y", 2, "two")]
        text_stream = io.StringIO()
        json_stream = io.StringIO()
        with contextlib.redirect_stdout(text_stream):
            self.assertEqual(1, sv.report(issues, False))
        with contextlib.redirect_stdout(json_stream):
            self.assertEqual(1, sv.report(issues, True))
        payload = json.loads(json_stream.getvalue())
        self.assertFalse(payload["valid"])
        self.assertEqual(2, payload["issue_count"])
        self.assertEqual(2, len(payload["issues"]))
        self.assertIn("DETERMINISTIC INVALID: 2 issue(s)", text_stream.getvalue())

    def test_cli_expected_invalid_input_text_and_json_agree(self) -> None:
        validator = ROOT / "tools/specset_validate.py"
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.md"
            text_run = subprocess.run(
                [sys.executable, str(validator), "document", str(missing), "--mode", "materialized"],
                capture_output=True,
                text=True,
                check=False,
            )
            json_run = subprocess.run(
                [sys.executable, str(validator), "document", str(missing), "--mode", "materialized", "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, text_run.returncode)
            self.assertEqual(1, json_run.returncode)
            self.assertIn("DOCUMENT_MISSING", text_run.stdout)
            payload = json.loads(json_run.stdout)
            self.assertFalse(payload["valid"])
            self.assertEqual(1, payload["issue_count"])
            self.assertEqual("DOCUMENT_MISSING", payload["issues"][0]["code"])

    def test_unexpected_programming_failure_is_not_converted_to_validation_result(self) -> None:
        with mock.patch.object(sv, "validate_framework", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                sv.main(["framework"])

    def _dependency_issues(self, rows: list[list[str]]) -> list[sv.Issue]:
        rule = {
            "mode": "allowlist",
            "module_context": "CS-MOD-MODRT",
            "module_key_field": "MODULE_KEY",
            "edge_context": "CS-DEP-DEPRT",
            "edge_from_field": "FROM_MODULE",
            "edge_to_field": "TO_MODULE",
            "edge_rule_field": "EDGE_RULE",
            "allow_value": "ALLOW",
            "deny_value": "DENY",
            "self_edges": "implicit",
            "explicit_self_allow": "allowed_redundant",
            "explicit_self_deny": "forbidden",
        }
        doc = sv.NormDoc(
            Path("codebase.md"),
            "materialized",
            [],
            [
                sv.MdTable(["MODULE_KEY"], [["MOD_A"], ["MOD_B"]], 1, "CS-MOD-MODRT"),
                sv.MdTable(["FROM_MODULE", "TO_MODULE", "EDGE_RULE"], rows, 10, "CS-DEP-DEPRT"),
            ],
            {},
            {},
            [],
        )
        issues: list[sv.Issue] = []
        sv._validate_dependency_allowlist(doc, rule, issues)
        return issues

    def test_self_dependency_policy(self) -> None:
        self.assertEqual([], self._dependency_issues([]))
        self.assertEqual([], self._dependency_issues([["MOD_A", "MOD_A", "ALLOW"]]))
        self.assert_has(self._dependency_issues([["MOD_A", "MOD_A", "DENY"]]), "DEPENDENCY_SELF_DENY")
        self.assert_has(self._dependency_issues([["MOD_X", "MOD_X", "ALLOW"]]), "DEPENDENCY_MODULE")
        self.assert_has(self._dependency_issues([["MOD_A", "MOD_A", "ALLOW"], ["MOD_A", "MOD_A", "ALLOW"]]), "DEPENDENCY_EDGE_DUP")
        self.assert_has(self._dependency_issues([["MOD_A", "MOD_B", "ALLOW"], ["MOD_A", "MOD_B", "DENY"]]), "DEPENDENCY_EDGE_CONFLICT")


if __name__ == "__main__":
    unittest.main()
