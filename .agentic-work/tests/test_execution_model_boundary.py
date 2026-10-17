"""Regression coverage for the external Execution Model activation boundary."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = INSTALL_ROOT.parent
EXECUTION_ROOT = INSTALL_ROOT / ".execution-models"
EXECUTION_GUARD = EXECUTION_ROOT / "AGENTS.md"

CANONICAL_MODELS = {
    Path("ChatGPT+GitHub/ChatGPT+GitHub_execution_model.md"): "# ChatGPT + GitHub Execution Model",
    Path("ChatGPT+MCP+GitHub/ChatGPT+MCP+GitHub_execution_model.md"): "# ChatGPT + MCP + GitHub Execution Model",
    Path("ChatGPT+MCP+Codex+GitHub/ChatGPT+MCP+Codex+GitHub_execution_model.md"): "# ChatGPT + MCP + Codex + GitHub Execution Model",
    Path("ChatGPT+MCP+RLM+Codex+GitHub/ChatGPT+MCP+RLM+Codex+GitHub_execution_model.md"): "# ChatGPT + MCP + RLM + Codex + GitHub Execution Model",
}

DISTRIBUTED_AGENTS = (
    REPO_ROOT / "AGENTS.md",
    INSTALL_ROOT / "AGENTS.md",
    INSTALL_ROOT / ".work-models/AGENTS.md",
    INSTALL_ROOT / ".spec-set/AGENTS.md",
    EXECUTION_GUARD,
)

LOCAL_LINK = re.compile(r"\]\((?!https?://|#)[^)]+\)")
FORBIDDEN_RUNTIME_PATHS = (
    ".agentic-work/",
    ".execution-models/",
    ".work-models/",
)

REMOVED_MODEL_PATHS = (
    "ChatGPT+MCP+Codex/ChatGPT+MCP+Codex_execution_model.md",
    "ChatGPT+MCP+RLM+Codex/ChatGPT+MCP+RLM+Codex_execution_model.md",
)


class ExecutionModelBoundaryTests(unittest.TestCase):
    def test_distributed_agents_do_not_reference_execution_models(self) -> None:
        for path in DISTRIBUTED_AGENTS:
            self.assertTrue(path.is_file(), path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(".execution-models/", text, path)
            for model in CANONICAL_MODELS:
                self.assertNotIn(model.name, text, path)

    def test_execution_model_guard_and_canonical_models_are_distributed(self) -> None:
        self.assertTrue(EXECUTION_ROOT.is_dir())
        self.assertTrue((EXECUTION_ROOT / "README.md").is_file())
        self.assertTrue(EXECUTION_GUARD.is_file())
        self.assertEqual("DO NOT READ THIS FOLDER", EXECUTION_GUARD.read_text(encoding="utf-8").strip())
        for model in CANONICAL_MODELS:
            self.assertTrue((EXECUTION_ROOT / model).is_file(), model)

    def test_catalog_matches_canonical_inventory(self) -> None:
        catalog = (EXECUTION_ROOT / "README.md").read_text(encoding="utf-8")
        for model in CANONICAL_MODELS:
            self.assertIn(f"]({model.as_posix()})", catalog, model)
        for removed in REMOVED_MODEL_PATHS:
            self.assertNotIn(removed, catalog)
            self.assertFalse((EXECUTION_ROOT / removed).exists(), removed)

    def test_canonical_execution_model_identity_matches_path(self) -> None:
        for model, expected_title in CANONICAL_MODELS.items():
            path = EXECUTION_ROOT / model
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(expected_title, first_line, model)

    def test_canonical_execution_models_are_standalone_project_sources(self) -> None:
        model_names = {model.name for model in CANONICAL_MODELS}
        for model in CANONICAL_MODELS:
            path = EXECUTION_ROOT / model
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(LOCAL_LINK.search(text), model)
            for fragment in FORBIDDEN_RUNTIME_PATHS:
                self.assertNotIn(fragment, text, model)
            self.assertNotIn("AGENTS.md", text, model)
            for sibling in model_names - {model.name}:
                self.assertNotIn(sibling, text, model)

    def test_work_model_docs_do_not_load_execution_models_from_framework_paths(self) -> None:
        for relative in (
            "AGENTS.md",
            "WORK_MODELS.md",
            "README.md",
            "flows/SDD_TDD.md",
        ):
            path = INSTALL_ROOT / ".work-models" / relative
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(".execution-models/", text, path)


if __name__ == "__main__":
    unittest.main()
