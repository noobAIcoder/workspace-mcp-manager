from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ComponentUninstallWorkflowTests(unittest.TestCase):
    """Single operator selection migrates a legacy bundled manager and uninstalls."""

    def _run_case(self, component: str, *, migrate_ok: bool = True, manager_only: bool = False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            prefix = home / ".local"
            manifest = prefix / "lib/workspace-mcp-manager/current/install.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "schema_version": 4 if manager_only else 3,
                "distribution_profile": "manager-only" if manager_only else "legacy",
            }))
            order = root / "order.txt"
            installer = root / "bootstrap-fixture.sh"
            installer.write_text("""#!/usr/bin/env bash
set -euo pipefail
printf 'migrate\\n' >> "$TEST_ORDER"
if [[ "$TEST_MIGRATE_OK" != 1 ]]; then exit 2; fi
printf '%s\\n' '{"schema_version":4,"distribution_profile":"manager-only"}' > "$TEST_MANIFEST"
printf '%s\\n' '{"ok":true,"action":"upgrade"}'
""")
            installer.chmod(0o755)

            backend = root / "backend.sh"
            backend.write_text("""#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  --maintenance-preview)
    printf 'preview-%s\\n' "$2" >> "$TEST_ORDER"
    printf '%s\\n' '{"ok":true,"action":"uninstall-REPLACE","candidates":["dist-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"protected":{}}'
    ;;
  --maintenance-apply)
    cat >/dev/null
    printf 'apply\\n' >> "$TEST_ORDER"
    printf '%s\\n' '{"ok":true,"action":"uninstall-REPLACE","removed":["dist-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}'
    ;;
  *) exit 2 ;;
esac
""".replace("REPLACE", component))
            backend.chmod(0o755)

            script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
            start = script.index("setup_prepare_component_uninstall() {")
            end = script.index("setup_maintenance_task() {", start)
            functions = script[start:end]
            harness = """#!/usr/bin/env bash
set -euo pipefail
ACCOUNT_HOME="$TEST_HOME"
PREFIX="$TEST_PREFIX"
self="$TEST_INSTALLER"
backend="$TEST_BACKEND"
ownership=standalone_distribution
pending=false
remote_args=(--non-interactive --account-home "$TEST_HOME" --prefix "$TEST_PREFIX")
setup_inspect() { ownership=standalone_distribution; }
setup_result() {
  python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["ok"]; print("completed:"+p["action"])'
}
setup_read() { printf 'UNEXPECTED READ\\n' >&2; exit 3; }
""" + functions + "\nsetup_uninstall_component_task " + component + "\n"
            runner = root / "run.sh"
            runner.write_text(harness)
            runner.chmod(0o755)
            result = subprocess.run(
                ["bash", str(runner)], text=True, capture_output=True, timeout=30,
                env={
                    **os.environ,
                    "TEST_HOME": str(home), "TEST_PREFIX": str(prefix),
                    "TEST_INSTALLER": str(installer), "TEST_BACKEND": str(backend),
                    "TEST_MANIFEST": str(manifest), "TEST_ORDER": str(order),
                    "TEST_MIGRATE_OK": "1" if migrate_ok else "0",
                },
            )
            actions = order.read_text().splitlines() if order.exists() else []
            return result, actions, json.loads(manifest.read_text())

    def test_uninstall_mcp_automatically_migrates_then_removes(self):
        result, actions, manifest = self._run_case("mcp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actions, ["migrate", "preview-uninstall-mcp", "apply"])
        self.assertEqual(manifest["schema_version"], 4)
        self.assertIn("Uninstalling unused historical bundled releases", result.stdout)
        self.assertNotIn("Type UNINSTALL-MCP", result.stdout)

    def test_uninstall_tunnel_automatically_migrates_then_removes(self):
        result, actions, manifest = self._run_case("tunnel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actions, ["migrate", "preview-uninstall-tunnel", "apply"])
        self.assertEqual(manifest["schema_version"], 4)

    def test_uninstall_on_manager_only_release_needs_no_migration(self):
        result, actions, _ = self._run_case("mcp", manager_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actions, ["preview-uninstall-mcp", "apply"])

    def test_migration_failure_never_attempts_removal(self):
        result, actions, manifest = self._run_case("mcp", migrate_ok=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actions, ["migrate"])
        self.assertEqual(manifest["schema_version"], 3)
        self.assertNotIn("Uninstalling unused historical bundled releases", result.stdout)


if __name__ == "__main__":
    unittest.main()
