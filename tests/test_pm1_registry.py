from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.registry import InstanceRegistry

from helpers import sample_instance, sample_v2_instance


class Pm1RegistryTests(unittest.TestCase):
    def test_schema_upgrade_is_not_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = InstanceRegistry(Path(directory))
            registry.create(DesiredInstance.from_dict(sample_instance()))
            registry.update(DesiredInstance.from_dict(sample_v2_instance()))
            with self.assertRaises(ManagerError) as caught:
                registry.update(DesiredInstance.from_dict(sample_instance()))
            self.assertEqual(caught.exception.code, ErrorCode.CONFIG_VERSION_UNSUPPORTED)
            self.assertEqual(registry.get("sample").config_version, 2)


if __name__ == "__main__":
    unittest.main()
