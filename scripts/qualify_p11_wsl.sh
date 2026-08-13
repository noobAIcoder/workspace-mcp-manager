#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
WORKSPACE="${WORKSPACE:-/home/cloudtoor/repos/workspace-mcp-manager-qual}"
SOURCE_REPO="${SOURCE_REPO:-/home/cloudtoor/repos/workspace-mcp-manager}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
STATE_DIR="$HOME/.local/state/workspace-mcp-manager/instances/$INSTANCE_ID"
EVIDENCE="$STATE_DIR/admission-recovery.json"
MCP_UNIT="coding-tools-mcp-$INSTANCE_ID.service"
TUNNEL_UNIT="tunnel-client-$INSTANCE_ID.service"
GUARD_SERVICE="workspace-mcp-admission-guard-$INSTANCE_ID.service"
GUARD_TIMER="workspace-mcp-admission-guard-$INSTANCE_ID.timer"
GUARD_SERVICE_FILE="$HOME/.config/systemd/user/$GUARD_SERVICE"
GUARD_TIMER_FILE="$HOME/.config/systemd/user/$GUARD_TIMER"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

json_value() {
  local path=$1 expression=$2
  python3 - "$path" "$expression" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in sys.argv[2].split('.'):
    value = value[item]
if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("")
else:
    print(value)
PY
}

if [ ! -x "$MANAGER" ]; then
  fail "manager executable is unavailable: $MANAGER"
fi

TMP_ROOT="$(mktemp -d -t workspace-mcp-p11-XXXXXX)"
SHOW_JSON="$TMP_ROOT/show.json"
BASELINE="$TMP_ROOT/baseline.json"
ENABLED="$TMP_ROOT/enabled.json"
FIRST_SESSIONS="$TMP_ROOT/first-sessions.txt"
SECOND_SESSIONS="$TMP_ROOT/second-sessions.txt"
RESTORE_NEEDED=0
MCP_PORT=""

delete_sessions() {
  local session_file=$1
  [ -n "$MCP_PORT" ] || return 0
  [ -f "$session_file" ] || return 0
  python3 - "$MCP_PORT" "$session_file" <<'PY' || true
import pathlib, sys, urllib.request
port = int(sys.argv[1])
path = pathlib.Path(sys.argv[2])
if not path.is_file():
    raise SystemExit(0)
url = f"http://127.0.0.1:{port}/mcp"
for sid in path.read_text(encoding="utf-8").splitlines():
    if not sid:
        continue
    request = urllib.request.Request(
        url,
        method="DELETE",
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
            "Mcp-Session-Id": sid,
            "Connection": "close",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2):
            pass
    except Exception:
        pass
PY
}

cleanup() {
  local rc=$?
  delete_sessions "$FIRST_SESSIONS"
  delete_sessions "$SECOND_SESSIONS"
  if [ "$RESTORE_NEEDED" -eq 1 ] && [ -f "$BASELINE" ]; then
    "$MANAGER" instance update "$BASELINE" >/dev/null 2>&1 || true
    "$MANAGER" instance apply "$INSTANCE_ID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
  return "$rc"
}
trap cleanup EXIT

"$MANAGER" instance show "$INSTANCE_ID" >"$SHOW_JSON"
python3 - "$SHOW_JSON" "$BASELINE" "$ENABLED" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
raw = payload["desired"]
if raw["lifecycle"] != {"deployment": "present", "runtime": "running"}:
    raise SystemExit("manager-qual must begin present+running")
if raw["access"] != {"read_only": [], "read_write": []}:
    raise SystemExit("manager-qual access must be empty before P11 qualification")
if raw["recovery"]["admission_guard_enabled"]:
    raise SystemExit("admission guard must be disabled in the P11 baseline")
pathlib.Path(sys.argv[2]).write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
updated = json.loads(json.dumps(raw))
updated["recovery"] = {
    "admission_guard_enabled": True,
    "guard_interval_seconds": 30,
    "recovery_cooldown_seconds": 120,
}
pathlib.Path(sys.argv[3]).write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

BASELINE_FINGERPRINT="$(json_value "$SHOW_JSON" fingerprint)"
MCP_PORT="$(json_value "$SHOW_JSON" desired.mcp.port)"
TUNNEL_HEALTH_PORT="$(json_value "$SHOW_JSON" desired.tunnel.health_port)"

"$MANAGER" instance update "$ENABLED" >/dev/null
RESTORE_NEEDED=1
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

