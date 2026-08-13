#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
BRIDGE="${BRIDGE:-$HOME/.local/bin/workspace-mcp-reboot}"
CHECKPOINT="$HOME/.local/state/workspace-mcp-manager/reboot/checkpoint.json"
REBOOT_ROOT="$(dirname "$CHECKPOINT")"
MANAGER_REAL="$(readlink -f "$MANAGER")"
MANAGER_PYTHON="${MANAGER_PYTHON:-$(dirname "$MANAGER_REAL")/python}"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable: $MANAGER"
[ -x "$BRIDGE" ] || fail "reboot bridge is unavailable: $BRIDGE"
[ -x "$MANAGER_PYTHON" ] || fail "manager tool Python is unavailable: $MANAGER_PYTHON"

TMP_ROOT="$(mktemp -d -t workspace-mcp-p13-XXXXXX)"
CHECKPOINT_BACKUP="$TMP_ROOT/checkpoint.backup"
HAD_CHECKPOINT=0
if [ -f "$CHECKPOINT" ]; then
  cp -p "$CHECKPOINT" "$CHECKPOINT_BACKUP"
  HAD_CHECKPOINT=1
fi

cleanup() {
  local rc=$?
  if [ "$HAD_CHECKPOINT" -eq 1 ]; then
    mkdir -p "$REBOOT_ROOT"
    cp -p "$CHECKPOINT_BACKUP" "$CHECKPOINT"
  else
    rm -f "$CHECKPOINT"
  fi
  rm -rf "$TMP_ROOT"
  return "$rc"
}
trap cleanup EXIT

BOOT_ID_BEFORE="$(cat /proc/sys/kernel/random/boot_id)"
MCP_UNIT="coding-tools-mcp-$INSTANCE_ID.service"
TUNNEL_UNIT="tunnel-client-$INSTANCE_ID.service"
MCP_PID_BEFORE="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
TUNNEL_PID_BEFORE="$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)"

SHOW_JSON="$TMP_ROOT/show.json"
PLAN_JSON="$TMP_ROOT/plan.json"
"$MANAGER" instance show "$INSTANCE_ID" >"$SHOW_JSON"
"$MANAGER" instance plan "$INSTANCE_ID" >"$PLAN_JSON"
BASELINE_FINGERPRINT="$(python3 - "$SHOW_JSON" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["fingerprint"])
PY
)"
EXPECTED_PATH="$(python3 - "$SHOW_JSON" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["desired"]["mcp"]["exec_path"])
PY
)"
python3 - "$PLAN_JSON" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["valid"] is True
assert {item["operation"] for item in p["operations"]} == {"NOOP"}
PY

# Real bridge authorization check only. Never invoke the real bridge without
# --check from this non-destructive qualification harness.
set +e
"$BRIDGE" --check
REAL_BRIDGE_CHECK_RC=$?
set -e
printf 'P13_REAL_BRIDGE_CHECK_RC=%s\n' "$REAL_BRIDGE_CHECK_RC"

# Exercise the installed RebootService against the real registry/boot ID while
# substituting a deliberately failing *fake* bridge. The fake checks that the
# canonical checkpoint already exists in state=prepared before preflight.
FAKE1="$TMP_ROOT/auth-fail/bin"
mkdir -p "$FAKE1"
cat >"$FAKE1/workspace-mcp-reboot" <<'SH'
#!/bin/sh
checkpoint="$HOME/.local/state/workspace-mcp-manager/reboot/checkpoint.json"
test -s "$checkpoint" || exit 91
if [ "${1-}" = "--check" ] && grep -F '"state": "prepared"' "$checkpoint" >/dev/null; then
  exit 42
fi
exit 93
SH
chmod 0755 "$FAKE1/workspace-mcp-reboot"

"$MANAGER_PYTHON" - "$FAKE1" "$BOOT_ID_BEFORE" <<'PY'
from dataclasses import replace
from pathlib import Path
import json
import sys

from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.reboot import RebootService
from workspace_mcp_manager.registry import InstanceRegistry

fake_bin = Path(sys.argv[1])
boot_id = sys.argv[2]
paths = ManagerPaths.for_current_user()
paths = replace(paths, manager_executable=fake_bin / "workspace-mcp-manager")
service = RebootService(paths, InstanceRegistry(paths.registry_dir))
result = service.request(reason="P13 WSL authorization-failure qualification")
assert result["ok"] is False, result
assert result["state"] == "authorization_failed", result
checkpoint = json.loads(service.checkpoint_path.read_text(encoding="utf-8"))
assert checkpoint["boot_id_before"] == boot_id
assert checkpoint["boot_id_after"] is None
assert checkpoint["authorization"]["exit_code"] == 42
assert checkpoint["request_result"]["ok"] is False
assert any(item["instance_id"] == "manager-qual" for item in checkpoint["expected_instances"])
print("P13_AUTHORIZATION_FAILURE_PERSISTENCE=PASS")
PY

