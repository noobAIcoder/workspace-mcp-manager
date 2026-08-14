from __future__ import annotations

import unittest
from pathlib import Path


class Pm31QualificationScriptTests(unittest.TestCase):
    def test_script_covers_public_contracts_regressions_and_final_gate(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_pm3_1_wsl.sh").read_text(encoding="utf-8")
        required = (
            "instance discover",
            "instance candidate",
            "host ports",
            "tests.test_pm3_1_setup",
            "tests.test_tui_textual",
            "qualify_pm3_wsl.sh",
            "qualify_pm2_wsl.sh",
            "PM3_1_SPECIFICATION=PASS",
            "PM3_1_ELECTROCAD_DRY_RUN=PASS",
            "PM3_1_WORKSPACE_DISCOVERY=PASS",
            "PM3_1_WORKSPACE_IDENTITY=PASS",
            "PM3_1_INSTANCE_SELECTION=PASS",
            "PM3_1_CANDIDATE_REQUEST=PASS",
            "PM3_1_CANDIDATE_AUTHORITY=PASS",
            "PM3_1_CANDIDATE_PROVENANCE=PASS",
            "PM3_1_CANDIDATE_FRESHNESS=PASS",
            "PM3_1_COPY_POLICY=PASS",
            "PM3_1_DIRECTORY_PICKER=PASS",
            "PM3_1_PORT_PROJECTION=PASS",
            "PM3_1_ENDPOINT_COLLISION_MODEL=PASS",
            "PM3_1_UNKNOWN_LISTENER_SAFETY=PASS",
            "PM3_1_PORT_RECOMMENDATION=PASS",
            "PM3_1_TUNNEL_PROFILE_ABSTRACTION=PASS",
            "PM3_1_GIT_DISCOVERY=PASS",
            "PM3_1_GIT_ADOPTION_POLICY=PASS",
            "PM3_1_GIT_PROVENANCE=PASS",
            "PM3_1_ACCESS_PROVENANCE=PASS",
            "PM3_1_AUTH_STATUS_CONTRACT=PASS",
            "PM3_1_EXTERNAL_AUTH_UX=PASS",
            "PM3_1_WIZARD_REFINEMENT=PASS",
            "PM3_1_DIRTY_FIELD_SEMANTICS=PASS",
            "PM3_1_CLIPBOARD_UX=PASS",
            "PM3_1_VERSION_PROJECTION=PASS",
            "PM3_1_PURE_VERIFICATION=PASS",
            "PM3_1_TEXTUAL_PILOT=PASS",
            "PM3_1_LIVE_WSL_QUALIFICATION=PASS",
            "PM3_1_PM3_REGRESSION=PASS",
            "PM3_1_PM2_REGRESSION=PASS",
            "PM3_1_CONTEXT_AWARE_UX_QUALIFICATION=PASS",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_script_does_not_mutate_external_authentication(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/qualify_pm3_1_wsl.sh").read_text(encoding="utf-8")
        forbidden = (
            "gh auth login",
            "gh auth logout",
            "ssh-add ",
            "CONTROL_PLANE_API_KEY=",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
