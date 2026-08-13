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


if __name__ == "__main__":
    unittest.main()