[ -f "$GUARD_SERVICE_FILE" ] || fail "guard service was not generated"
[ -f "$GUARD_TIMER_FILE" ] || fail "guard timer was not generated"
grep -F 'OnBootSec=30s' "$GUARD_TIMER_FILE" >/dev/null || fail "guard OnBootSec is not 30 seconds"
grep -F 'OnUnitActiveSec=30s' "$GUARD_TIMER_FILE" >/dev/null || fail "guard cadence is not 30 seconds"
grep -F 'admission-guard' "$GUARD_SERVICE_FILE" >/dev/null || fail "guard runtime command is absent"
systemctl --user is-enabled "$GUARD_TIMER" >/dev/null || fail "guard timer is not enabled"
systemctl --user is-active "$GUARD_TIMER" >/dev/null || fail "guard timer is not active"

MCP_PID_HEALTHY="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
TUNNEL_PID_BASE="$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)"
[ "$MCP_PID_HEALTHY" -gt 0 ] || fail "MCP has no main PID"
[ "$TUNNEL_PID_BASE" -gt 0 ] || fail "tunnel has no main PID"

# Healthy manual guard run: prove the probe cleans its session and does not
# restart either managed runtime.
systemctl --user start "$GUARD_SERVICE"
[ -f "$EVIDENCE" ] || fail "guard did not persist structured evidence"
[ "$(json_value "$EVIDENCE" last_observation.classification)" = healthy ] \
  || fail "healthy guard run was not classified healthy"
[ "$(json_value "$EVIDENCE" last_observation.session_cleanup)" = deleted ] \
  || fail "healthy guard probe did not delete its session"
[ "$(json_value "$EVIDENCE" restart_attempt_count)" -eq 0 ] \
  || fail "healthy guard unexpectedly attempted recovery"
[ "$(systemctl --user show "$MCP_UNIT" -p MainPID --value)" = "$MCP_PID_HEALTHY" ] \
  || fail "healthy guard restarted MCP"
[ "$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)" = "$TUNNEL_PID_BASE" ] \
  || fail "healthy guard restarted tunnel"

BASE_DETECTIONS="$(json_value "$EVIDENCE" saturation_detection_count)"
BASE_RESTARTS="$(json_value "$EVIDENCE" restart_attempt_count)"
BASE_SUPPRESSIONS="$(json_value "$EVIDENCE" cooldown_suppression_count)"

saturate() {
  local session_file=$1
  python3 - "$MCP_PORT" "$session_file" <<'PY'
import io, json, pathlib, sys, urllib.error, urllib.request
port = int(sys.argv[1])
sessions = pathlib.Path(sys.argv[2])
url = f"http://127.0.0.1:{port}/mcp"
payload = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "workspace-mcp-manager-p11-qualification", "version": "1"},
    },
}, separators=(",", ":")).encode("utf-8")
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
    "Connection": "close",
}
opened = []
for attempt in range(1, 513):
    request = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise SystemExit(f"unexpected MCP admission status {response.status}")
            sid = response.headers.get("Mcp-Session-Id")
            if not sid:
                raise SystemExit("healthy initialize returned no session id")
            opened.append(sid)
            sessions.write_text("\n".join(opened) + "\n", encoding="utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read(4097).decode("utf-8", errors="replace").strip()
        if exc.code == 503 and body == "maximum HTTP session count reached":
            print(f"P11_SATURATION_SIGNATURE=PASS sessions={len(opened)}")
            raise SystemExit(0)
        raise SystemExit(f"unexpected admission failure status={exc.code} body={body!r}")
raise SystemExit("did not reach the MCP session limit within 512 sessions")
PY
}

saturate "$FIRST_SESSIONS"
MCP_PID_BEFORE_RECOVERY="$MCP_PID_HEALTHY"

# The timer, not this script, must perform the first recovery.
RECOVERED=0
for _ in $(seq 1 75); do
  if [ -f "$EVIDENCE" ]; then
    attempts="$(json_value "$EVIDENCE" restart_attempt_count || true)"
    result_ok="$(json_value "$EVIDENCE" last_restart_result.ok || true)"
    current_pid="$(systemctl --user show "$MCP_UNIT" -p MainPID --value 2>/dev/null || true)"
    if [ -n "$attempts" ] \
       && [ "$attempts" -ge $((BASE_RESTARTS + 1)) ] \
       && [ "$result_ok" = true ] \
       && [ -n "$current_pid" ] \
       && [ "$current_pid" -gt 0 ] \
       && [ "$current_pid" != "$MCP_PID_BEFORE_RECOVERY" ]; then
      RECOVERED=1
      break
    fi
  fi
  sleep 1
