#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
LEGACY_ID="${LEGACY_ID:-p14-legacy-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"

CONFIG="$HOME/.config/workspace-mcp/$LEGACY_ID.conf"
CONFIG_BACKUP="$HOME/.config/workspace-mcp/$LEGACY_ID.conf.bak"
PROFILE="$HOME/.config/tunnel-client/workspace-mcp-$LEGACY_ID.yaml"
MCP_UNIT="coding-tools-mcp-$LEGACY_ID.service"
TUNNEL_UNIT="tunnel-client-$LEGACY_ID.service"
MCP_UNIT_PATH="$HOME/.config/systemd/user/$MCP_UNIT"
MCP_DROPIN_DIR="$HOME/.config/systemd/user/$MCP_UNIT.d"
TUNNEL_UNIT_PATH="$HOME/.config/systemd/user/$TUNNEL_UNIT"
LAUNCHER="$HOME/.local/bin/$LEGACY_ID-mcp"
STATE_DIR="$HOME/.local/state/workspace-mcp/$LEGACY_ID"
SHARED_BIN="$HOME/.local/lib/workspace-mcp/workspace-mcp"
RUNTIME_ENV="$HOME/.config/tunnel-client/runtime.env"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable: $MANAGER"
[ -f "$SHARED_BIN" ] || fail "legacy shared implementation binary is unavailable: $SHARED_BIN"
[ -f "$RUNTIME_ENV" ] || fail "shared tunnel runtime env is unavailable: $RUNTIME_ENV"

TMP_ROOT="$(mktemp -d -t workspace-mcp-p14-XXXXXX)"
BASELINE_AUDIT="$TMP_ROOT/baseline-audit.json"
FINAL_AUDIT="$TMP_ROOT/final-audit.json"

cleanup() {
  local rc=$?
  systemctl --user stop "$TUNNEL_UNIT" >/dev/null 2>&1 || true
  systemctl --user stop "$MCP_UNIT" >/dev/null 2>&1 || true
  systemctl --user disable "$TUNNEL_UNIT" >/dev/null 2>&1 || true
  systemctl --user disable "$MCP_UNIT" >/dev/null 2>&1 || true
  rm -f -- "$TUNNEL_UNIT_PATH" "$MCP_UNIT_PATH" "$PROFILE" "$LAUNCHER" "$CONFIG_BACKUP" "$CONFIG"
  if [ -d "$MCP_DROPIN_DIR" ] && [ ! -L "$MCP_DROPIN_DIR" ]; then
    rm -f -- "$MCP_DROPIN_DIR/github-access.conf" "$MCP_DROPIN_DIR/zz-exec-roots.conf" \
      "$MCP_DROPIN_DIR/workspace-mcp-manager-access.conf"
    rmdir "$MCP_DROPIN_DIR" >/dev/null 2>&1 || true
  fi
  if [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ]; then
    rm -f -- "$STATE_DIR/mcp.log" "$STATE_DIR/tunnel.log" "$STATE_DIR/tunnel-supervisor.log"
    rmdir "$STATE_DIR" >/dev/null 2>&1 || true
  fi
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  rm -rf "$TMP_ROOT"
  return "$rc"
}
trap cleanup EXIT

for path in "$CONFIG" "$CONFIG_BACKUP" "$PROFILE" "$MCP_UNIT_PATH" "$MCP_DROPIN_DIR" "$TUNNEL_UNIT_PATH" "$LAUNCHER" "$STATE_DIR"; do
  if [ -e "$path" ] || [ -L "$path" ]; then
    fail "synthetic qualification path already exists: $path"
  fi
done

"$MANAGER" cleanup audit >"$BASELINE_AUDIT"
python3 - "$BASELINE_AUDIT" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["read_only"] is True
assert p["ok"] is True
ids = {item["instance_id"] for item in p["candidates"]}
assert "manager-qual" not in ids
PY

