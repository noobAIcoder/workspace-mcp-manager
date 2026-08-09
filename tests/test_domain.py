from __future__ import annotations

import unittest

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ErrorCode, ManagerError

from tests.helpers import sample_instance


class DesiredInstanceTests(unittest.TestCase):
    def test_valid_instance_round_trips_and_fingerprint_is_stable(self) -> None:
        first = DesiredInstance.from_dict(sample_instance())
        second = DesiredInstance.from_json(first.canonical_json())
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_unknown_root_field_fails_closed(self) -> None:
        value = sample_instance()
        value["surprise"] = True
        with self.assertRaisesRegex(ManagerError, "unknown fields"):
            DesiredInstance.from_dict(value)

    def test_unknown_nested_field_fails_closed(self) -> None:
        value = sample_instance()
        value["mcp"]["surprise"] = True
        with self.assertRaisesRegex(ManagerError, "unknown fields"):
            DesiredInstance.from_dict(value)

    def test_unsupported_version_has_specific_error(self) -> None:
        value = sample_instance()
        value["config_version"] = 2
        with self.assertRaises(ManagerError) as caught:
            DesiredInstance.from_dict(value)
        self.assertEqual(caught.exception.code, ErrorCode.CONFIG_VERSION_UNSUPPORTED)

    def test_instance_id_rejects_double_hyphen(self) -> None:
        value = sample_instance()
        value["instance_id"] = "bad--id"
        with self.assertRaises(ManagerError):
            DesiredInstance.from_dict(value)

    def test_access_aliases_collide_like_legacy_keys(self) -> None:
        value = sample_instance()
        value["access"]["read_only"] = [{"alias": "shared-docs", "path": "/a"}]
        value["access"]["read_write"] = [{"alias": "shared_docs", "path": "/b"}]
        with self.assertRaisesRegex(ManagerError, "aliases collide"):
            DesiredInstance.from_dict(value)

    def test_access_source_inside_workspace_is_rejected(self) -> None:
        value = sample_instance()
        value["access"]["read_only"] = [
            {"alias": "inside", "path": "/srv/workspaces/sample/already-here"}
        ]
        with self.assertRaisesRegex(ManagerError, "already inside workspace_path"):
            DesiredInstance.from_dict(value)

    def test_external_root_must_be_absolute(self) -> None:
        value = sample_instance()
        value["mcp"]["external_roots"] = ["relative/tool"]
        with self.assertRaisesRegex(ManagerError, "absolute POSIX"):
            DesiredInstance.from_dict(value)

    def test_exec_path_requires_absolute_entries(self) -> None:
        value = sample_instance()
        value["mcp"]["exec_path"] = "/usr/bin:relative/bin"
        with self.assertRaisesRegex(ManagerError, "absolute path entries"):
            DesiredInstance.from_dict(value)

    def test_same_endpoint_is_rejected(self) -> None:
        value = sample_instance()
        value["tunnel"]["health_port"] = value["mcp"]["port"]
        with self.assertRaisesRegex(ManagerError, "collide"):
            DesiredInstance.from_dict(value)

    def test_absent_deployment_cannot_be_running(self) -> None:
        value = sample_instance()
        value["lifecycle"] = {"deployment": "absent", "runtime": "running"}
        with self.assertRaisesRegex(ManagerError, "cannot be running"):
            DesiredInstance.from_dict(value)


if __name__ == "__main__":
    unittest.main()

