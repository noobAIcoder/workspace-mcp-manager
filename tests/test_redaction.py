from __future__ import annotations

import unittest

from workspace_mcp_manager.redaction import redact_object, redact_text, sanitized_subprocess_env


class RedactionTests(unittest.TestCase):
    def test_assignment_and_bearer_are_redacted(self) -> None:
        text = "CONTROL_PLANE_API_KEY=secret Bearer abc.def.ghi"
        redacted = redact_text(text)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("abc.def.ghi", redacted)

    def test_secret_mapping_values_are_redacted(self) -> None:
        value = redact_object({"GH_TOKEN": "secret", "path": "/tmp/x"})
        self.assertEqual(value["GH_TOKEN"], "<REDACTED>")
        self.assertEqual(value["path"], "/tmp/x")

    def test_secret_presence_boolean_is_preserved(self) -> None:
        value = redact_object({"CONTROL_PLANE_API_KEY": False})
        self.assertIs(value["CONTROL_PLANE_API_KEY"], False)

    def test_subprocess_environment_drops_secret_values(self) -> None:
        env = sanitized_subprocess_env({"PATH": "/bin", "OPENAI_API_KEY": "secret"})
        self.assertEqual(env["PATH"], "/bin")
        self.assertNotIn("OPENAI_API_KEY", env)


if __name__ == "__main__":
    unittest.main()

