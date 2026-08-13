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
    def test_p10_harness_uses_configured_path_and_fails_before_jobs_when_codex_is_unavailable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p10_wsl.sh").read_text(encoding="utf-8")
        blocked = text.index("P10_EXTERNAL_CODEX_BLOCKED=version-preflight")
        first_job = text.index("READ_JOB=")
        self.assertLess(blocked, first_job)
        self.assertIn('EXPECTED_PATH=', text)
        self.assertIn('CODEX_ENTRY="$(PATH="$EXPECTED_PATH" command -v codex || true)"', text)
        self.assertIn('configured Codex path is outside declared external roots', text)
        self.assertNotIn('CODEX_STANDALONE_ROOT', text)
        self.assertIn('CAP-121', (root / "specs/p10-codex-jobs.md").read_text(encoding="utf-8"))

    def test_p10_harness_qualifies_detached_read_write_cancel_and_restore(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p10_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('start_job read', text)
        self.assertIn('start_job write', text)
        self.assertIn('codex cancel', text)
        self.assertIn('instance restart', text)
        self.assertIn('FINAL_FINGERPRINT=', text)
        self.assertIn('P10 qualification changed desired-state fingerprint', text)
        self.assertIn('P10_WSL_QUALIFICATION=PASS', text)


class P11QualificationScriptTests(unittest.TestCase):
    def test_p11_harness_requires_exact_signature_and_timer_recovery(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p11_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('OnUnitActiveSec=30s', text)
        self.assertIn('maximum HTTP session count reached', text)
        self.assertIn('exc.code == 503', text)
        self.assertIn('error_payload["error"].get("message")', text)
        self.assertIn('The timer, not this script, must perform the first recovery.', text)
        self.assertIn('restart_attempt_count', text)
        self.assertIn('cooldown_suppression_count', text)
        self.assertIn('last_restart_result.unit', text)
        self.assertIn('Restart=always', text)
        self.assertIn('EVIDENCE_BACKUP=', text)

    def test_p11_harness_restores_baseline_and_checks_noop(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p11_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('instance update "$BASELINE"', text)
        self.assertIn('P11 qualification changed the final desired fingerprint', text)
        self.assertIn('final P11 plan is not NOOP', text)
        self.assertIn('guard timer remains after disabling P11', text)
        self.assertIn('P11_WSL_QUALIFICATION=PASS', text)


class P13QualificationScriptTests(unittest.TestCase):
    def test_p13_harness_never_invokes_real_bridge_without_check(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p13_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('"$BRIDGE" --check', text)
        self.assertNotIn('"$BRIDGE"\n', text)
        self.assertIn('MANAGER_PYTHON=', text)
        self.assertIn("P13_WSL_REBOOT_UNSUPPORTED=PASS", text)
        self.assertIn("P13_WSL_REBOOT=DEFERRED", text)
        self.assertIn("P13_NATIVE_LINUX_PHYSICAL_REBOOT_QUALIFICATION=NOT_RUN", text)
        self.assertIn("P13_REAL_BRIDGE_CHECK_RC=", text)

    def test_p13_harness_live_checks_checkpoint_ordering_and_failure_persistence(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p13_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('\"state\": \"prepared\"', text)
        self.assertIn('\"state\": \"requesting\"', text)
        self.assertIn("P13_AUTHORIZATION_FAILURE_PERSISTENCE=PASS", text)
        self.assertIn("P13_SYNCHRONOUS_REQUEST_FAILURE_PERSISTENCE=PASS", text)
        self.assertIn('host reboot-check', text)
        self.assertIn('PATH="$EXPECTED_PATH" command -v codex', text)
        self.assertIn("P13_WSL_NONDESTRUCTIVE_QUALIFICATION=PASS", text)


class P14QualificationScriptTests(unittest.TestCase):
    def test_p14_harness_uses_only_synthetic_legacy_target(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p14_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('LEGACY_ID="${LEGACY_ID:-p14-legacy-qual}"', text)
        self.assertIn('cleanup audit "$LEGACY_ID"', text)
        self.assertIn('cleanup execute "$LEGACY_ID"', text)
        self.assertIn("pre-existing legacy inventory changed", text)
        self.assertIn("P14_LEGACY_CLEANUP_QUALIFICATION=PASS", text)

    def test_p14_harness_proves_read_only_and_shared_resource_preservation(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p14_wsl.sh").read_text(encoding="utf-8")
        self.assertIn("P14_READ_ONLY_AUDIT=PASS", text)
        self.assertIn("P14_SHARED_RESOURCES_PRESERVED=PASS", text)
        self.assertIn('sha256sum "$SHARED_BIN"', text)
        self.assertIn('sha256sum "$RUNTIME_ENV"', text)
        self.assertIn('ops[:4] == ["STOP", "STOP", "DISABLE", "DISABLE"]', text)


class P15QualificationScriptTests(unittest.TestCase):
    def test_p15_harness_has_isolated_toolkit_and_final_gates(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p15_wsl.sh").read_text(encoding="utf-8")
        self.assertIn("P15_REAL_TOOLKIT_CHECK=PASS", text)
        self.assertIn("P15_TOOLKIT_INITIAL_INSTALL=PASS", text)
        self.assertIn("P15_TOOLKIT_NOOP=PASS", text)
        self.assertIn("P15_TOOLKIT_ATOMIC_SWITCH=PASS", text)
        self.assertIn('--prefix "$ISO_PREFIX"', text)
        self.assertIn('host tools audit', text)
        self.assertIn("P15_TOOLING_STATE=PASS", text)
        self.assertIn("P15_FINAL_CONVERGENCE=PASS", text)
        self.assertIn("P15_WSL_QUALIFICATION=PASS", text)


class P16QualificationScriptTests(unittest.TestCase):
    def test_p16_harness_qualifies_owned_launcher_and_dispatch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p16_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('LAUNCHER="$HOME/.local/bin/$INSTANCE_ID-mcp"', text)
        self.assertIn('# workspace-mcp-manager-instance=$INSTANCE_ID', text)
        self.assertIn('"$LAUNCHER" status', text)
        self.assertIn('"$LAUNCHER" plan', text)
        self.assertIn('"$LAUNCHER" access list', text)
        self.assertIn('"$LAUNCHER" host', text)
        self.assertIn("P16_LAUNCHER_RESOURCE=PASS", text)
        self.assertIn("P16_WSL_QUALIFICATION=PASS", text)


class P12QualificationScriptTests(unittest.TestCase):
    def test_p12_harness_uses_persisted_snapshots_and_real_tunnel_history(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p12_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('instance diagnose "$INSTANCE_ID" --since-seconds "$SHORT_WINDOW"', text)
        self.assertIn('instance diagnose "$INSTANCE_ID" --since-seconds "$LONG_WINDOW"', text)
        self.assertIn('tunnel_poll_timeout', text)
        self.assertIn('long window contains no poll timeout older than short window', text)
        self.assertNotIn('tunnel.log" >>', text)

    def test_p12_harness_saturates_locally_without_restart_and_cleans_sessions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_p12_wsl.sh").read_text(encoding="utf-8")
        self.assertIn('maximum HTTP session count reached', text)
        self.assertIn('P12_LOCAL_SATURATION=PASS', text)
        self.assertIn('local_mcp_5xx', text)
        self.assertIn('diagnostic restarted MCP', text)
        self.assertIn('diagnostic restarted tunnel', text)
        self.assertIn('delete_sessions "$SESSIONS"', text)
        self.assertIn('final manager plan is not all NOOP', text)
        self.assertIn('P12_RESILIENCE_DIAGNOSTICS_QUALIFICATION=PASS', text)


if __name__ == "__main__":
    unittest.main()