SHARED_HASH_BEFORE="$(sha256sum "$SHARED_BIN" | awk '{print $1}')"
ENV_HASH_BEFORE="$(sha256sum "$RUNTIME_ENV" | awk '{print $1}')"
MCP_PID_BEFORE="$(systemctl --user show coding-tools-mcp-$INSTANCE_ID.service -p MainPID --value)"
TUNNEL_PID_BEFORE="$(systemctl --user show tunnel-client-$INSTANCE_ID.service -p MainPID --value)"

mkdir -p "$(dirname "$CONFIG")" "$(dirname "$PROFILE")" "$(dirname "$MCP_UNIT_PATH")" \
  "$(dirname "$LAUNCHER")" "$STATE_DIR"

cat >"$CONFIG" <<EOF
# Workspace MCP lifecycle configuration. Values are parsed as data, never sourced.
CONFIG_VERSION=1
INSTANCE_ID=$LEGACY_ID
WORKSPACE_PATH=$HOME/repos/workspace-mcp-manager-qual
MCP_HOST=127.0.0.1
MCP_PORT=19991
TUNNEL_PROFILE=workspace-mcp-$LEGACY_ID
TUNNEL_HEALTH_HOST=127.0.0.1
TUNNEL_HEALTH_PORT=19992
EOF
cp -- "$CONFIG" "$CONFIG_BACKUP"

cat >"$PROFILE" <<EOF
# workspace-mcp-instance=$LEGACY_ID
config_version: 1
control_plane:
  tunnel_id: "tunnel_p14legacyqual"
  api_key: "env:CONTROL_PLANE_API_KEY"
health:
  listen_addr: "127.0.0.1:19992"
EOF

cat >"$MCP_UNIT_PATH" <<EOF
# workspace-mcp-instance=$LEGACY_ID
[Unit]
Description=P14 synthetic legacy MCP

[Service]
Type=simple
ExecStart=/usr/bin/sleep infinity

[Install]
WantedBy=default.target
EOF

mkdir -p "$MCP_DROPIN_DIR"
cat >"$MCP_DROPIN_DIR/github-access.conf" <<EOF
[Service]
Environment="GH_CONFIG_DIR=$HOME/.config/workspace-mcp/$LEGACY_ID-gh"
Environment="CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=$HOME/.config/workspace-mcp/$LEGACY_ID-gh"
EOF

cat >"$TUNNEL_UNIT_PATH" <<EOF
# workspace-mcp-instance=$LEGACY_ID
[Unit]
Description=P14 synthetic legacy tunnel
Requires=$MCP_UNIT
After=$MCP_UNIT

[Service]
Type=simple
ExecStart=/usr/bin/sleep infinity

[Install]
WantedBy=default.target
EOF

cat >"$LAUNCHER" <<EOF
#!/bin/sh
exec env WORKSPACE_MCP_CONFIG='$CONFIG' '$SHARED_BIN' "\$@"
EOF
chmod 0755 "$LAUNCHER"
printf 'synthetic mcp log\n' >"$STATE_DIR/mcp.log"
printf 'synthetic tunnel log\n' >"$STATE_DIR/tunnel.log"
printf 'synthetic supervisor log\n' >"$STATE_DIR/tunnel-supervisor.log"

systemctl --user daemon-reload
systemctl --user enable --now "$MCP_UNIT"
systemctl --user enable --now "$TUNNEL_UNIT"

SYN_MCP_PID="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
SYN_TUNNEL_PID="$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)"
[ "$SYN_MCP_PID" -gt 0 ] || fail "synthetic legacy MCP is not running"
[ "$SYN_TUNNEL_PID" -gt 0 ] || fail "synthetic legacy tunnel is not running"

AUDIT_ONE="$TMP_ROOT/audit-one.json"
"$MANAGER" cleanup audit "$LEGACY_ID" >"$AUDIT_ONE"
python3 - "$AUDIT_ONE" "$LEGACY_ID" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True and p["read_only"] is True
c = p["candidates"][0]
assert c["instance_id"] == sys.argv[2]
assert c["state"] == "removable"
assert {r["ownership"] for r in c["resources"]} == {"legacy-owned"}
PY

[ "$(systemctl --user show "$MCP_UNIT" -p MainPID --value)" = "$SYN_MCP_PID" ] \
  || fail "read-only audit changed synthetic MCP PID"
