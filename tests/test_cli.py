from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from workspace_mcp_manager.cli import _payload_exit_code, build_parser, main

from tests.helpers import sample_instance


class CliTests(unittest.TestCase):
    def test_version_flag_reports_package_version(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "workspace-mcp-manager 0.2.0")

    def test_structured_false_result_returns_semantic_failure_exit(self) -> None:
        self.assertEqual(_payload_exit_code({"ok": False}), 1)
        self.assertEqual(_payload_exit_code({"ok": True}), 0)
        self.assertEqual(_payload_exit_code({"status": "observed"}), 0)

    def test_validate_outputs_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps(sample_instance()), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["instance", "validate", str(path)])
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["instance_id"], "sample")
            self.assertEqual(len(payload["fingerprint"]), 64)

    def test_invalid_config_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            value = sample_instance()
            value["unknown"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            output = StringIO()
            with redirect_stderr(output):
                exit_code = main(["instance", "validate", str(path)])
            self.assertEqual(exit_code, 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["error"]["code"], "CONFIG_INVALID")

    def test_access_command_surface_parses_all_p8_operations(self) -> None:
        parser = build_parser()
        listed = parser.parse_args(["access", "list", "manager-qual"])
        self.assertEqual((listed.area, listed.command, listed.instance_id), ("access", "list", "manager-qual"))
        added = parser.parse_args(
            ["access", "add-ro", "manager-qual", "docs", "/srv/shared/docs"]
        )
        self.assertEqual((added.command, added.alias, added.path), ("add-ro", "docs", "/srv/shared/docs"))
        removed = parser.parse_args(["access", "remove", "manager-qual", "docs"])
        self.assertEqual((removed.command, removed.alias), ("remove", "docs"))

    def test_pm3_public_projection_and_concurrency_surface_parses(self) -> None:
        parser = build_parser()
        template = parser.parse_args(["instance", "template"])
        self.assertEqual((template.area, template.command), ("instance", "template"))
        summary = parser.parse_args(["instance", "summary", "manager-qual"])
        self.assertEqual(summary.instance_id, "manager-qual")
        summaries = parser.parse_args(["instance", "summaries"])
        self.assertEqual(summaries.command, "summaries")
        preview = parser.parse_args(["instance", "preview", "/tmp/candidate.json"])
        self.assertEqual(str(preview.file), "/tmp/candidate.json")
        update = parser.parse_args(
            ["instance", "update", "/tmp/candidate.json", "--expected-current-fingerprint", "a" * 64]
        )
        self.assertEqual(update.expected_current_fingerprint, "a" * 64)
        apply = parser.parse_args(
            ["instance", "apply", "manager-qual", "--expected-plan-fingerprint", "b" * 64]
        )
        self.assertEqual(apply.expected_plan_fingerprint, "b" * 64)
        logs = parser.parse_args(["instance", "logs", "manager-qual", "--category", "recovery"])
        self.assertEqual(logs.category, "recovery")
        access = parser.parse_args(
            [
                "access",
                "update",
                "manager-qual",
                "docs",
                "rw",
                "models",
                "/srv/models",
                "--expected-current-fingerprint",
                "c" * 64,
            ]
        )
        self.assertEqual(
            (access.existing_alias, access.mode, access.alias, access.path, access.expected_current_fingerprint),
            ("docs", "rw", "models", "/srv/models", "c" * 64),
        )

    def test_pm3_1_discovery_candidate_and_ports_surface_parses(self) -> None:
        parser = build_parser()
        discover = parser.parse_args(["instance", "discover", "/home/operator/repos/example", "--pretty"])
        self.assertEqual((discover.area, discover.command, discover.path), ("instance", "discover", "/home/operator/repos/example"))
        self.assertTrue(discover.pretty)
        candidate = parser.parse_args(["instance", "candidate", "--pretty"])
        self.assertEqual((candidate.area, candidate.command), ("instance", "candidate"))
        self.assertTrue(candidate.pretty)
        ports = parser.parse_args(["host", "ports", "--pretty"])
        self.assertEqual((ports.area, ports.command), ("host", "ports"))
        self.assertTrue(ports.pretty)

    def test_instance_git_command_parses_p9_diagnostic(self) -> None:
        args = build_parser().parse_args(["instance", "git", "manager-qual", "--pretty"])
        self.assertEqual((args.area, args.command, args.instance_id), ("instance", "git", "manager-qual"))
        self.assertTrue(args.pretty)

    def test_host_reboot_commands_parse_p13_surface(self) -> None:
        parser = build_parser()
        request = parser.parse_args(["host", "reboot", "--reason", "maintenance", "--pretty"])
        self.assertEqual((request.area, request.command, request.reason), ("host", "reboot", "maintenance"))
        self.assertTrue(request.pretty)
        check = parser.parse_args(["host", "reboot-check", "--pretty"])
        self.assertEqual((check.area, check.command), ("host", "reboot-check"))
        self.assertTrue(check.pretty)

    def test_instance_diagnose_command_parses_p12_window(self) -> None:
        args = build_parser().parse_args(
            ["instance", "diagnose", "manager-qual", "--since-seconds", "321", "--pretty"]
        )
        self.assertEqual((args.area, args.command, args.instance_id), ("instance", "diagnose", "manager-qual"))
        self.assertEqual(args.since_seconds, 321)
        self.assertTrue(args.pretty)

    def test_p7_1_client_compatibility_surface_parses_and_keeps_legacy_alias(self) -> None:
        primary = build_parser().parse_args(["instance", "client-compatibility", "electrocad", "--pretty"])
        self.assertEqual(
            (primary.area, primary.command, primary.instance_id),
            ("instance", "client-compatibility", "electrocad"),
        )
        self.assertTrue(primary.pretty)

        args = build_parser().parse_args(["instance", "session-continuity", "electrocad", "--pretty"])
        self.assertEqual((args.area, args.command, args.instance_id), ("instance", "session-continuity", "electrocad"))
        self.assertTrue(args.pretty)

    def test_codex_command_surface_parses_p10_operations(self) -> None:
        parser = build_parser()
        started = parser.parse_args(
            ["codex", "start", "manager-qual", "--mode", "read", "--prompt", "inspect only", "--pretty"]
        )
        self.assertEqual(
            (started.area, started.command, started.instance_id, started.mode, started.prompt),
            ("codex", "start", "manager-qual", "read", "inspect only"),
        )
        output = parser.parse_args(
            ["codex", "output", "manager-qual", "20260813T010203Z-abcdef123456", "--limit-bytes", "99"]
        )
        self.assertEqual(output.limit_bytes, 99)
        cancelled = parser.parse_args(
            ["codex", "cancel", "manager-qual", "20260813T010203Z-abcdef123456"]
        )
        self.assertEqual(cancelled.command, "cancel")

    def test_cleanup_command_surface_parses_p14_operations(self) -> None:
        parser = build_parser()
        audit_all = parser.parse_args(["cleanup", "audit", "--pretty"])
        self.assertEqual((audit_all.area, audit_all.command, audit_all.instance_id), ("cleanup", "audit", None))
        audit_one = parser.parse_args(["cleanup", "audit", "legacy-qual"])
        self.assertEqual(audit_one.instance_id, "legacy-qual")
        execute = parser.parse_args(["cleanup", "execute", "legacy-qual", "--pretty"])
        self.assertEqual((execute.area, execute.command, execute.instance_id), ("cleanup", "execute", "legacy-qual"))
        self.assertTrue(execute.pretty)

    def test_host_tools_command_surface_parses_p15_audits(self) -> None:
        parser = build_parser()
        audit = parser.parse_args(["host", "tools", "audit", "--pretty"])
        self.assertEqual((audit.area, audit.command, audit.tool_action), ("host", "tools", "audit"))
        agents = parser.parse_args(["host", "tools", "agents", "audit"])
        self.assertEqual((agents.tool_action, agents.action), ("agents", "audit"))

    def test_host_tools_command_surface_parses_p15_codex_and_gh(self) -> None:
        parser = build_parser()
        codex = parser.parse_args(
            [
                "host",
                "tools",
                "codex",
                "--node-root",
                "/home/operator/.nvm/versions/node/v24.15.0",
                "--version",
                "0.147.0",
            ]
        )
        self.assertEqual((codex.tool_action, codex.version), ("codex", "0.147.0"))
        gh = parser.parse_args(
            ["host", "tools", "gh", "--source", "/tmp/gh", "--sha256", "a" * 64]
        )
        self.assertEqual((gh.tool_action, gh.source, gh.sha256), ("gh", "/tmp/gh", "a" * 64))


if __name__ == "__main__":
    unittest.main()

