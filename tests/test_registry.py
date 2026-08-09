from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.registry import InstanceRegistry

from tests.helpers import sample_instance


class RegistryTests(unittest.TestCase):
    def test_create_list_show_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = InstanceRegistry(Path(directory) / "instances")
            desired = DesiredInstance.from_dict(sample_instance())
            path = registry.create(desired)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual([item.instance_id.value for item in registry.list()], ["sample"])
            self.assertEqual(registry.get("sample").fingerprint(), desired.fingerprint())

            value = sample_instance()
            value["lifecycle"]["runtime"] = "stopped"
            updated = DesiredInstance.from_dict(value)
            registry.update(updated)
            self.assertEqual(registry.get("sample").lifecycle.runtime.value, "stopped")

    def test_create_refuses_existing_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = InstanceRegistry(Path(directory))
            desired = DesiredInstance.from_dict(sample_instance())
            registry.create(desired)
            with self.assertRaises(ManagerError) as caught:
                registry.create(desired)
            self.assertEqual(caught.exception.code, ErrorCode.INSTANCE_EXISTS)

    def test_registry_filename_must_match_instance_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.mkdir(exist_ok=True)
            (root / "other.json").write_text(json.dumps(sample_instance()), encoding="utf-8")
            registry = InstanceRegistry(root)
            with self.assertRaises(ManagerError) as caught:
                registry.list()
            self.assertEqual(caught.exception.code, ErrorCode.REGISTRY_INVALID)


if __name__ == "__main__":
    unittest.main()

