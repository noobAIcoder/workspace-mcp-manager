from __future__ import annotations

from pathlib import Path
import unittest


class P6QualificationScriptTests(unittest.TestCase):
    def test_authority_check_matches_quoted_runtime_command(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p6_wsl.sh").read_text(encoding="utf-8")
        self.assertIn("grep -F '\"_runtime\" \"tunnel\"'", text)
        self.assertNotIn("grep -F '_runtime tunnel'", text)

    def test_harness_can_resume_after_successful_first_apply(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p6_wsl.sh").read_text(encoding="utf-8")
        self.assertIn("RESUME_AFTER_FIRST_APPLY=false", text)
        self.assertIn('if [ -f "$APPLIED" ]; then', text)
        self.assertIn('RESUME_AFTER_FIRST_APPLY=true', text)
        self.assertIn('existing applied state is not resumable present+running state', text)
        self.assertIn('previous first apply remains ready', text)


class P8QualificationScriptTests(unittest.TestCase):
    def test_p8_harness_has_prepare_and_cleanup_gates(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p8_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('workspace-mcp-manager access add-ro "$INSTANCE_ID" p8-ro', text)
        self.assertIn('workspace-mcp-manager access add-rw "$INSTANCE_ID" p8-rw', text)
        self.assertIn('workspace-mcp-manager access remove "$INSTANCE_ID" p8-ro', text)
        self.assertIn('workspace-mcp-manager access remove "$INSTANCE_ID" p8-rw', text)
        self.assertIn("P8_HOST_PREPARED=PASS", text)
        self.assertIn("P8_HOST_CLEANUP=PASS", text)

    def test_p8_harness_checks_git_and_generated_bind_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p8_wsl.sh").read_text(encoding="utf-8")
        self.assertIn("git -C \"$WORKSPACE\" status --porcelain --untracked-files=all", text)
        self.assertIn("PrivateUsers=true", text)
        self.assertIn("BindReadOnlyPaths=", text)
        self.assertIn("BindPaths=", text)
        self.assertIn('grep -F \'"operation":"REMOVE"\'', text)


class P9QualificationScriptTests(unittest.TestCase):
    def test_p9_harness_uses_temporary_remote_and_cleans_it(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p9_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('git -C "$WORKSPACE" remote add "$REMOTE_NAME" "$REMOTE_URL"', text)
        self.assertIn('git -C "$WORKSPACE" remote remove "$REMOTE_NAME"', text)
        self.assertIn('MANAGER_BIN="${MANAGER_BIN:-$HOME/.local/bin/workspace-mcp-manager}"', text)
        self.assertIn('"$MANAGER_BIN" instance git "$INSTANCE_ID"', text)
        self.assertIn("P9_HOST_QUALIFICATION=PASS", text)

    def test_p9_harness_checks_real_home_path_remote_and_auth(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p9_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('payload.get("environment", {}).get("home") == real_home', text)
        self.assertIn('payload.get("environment", {}).get("path") == expected_path', text)
        self.assertIn('remote.get("reachable") is True', text)
        self.assertIn('github.get("authenticated") is True', text)
        self.assertIn('"url" not in remote', text)


class P10QualificationScriptTests(unittest.TestCase):
    def test_p10_harness_fails_before_mutation_when_standalone_codex_is_unavailable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p10_wsl.sh").read_text(encoding="utf-8")
        blocked = text.index("P10_EXTERNAL_CODEX_BLOCKED=version-preflight")
        mutation = text.index('"$MANAGER" instance update "$UPDATED"')
        self.assertLess(blocked, mutation)
        self.assertIn('CODEX_ENTRY="${CODEX_ENTRY:-$CODEX_BIN_DIR/codex}"', text)
        self.assertIn('CAP-121', (root / "specs/p10-codex-jobs.md").read_text(encoding="utf-8"))

    def test_p10_harness_qualifies_detached_read_write_cancel_and_restore(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p10_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('start_job read', text)
        self.assertIn('start_job write', text)
        self.assertIn('codex cancel', text)
        self.assertIn('instance restart', text)
        self.assertIn('instance update "$BASELINE"', text)
        self.assertIn('P10_WSL_QUALIFICATION=PASS', text)


if __name__ == "__main__":
    unittest.main()
