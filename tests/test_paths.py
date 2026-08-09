from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from workspace_mcp_manager.paths import current_account_home


class PathsTests(unittest.TestCase):
    @patch("workspace_mcp_manager.paths.pwd.getpwuid", side_effect=KeyError("blocked"))
    @patch("workspace_mcp_manager.paths.shutil.which", return_value="/usr/bin/systemctl")
    @patch("workspace_mcp_manager.paths.subprocess.run")
    def test_systemd_environment_recovers_real_home_when_passwd_is_blocked(
        self, run, _which, _getpwuid
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            ["systemctl"], 0, "HOME=/home/operator\nUSER=operator\n", ""
        )
        self.assertEqual(str(current_account_home()), "/home/operator")


if __name__ == "__main__":
    unittest.main()