[ "$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)" = "$SYN_TUNNEL_PID" ] \
  || fail "read-only audit changed synthetic tunnel PID"
for path in "$CONFIG" "$CONFIG_BACKUP" "$PROFILE" "$MCP_UNIT_PATH" "$MCP_DROPIN_DIR" "$TUNNEL_UNIT_PATH" "$LAUNCHER" "$STATE_DIR"; do
  [ -e "$path" ] || fail "read-only audit removed synthetic legacy resource: $path"
done
printf 'P14_READ_ONLY_AUDIT=PASS\n'

EXECUTE_JSON="$TMP_ROOT/execute.json"
"$MANAGER" cleanup execute "$LEGACY_ID" >"$EXECUTE_JSON"
python3 - "$EXECUTE_JSON" "$LEGACY_ID" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True
assert p["instance_id"] == sys.argv[2]
assert p["before"]["state"] == "removable"
assert p["final"]["state"] == "clean"
ops = [item["operation"] for item in p["operations"]]
assert ops[:4] == ["STOP", "STOP", "DISABLE", "DISABLE"], ops
assert "DAEMON_RELOAD" in ops
assert "REMOVE" in ops
PY

for path in "$CONFIG" "$CONFIG_BACKUP" "$PROFILE" "$MCP_UNIT_PATH" "$MCP_DROPIN_DIR" "$TUNNEL_UNIT_PATH" "$LAUNCHER" "$STATE_DIR"; do
  if [ -e "$path" ] || [ -L "$path" ]; then
    fail "cleanup left synthetic legacy resource: $path"
  fi
done
systemctl --user is-active "$MCP_UNIT" >/dev/null 2>&1 && fail "synthetic MCP remains active"
systemctl --user is-active "$TUNNEL_UNIT" >/dev/null 2>&1 && fail "synthetic tunnel remains active"

[ "$(sha256sum "$SHARED_BIN" | awk '{print $1}')" = "$SHARED_HASH_BEFORE" ] \
  || fail "shared workspace-mcp implementation changed"
[ "$(sha256sum "$RUNTIME_ENV" | awk '{print $1}')" = "$ENV_HASH_BEFORE" ] \
  || fail "shared tunnel runtime env changed"
printf 'P14_SHARED_RESOURCES_PRESERVED=PASS\n'

SECOND_AUDIT="$TMP_ROOT/second-audit.json"
"$MANAGER" cleanup audit "$LEGACY_ID" >"$SECOND_AUDIT"
python3 - "$SECOND_AUDIT" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True
assert p["candidates"][0]["state"] == "clean"
PY

"$MANAGER" cleanup audit >"$FINAL_AUDIT"
python3 - "$BASELINE_AUDIT" "$FINAL_AUDIT" <<'PY'
import json, pathlib, sys
a = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
b = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert a["candidates"] == b["candidates"], "pre-existing legacy inventory changed"
PY

[ "$(systemctl --user show coding-tools-mcp-$INSTANCE_ID.service -p MainPID --value)" = "$MCP_PID_BEFORE" ] \
  || fail "manager-qual MCP PID changed during P14 qualification"
[ "$(systemctl --user show tunnel-client-$INSTANCE_ID.service -p MainPID --value)" = "$TUNNEL_PID_BEFORE" ] \
  || fail "manager-qual tunnel PID changed during P14 qualification"

PLAN="$TMP_ROOT/plan.json"
"$MANAGER" instance plan "$INSTANCE_ID" >"$PLAN"
python3 - "$PLAN" <<'PY'
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["valid"] is True
assert {item["operation"] for item in p["operations"]} == {"NOOP"}
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null
curl -fsS http://127.0.0.1:7657/.well-known/mcp.json >/dev/null
[ "$(curl -fsS http://127.0.0.1:7373/healthz)" = live ] || fail "tunnel health regression"
[ "$(curl -fsS http://127.0.0.1:7373/readyz)" = ready ] || fail "tunnel readiness regression"

cleanup
trap - EXIT
printf 'P14_LEGACY_CLEANUP_QUALIFICATION=PASS\n'
