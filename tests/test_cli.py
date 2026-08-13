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


if __name__ == "__main__":
    unittest.main()

