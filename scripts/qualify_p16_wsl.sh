#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
LAUNCHER="$HOME/.local/bin/$INSTANCE_ID-mcp"

fail() {
  printf 'P16_FAIL=%s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable"
[ -f "$LAUNCHER" ] || fail "instance launcher is missing"
[ ! -L "$LAUNCHER" ] || fail "instance launcher must not be a symlink"
[ -x "$LAUNCHER" ] || fail "instance launcher is not executable"
[ "$(stat -c '%a' "$LAUNCHER")" = 755 ] || fail "instance launcher mode is not 0755"

grep -F "# workspace-mcp-manager-instance=$INSTANCE_ID" "$LAUNCHER" >/dev/null \
  || fail "instance launcher ownership marker is missing"
! grep -F 'WORKSPACE_MCP_CONFIG' "$LAUNCHER" >/dev/null \
  || fail "instance launcher embeds legacy configuration authority"
! grep -F '_runtime' "$LAUNCHER" >/dev/null \
  || fail "instance launcher exposes private runtime commands"
! grep -F 'eval ' "$LAUNCHER" >/dev/null \
  || fail "instance launcher contains eval"

TMP_ROOT="$(mktemp -d -t workspace-mcp-p16-XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT

"$LAUNCHER" status >"$TMP_ROOT/status.json"
python3 - "$TMP_ROOT/status.json" "$INSTANCE_ID" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["instance_id"] == sys.argv[2], p
assert p["ok"] is True, p
PY

"$LAUNCHER" plan >"$TMP_ROOT/launcher-plan.json"
python3 - "$TMP_ROOT/launcher-plan.json" "$INSTANCE_ID" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["instance_id"] == sys.argv[2], p
assert p["valid"] is True, p
assert {item["operation"] for item in p["operations"]} == {"NOOP"}, p
assert any(item["resource_id"] == "launcher" for item in p["operations"]), p
PY

"$LAUNCHER" access list >"$TMP_ROOT/access.json"
python3 - "$TMP_ROOT/access.json" "$INSTANCE_ID" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["instance_id"] == sys.argv[2], p
assert p["entries"] == [], p
assert p["apply_required"] is False, p
PY

MCP_PID_BEFORE="$(systemctl --user show coding-tools-mcp-$INSTANCE_ID.service -p MainPID --value)"
TUNNEL_PID_BEFORE="$(systemctl --user show tunnel-client-$INSTANCE_ID.service -p MainPID --value)"
set +e
"$LAUNCHER" host >"$TMP_ROOT/rejected.out" 2>"$TMP_ROOT/rejected.err"
REJECT_RC=$?
set -e
[ "$REJECT_RC" -eq 2 ] || fail "unsupported launcher command did not fail with usage status"
grep -F 'unsupported command' "$TMP_ROOT/rejected.err" >/dev/null \
  || fail "unsupported launcher command did not emit bounded error"
[ "$(systemctl --user show coding-tools-mcp-$INSTANCE_ID.service -p MainPID --value)" = "$MCP_PID_BEFORE" ] \
  || fail "unsupported launcher command changed MCP PID"
[ "$(systemctl --user show tunnel-client-$INSTANCE_ID.service -p MainPID --value)" = "$TUNNEL_PID_BEFORE" ] \
  || fail "unsupported launcher command changed tunnel PID"

"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/final-plan.json"
python3 - "$TMP_ROOT/final-plan.json" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["valid"] is True, p
assert {item["operation"] for item in p["operations"]} == {"NOOP"}, p
PY

curl -fsS http://127.0.0.1:7657/.well-known/mcp.json >/dev/null \
  || fail "MCP discovery is unhealthy"
[ "$(curl -fsS http://127.0.0.1:7373/healthz)" = live ] || fail "tunnel health is not live"
[ "$(curl -fsS http://127.0.0.1:7373/readyz)" = ready ] || fail "tunnel readiness is not ready"
[ ! -e "$HOME/repos/workspace-mcp-manager-qual/.workspace-mcp-access" ] \
  || fail "external access root remains after P16 qualification"

printf 'P16_LAUNCHER_RESOURCE=PASS\n'
printf 'P16_LAUNCHER_DISPATCH=PASS\n'
printf 'P16_LAUNCHER_REJECTION=PASS\n'
printf 'P16_WSL_QUALIFICATION=PASS\n'