done
[ "$RECOVERED" -eq 1 ] || fail "30-second guard timer did not recover saturated MCP"

MCP_PID_AFTER_RECOVERY="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
[ "$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)" = "$TUNNEL_PID_BASE" ] \
  || fail "admission recovery restarted the tunnel"
[ "$(json_value "$EVIDENCE" last_saturation_signature)" = '503 maximum HTTP session count reached' ] \
  || fail "exact saturation signature was not persisted"
[ "$(json_value "$EVIDENCE" saturation_detection_count)" -ge $((BASE_DETECTIONS + 1)) ] \
  || fail "saturation detection count did not increment"
[ "$(json_value "$EVIDENCE" restart_attempt_count)" -eq $((BASE_RESTARTS + 1)) ] \
  || fail "unexpected number of first recovery attempts"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$MCP_PORT/.well-known/mcp.json" >/dev/null \
     && [ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/healthz")" = live ] \
     && [ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/readyz")" = ready ]; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$MCP_PORT/.well-known/mcp.json" >/dev/null \
  || fail "MCP discovery did not recover"
[ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/healthz")" = live ] \
  || fail "tunnel health did not recover"
[ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/readyz")" = ready ] \
  || fail "tunnel readiness did not recover"

# Saturate again while the 120-second cooldown is active. Run the guard
# manually so the assertion is deterministic and remains inside cooldown.
saturate "$SECOND_SESSIONS"
SECOND_PID_BEFORE="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
systemctl --user start "$GUARD_SERVICE"
[ "$(json_value "$EVIDENCE" restart_attempt_count)" -eq $((BASE_RESTARTS + 1)) ] \
  || fail "cooldown allowed a second MCP restart"
[ "$(json_value "$EVIDENCE" cooldown_suppression_count)" -ge $((BASE_SUPPRESSIONS + 1)) ] \
  || fail "cooldown suppression was not persisted"
[ "$(systemctl --user show "$MCP_UNIT" -p MainPID --value)" = "$SECOND_PID_BEFORE" ] \
  || fail "MCP restarted inside the cooldown"
[ "$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)" = "$TUNNEL_PID_BASE" ] \
  || fail "cooldown guard touched the tunnel"

delete_sessions "$SECOND_SESSIONS"
rm -f "$SECOND_SESSIONS"
systemctl --user start "$GUARD_SERVICE"

# Restore baseline and prove conditional guard resources are removed safely.
"$MANAGER" instance update "$BASELINE" >/dev/null
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null
RESTORE_NEEDED=0
[ ! -e "$GUARD_SERVICE_FILE" ] || fail "guard service remains after disabling P11"
[ ! -e "$GUARD_TIMER_FILE" ] || fail "guard timer remains after disabling P11"
systemctl --user is-active "$GUARD_TIMER" >/dev/null 2>&1 \
  && fail "guard timer remains active after disabling P11"

FINAL_FINGERPRINT="$("$MANAGER" instance show "$INSTANCE_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"
[ "$FINAL_FINGERPRINT" = "$BASELINE_FINGERPRINT" ] \
  || fail "P11 qualification changed the final desired fingerprint"

PLAN_JSON="$TMP_ROOT/final-plan.json"
"$MANAGER" instance plan "$INSTANCE_ID" >"$PLAN_JSON"
python3 - "$PLAN_JSON" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if not payload.get("ok") or not payload.get("valid"):
    raise SystemExit("final P11 plan is invalid")
bad = [item for item in payload["operations"] if item["operation"] != "NOOP"]
if bad:
    raise SystemExit(f"final P11 plan is not NOOP: {bad}")
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

curl -fsS "http://127.0.0.1:$MCP_PORT/.well-known/mcp.json" >/dev/null \
  || fail "final MCP discovery failed"
[ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/healthz")" = live ] \
  || fail "final tunnel health failed"
[ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/readyz")" = ready ] \
  || fail "final tunnel readiness failed"
[ -z "$(git -C "$SOURCE_REPO" status --porcelain --untracked-files=all)" ] \
  || fail "source repository is dirty after P11 qualification"

trap - EXIT
rm -rf "$TMP_ROOT"
printf 'P11_WSL_QUALIFICATION=PASS\n'
