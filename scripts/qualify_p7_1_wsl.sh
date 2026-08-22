#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTANCE_ID=${P7_1_INSTANCE_ID:-electrocad}
TMP=$(mktemp -d -t workspace-mcp-p7-1-XXXXXX)
PROXY_PID=""

cleanup() {
  if [[ -n "$PROXY_PID" ]]; then
    kill "$PROXY_PID" >/dev/null 2>&1 || true
    wait "$PROXY_PID" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$TMP"
}
trap cleanup EXIT INT TERM

json_assert() {
  local expression=$1
  python3 -c '
import json, sys
d=json.load(sys.stdin)
safe={"all":all,"any":any,"bool":bool,"len":len,"set":set,"sorted":sorted,"str":str,"isinstance":isinstance,"list":list,"dict":dict}
if not eval(sys.argv[1], {"__builtins__": {}}, {"d": d, **safe}):
    raise SystemExit(f"assertion failed: {sys.argv[1]}\n{json.dumps(d, indent=2, sort_keys=True)}")
' "$expression"
}

json_field() {
  local field=$1
  python3 -c '
import json, sys
value=json.load(sys.stdin)
for part in sys.argv[1].split("."):
    value=value[int(part)] if isinstance(value, list) else value[part]
print("" if value is None else value)
' "$field"
}

SOURCE_MANAGER=(python3 -m workspace_mcp_manager.cli)
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SHOW=$("${SOURCE_MANAGER[@]}" _runtime show "$INSTANCE_ID")
HOST=$(printf '%s\n' "$SHOW" | json_field desired.mcp.host)
PORT=$(printf '%s\n' "$SHOW" | json_field desired.mcp.port)
TUNNEL_BINARY=$(printf '%s\n' "$SHOW" | json_field desired.tunnel.binary)
[[ -x "$TUNNEL_BINARY" ]] || { echo "P7_1_FAIL=tunnel-client unavailable" >&2; exit 1; }

echo "=== P7.1 Layer 1: direct local MCP client compatibility ==="
LOCAL=$("${SOURCE_MANAGER[@]}" _runtime client-compatibility "$INSTANCE_ID")
printf '%s\n' "$LOCAL" | json_assert \
  'd["client_compatibility_projection_version"] == 1 and d["session_continuity_projection_version"] == 2 and d["scope"] == "local_mcp" and d["atomic_coding_ready"] is True and d["protocol_session_persistence"] == "not_required_for_atomic_operations" and d["session_local_state"]["mcp_session"] == "passed" and d["session_local_state"]["command_session"] == "passed" and d["cross_session_state"]["command_handles"] in {"session_scoped","cross_session"} and d["cleanup"] == "passed" and d["result"] in {"passed","passed_with_limitations"} and len(d["session_fingerprint"]) == 12'
echo "P7_1_ATOMIC_CODING_READINESS=PASS"
echo "P7_1_PROTOCOL_SESSION_PERSISTENCE=NOT_REQUIRED"
echo "P7_1_LOCAL_MCP_COMMAND_HANDLES=$(printf '%s\n' "$LOCAL" | json_field cross_session_state.command_handles)"

echo "=== P7.1 Layer 2: local tunnel-client dev proxy ==="
PROXY_JSON="$TMP/proxy.json"
"$TUNNEL_BINARY" dev proxy \
  --mcp-server-url "http://${HOST}:${PORT}/mcp" \
  --url-file "$PROXY_JSON" \
  --duration 60s \
  >"$TMP/proxy.log" 2>&1 &
PROXY_PID=$!
for _ in $(seq 1 100); do
  [[ -s "$PROXY_JSON" ]] && break
  sleep 0.1
done
[[ -s "$PROXY_JSON" ]] || { echo "P7_1_FAIL=local tunnel proxy did not become ready" >&2; exit 1; }
PROXY_URL=$(cat "$PROXY_JSON" | json_field mcp_url)
PROXY=$(P7_1_PROXY_URL="$PROXY_URL" P7_1_INSTANCE_ID="$INSTANCE_ID" python3 -c '
import json, os
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.session_continuity import ClientCompatibilityService
paths=ManagerPaths.for_current_user()
service=ClientCompatibilityService(paths, InstanceRegistry(paths.registry_dir))
print(json.dumps(service.run(os.environ["P7_1_INSTANCE_ID"], endpoint_url=os.environ["P7_1_PROXY_URL"], scope="local_tunnel_proxy"), sort_keys=True, separators=(",", ":")))
')
printf '%s\n' "$PROXY" | json_assert \
  'd["client_compatibility_projection_version"] == 1 and d["scope"] == "local_tunnel_proxy" and d["atomic_coding_ready"] is True and d["session_local_state"]["command_session"] == "passed" and d["cross_session_state"]["command_handles"] in {"session_scoped","cross_session"} and d["cleanup"] in {"passed","unavailable"} and d["result"] in {"passed","passed_with_limitations"}'
echo "P7_1_LOCAL_TUNNEL_PROXY_COMPATIBILITY=PASS"
echo "P7_1_LOCAL_TUNNEL_PROXY_COMMAND_HANDLES=$(printf '%s\n' "$PROXY" | json_field cross_session_state.command_handles)"
if printf '%s\n' "$PROXY" | python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("cleanup") == "unavailable" else 1)'; then
  echo "P7_1_LOCAL_TUNNEL_PROXY_SESSION_TERMINATION=UNAVAILABLE"
fi

echo "P7_1_DEFAULT_CWD_CROSS_CALL=OPTIONAL"
echo "P7_1_CROSS_CALL_RESOURCE_HANDLE_SCOPE=EXPLICIT_CAPABILITY"
echo "P7_1_IMPLEMENTATION_QUALIFICATION=PASS"