# A second fake bridge authorizes preflight but fails the actual request. It
# verifies state=requesting was persisted before the second bridge invocation.
FAKE2="$TMP_ROOT/request-fail/bin"
mkdir -p "$FAKE2"
cat >"$FAKE2/workspace-mcp-reboot" <<'SH'
#!/bin/sh
checkpoint="$HOME/.local/state/workspace-mcp-manager/reboot/checkpoint.json"
test -s "$checkpoint" || exit 91
if [ "${1-}" = "--check" ]; then
  grep -F '"state": "prepared"' "$checkpoint" >/dev/null || exit 92
  exit 0
fi
if [ "$#" -eq 0 ]; then
  grep -F '"state": "requesting"' "$checkpoint" >/dev/null || exit 94
  exit 43
fi
exit 95
SH
chmod 0755 "$FAKE2/workspace-mcp-reboot"

"$MANAGER_PYTHON" - "$FAKE2" "$BOOT_ID_BEFORE" <<'PY'
from dataclasses import replace
from pathlib import Path
import json
import sys

from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.reboot import RebootService
from workspace_mcp_manager.registry import InstanceRegistry

fake_bin = Path(sys.argv[1])
boot_id = sys.argv[2]
paths = ManagerPaths.for_current_user()
paths = replace(paths, manager_executable=fake_bin / "workspace-mcp-manager")
service = RebootService(paths, InstanceRegistry(paths.registry_dir))
result = service.request(reason="P13 WSL synchronous-request-failure qualification")
assert result["ok"] is False, result
assert result["state"] == "request_failed", result
checkpoint = json.loads(service.checkpoint_path.read_text(encoding="utf-8"))
assert checkpoint["boot_id_before"] == boot_id
assert checkpoint["request_result"]["exit_code"] == 43
assert checkpoint["request_result"]["ok"] is False
print("P13_SYNCHRONOUS_REQUEST_FAILURE_PERSISTENCE=PASS")
PY

[ "$(stat -c '%a' "$REBOOT_ROOT")" = 700 ] || fail "reboot state directory mode is not 0700"
[ "$(stat -c '%a' "$CHECKPOINT")" = 600 ] || fail "reboot checkpoint mode is not 0600"

# Public reboot-check is safe and must preserve the failed request while the
# actual host boot ID remains unchanged.
set +e
"$MANAGER" host reboot-check >"$TMP_ROOT/check.json"
CHECK_RC=$?
set -e
[ "$CHECK_RC" -eq 1 ] || fail "reboot-check did not surface failed checkpoint"
python3 - "$TMP_ROOT/check.json" "$BOOT_ID_BEFORE" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["state"] == "request_failed", p
assert p["boot_id_before"] == sys.argv[2]
assert p["boot_id_after"] is None
PY

[ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT_ID_BEFORE" ] || fail "host unexpectedly rebooted"
[ "$(systemctl --user show "$MCP_UNIT" -p MainPID --value)" = "$MCP_PID_BEFORE" ] \
  || fail "MCP PID changed during non-destructive P13 qualification"
[ "$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)" = "$TUNNEL_PID_BEFORE" ] \
  || fail "tunnel PID changed during non-destructive P13 qualification"

"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/final-plan.json"
python3 - "$TMP_ROOT/final-plan.json" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["valid"] is True
assert {item["operation"] for item in p["operations"]} == {"NOOP"}
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null
FINAL_FINGERPRINT="$("$MANAGER" instance show "$INSTANCE_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"
[ "$FINAL_FINGERPRINT" = "$BASELINE_FINGERPRINT" ] || fail "P13 changed desired-state fingerprint"

curl -fsS "http://127.0.0.1:7657/.well-known/mcp.json" >/dev/null
[ "$(curl -fsS "http://127.0.0.1:7373/healthz")" = live ] || fail "tunnel health regression"
[ "$(curl -fsS "http://127.0.0.1:7373/readyz")" = ready ] || fail "tunnel readiness regression"
CODEX_ENTRY="$(PATH="$EXPECTED_PATH" command -v codex || true)"
[ -x "$CODEX_ENTRY" ] || fail "Codex is unavailable on desired mcp.exec_path"
HOME="$HOME" CODEX_HOME="$HOME/.codex" PATH="$EXPECTED_PATH" "$CODEX_ENTRY" --version >/dev/null
"$MANAGER" instance diagnose "$INSTANCE_ID" --since-seconds 60 >/dev/null

printf 'P13_WSL_NONDESTRUCTIVE_QUALIFICATION=PASS\n'
printf 'P13_PHYSICAL_REBOOT_QUALIFICATION=NOT_RUN\n'
