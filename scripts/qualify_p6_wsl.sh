#!/usr/bin/env bash
set -Eeuo pipefail
export SYSTEMD_PAGER=cat

REPO=${REPO:-/home/cloudtoor/repos/workspace-mcp-manager}
QUAL_WORKSPACE=${QUAL_WORKSPACE:-/home/cloudtoor/repos/workspace-mcp-manager-qual}
DECLARATION=${DECLARATION:-$REPO/examples/registry/manager-qual.json}
QUAL_TUNNEL_ID=${QUAL_TUNNEL_ID:-}
QUAL_NVM_BIN=${QUAL_NVM_BIN:-}
MCP_PORT=${MCP_PORT:-7657}
HEALTH_PORT=${HEALTH_PORT:-7373}
INSTANCE=${INSTANCE:-manager-qual}
EFFECTIVE_DECLARATION=
EVIDENCE_DIR=
STATE_DIR="$HOME/.local/state/workspace-mcp-manager/instances/$INSTANCE"
OWNER_PATH="$STATE_DIR/.owner"
APPLIED="$HOME/.local/state/workspace-mcp-manager/applied/$INSTANCE.json"
PROFILE="$HOME/.config/workspace-mcp-manager/tunnel-profiles/workspace-mcp-manager-qual.yaml"
MCP_UNIT="coding-tools-mcp-$INSTANCE.service"
TUNNEL_UNIT="tunnel-client-$INSTANCE.service"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

cleanup() {
  if [ -n "${EFFECTIVE_DECLARATION:-}" ]; then
    rm -f -- "$EFFECTIVE_DECLARATION"
  fi
  if [ -n "${EVIDENCE_DIR:-}" ]; then
    rm -rf -- "$EVIDENCE_DIR"
  fi
}
trap cleanup EXIT

manager_evidence() {
  printf '\n== manager status after failure ==\n' >&2
  workspace-mcp-manager instance status "$INSTANCE" --pretty >&2 || true
  printf '\n== manager logs after failure ==\n' >&2
  workspace-mcp-manager instance logs "$INSTANCE" --lines 120 --pretty >&2 || true
}

run_capture() {
  local label=$1
  local output=$2
  shift 2
  section "$label"
  set +e
  "$@" >"$output"
  local rc=$?
  set -e
  cat "$output"
  if [ "$rc" -ne 0 ]; then
    manager_evidence
    fail "$label failed (exit $rc)"
  fi
}

assert_status() {
  local output=$1
  local deployment=$2
  local runtime=$3
  python3 - "$output" "$deployment" "$runtime" <<'PY'
import json, sys
path, deployment, runtime = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("ok") is not True:
    raise SystemExit("status ok=false")
lifecycle = data.get("lifecycle") or {}
if lifecycle.get("deployment") != deployment or lifecycle.get("runtime") != runtime:
    raise SystemExit(f"unexpected lifecycle: {lifecycle!r}")
operations = (data.get("plan") or {}).get("operations") or []
non_noop = [item for item in operations if item.get("operation") != "NOOP"]
if non_noop:
    raise SystemExit(f"status is not converged: {non_noop!r}")
PY
}

assert_running_endpoints() {
  curl -fsS "http://127.0.0.1:${MCP_PORT}/.well-known/mcp.json" >/dev/null
  curl -fsS "http://127.0.0.1:${HEALTH_PORT}/healthz" >/dev/null
  curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
}

assert_transaction_shape() {
  local output=$1
  python3 - "$output" <<'PY'
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)
path = result.get("journal")
if not path or not os.path.isfile(path):
    raise SystemExit("lifecycle result does not reference a transaction journal")
with open(path, encoding="utf-8") as handle:
    journal = json.load(handle)
if journal.get("status") != "succeeded":
    raise SystemExit(f"transaction is not succeeded: {journal.get('status')!r}")
if not isinstance(journal.get("attempted"), list):
    raise SystemExit("transaction attempted[] missing")
if not isinstance(journal.get("executed"), list):
    raise SystemExit("transaction executed[] missing")
PY
}

snapshot_other_instances() {
  local unit active enabled digest
  systemctl --user list-unit-files --no-legend --no-pager \
    'coding-tools-mcp-*.service' 'tunnel-client-*.service' 2>/dev/null \
    | awk '{print $1}' | sort -u \
    | while read -r unit; do
        [ -n "$unit" ] || continue
        case "$unit" in
          "$MCP_UNIT"|"$TUNNEL_UNIT") continue ;;
        esac
        active=$(systemctl --user is-active "$unit" 2>/dev/null || true)
        enabled=$(systemctl --user is-enabled "$unit" 2>/dev/null || true)
        digest=$(systemctl --user cat "$unit" --no-pager 2>/dev/null | sha256sum | awk '{print $1}')
        printf '%s\t%s\t%s\t%s\n' "$unit" "$active" "$enabled" "$digest"
      done
}

resolve_nvm_toolchain() {
  local node_path node_real tool
  if [ -z "$QUAL_NVM_BIN" ]; then
    node_path=$(command -v node || true)
    [ -n "$node_path" ] || fail "node is not on PATH; set QUAL_NVM_BIN to an NVM version bin directory"
    node_real=$(readlink -f "$node_path")
    case "$node_real" in
      "$HOME"/.nvm/versions/node/*/bin/node)
        QUAL_NVM_BIN=${node_real%/node}
        ;;
      *)
        fail "current node is not NVM-managed; set QUAL_NVM_BIN to ~/.nvm/versions/node/<version>/bin"
        ;;
    esac
  fi
  QUAL_NVM_BIN=$(readlink -f "$QUAL_NVM_BIN")
  case "$QUAL_NVM_BIN" in
    "$HOME"/.nvm/versions/node/*/bin) ;;
    *) fail "QUAL_NVM_BIN must resolve under ~/.nvm/versions/node/<version>/bin" ;;
  esac
  QUAL_NVM_ROOT=${QUAL_NVM_BIN%/bin}
  export QUAL_NVM_BIN QUAL_NVM_ROOT
  for tool in node npm npx corepack pnpm; do
    [ -x "$QUAL_NVM_BIN/$tool" ] || fail "NVM toolchain executable missing: $QUAL_NVM_BIN/$tool"
  done
  pass "NVM toolchain: $QUAL_NVM_ROOT"
}

cd "$REPO"
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"

section "preflight"
test -d "$QUAL_WORKSPACE" || fail "qualification workspace missing: $QUAL_WORKSPACE"
test -f "$DECLARATION" || fail "declaration missing: $DECLARATION"
command -v workspace-mcp-manager >/dev/null || fail "workspace-mcp-manager not installed"
command -v systemd-run >/dev/null || fail "systemd-run missing"
command -v systemd-analyze >/dev/null || fail "systemd-analyze missing"
command -v systemctl >/dev/null || fail "systemctl missing"
command -v curl >/dev/null || fail "curl missing"
command -v ss >/dev/null || fail "ss missing"
command -v readlink >/dev/null || fail "readlink missing"
command -v sha256sum >/dev/null || fail "sha256sum missing"
command -v uv >/dev/null || fail "uv missing"

[ -n "$QUAL_TUNNEL_ID" ] || fail "set QUAL_TUNNEL_ID to a currently existing disposable tunnel ID"
case "$QUAL_TUNNEL_ID" in
  tunnel_[A-Za-z0-9]*) ;;
  *) fail "QUAL_TUNNEL_ID must look like tunnel_<id>" ;;
esac
resolve_nvm_toolchain

# The first-apply test starts only from a clean disposable instance or from the
# established failed-first-apply residue. Do not normalize it with manual unit,
# profile, state, or applied-state deletion here.
test ! -f "$APPLIED" || fail "applied state already exists; manager-qual is not at the P6 first-apply baseline"
test ! -e "$HOME/.config/systemd/user/$MCP_UNIT" || fail "MCP unit already deployed before first apply"
test ! -e "$HOME/.config/systemd/user/$TUNNEL_UNIT" || fail "tunnel unit already deployed before first apply"
test ! -e "$PROFILE" || fail "tunnel profile already deployed before first apply"

RECOVERY_EXPECTED=false
if [ -d "$STATE_DIR" ] && [ ! -e "$OWNER_PATH" ]; then
  RECOVERY_EXPECTED=true
fi

EVIDENCE_DIR=$(mktemp -d /tmp/workspace-mcp-manager-p6.XXXXXX)
EFFECTIVE_DECLARATION=$(mktemp /tmp/workspace-mcp-manager-qual.XXXXXX.json)
python3 - "$DECLARATION" "$EFFECTIVE_DECLARATION" "$QUAL_TUNNEL_ID" "$QUAL_NVM_BIN" "$QUAL_NVM_ROOT" <<'PY'
import json, os, sys
src, dst, tunnel_id, nvm_bin, nvm_root = sys.argv[1:]
with open(src, encoding="utf-8") as handle:
    data = json.load(handle)
data["tunnel"]["id"] = tunnel_id
if data.get("lifecycle") != {"deployment": "present", "runtime": "running"}:
    raise SystemExit("manager-qual declaration must remain present+running for P6 qualification")
entries = data["mcp"]["exec_path"].split(os.pathsep)
data["mcp"]["exec_path"] = os.pathsep.join([nvm_bin, *[item for item in entries if item != nvm_bin]])
roots = data["mcp"]["external_roots"]
data["mcp"]["external_roots"] = [nvm_root, *[item for item in roots if item != nvm_root]]
with open(dst, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

OTHER_BEFORE="$EVIDENCE_DIR/other-before.txt"
snapshot_other_instances >"$OTHER_BEFORE"

section "pure qualification"
PYTHONPATH=src python3 -m unittest discover -s tests -v
pass "unit tests"
git diff --check
pass "git diff --check"

section "install editable manager"
uv tool install --editable . --force >/dev/null
command -v workspace-mcp-manager
pass "manager executable installed"

section "desired registry"
if workspace-mcp-manager instance show "$INSTANCE" >/dev/null 2>&1; then
  workspace-mcp-manager instance update "$EFFECTIVE_DECLARATION" --pretty
else
  workspace-mcp-manager instance create "$EFFECTIVE_DECLARATION" --pretty
fi

run_capture "plan" "$EVIDENCE_DIR/plan.json" \
  workspace-mcp-manager instance plan "$INSTANCE" --pretty

run_capture "first apply" "$EVIDENCE_DIR/apply-1.json" \
  workspace-mcp-manager instance apply "$INSTANCE" --pretty
assert_transaction_shape "$EVIDENCE_DIR/apply-1.json"

if [ "$RECOVERY_EXPECTED" = true ]; then
  python3 - "$EVIDENCE_DIR/apply-1.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
recovery = data.get("residual_recovery")
if not isinstance(recovery, dict) or recovery.get("recovered") is not True:
    raise SystemExit("known failed-first-apply residue was not recovered")
PY
  pass "failed-first-apply residue recovered"
fi

test -d "$STATE_DIR"
test -f "$OWNER_PATH"
grep -Fx "workspace-mcp-manager-instance=$INSTANCE" "$OWNER_PATH" >/dev/null
[ "$(stat -c '%a' "$STATE_DIR")" = 700 ] || fail "state directory mode is not 700"
[ "$(stat -c '%a' "$OWNER_PATH")" = 600 ] || fail "ownership marker mode is not 600"
test -f "$HOME/.config/systemd/user/$MCP_UNIT"
test -f "$HOME/.config/systemd/user/$TUNNEL_UNIT"
test -f "$PROFILE"
assert_running_endpoints
pass "first apply created manager-owned resources and reached readiness"

run_capture "status after first apply" "$EVIDENCE_DIR/status-1.json" \
  workspace-mcp-manager instance status "$INSTANCE" --pretty
assert_status "$EVIDENCE_DIR/status-1.json" present running

section "generated service authority and NVM toolchain"
MCP_UNIT_TEXT=$(systemctl --user cat "$MCP_UNIT" --no-pager)
TUNNEL_UNIT_TEXT=$(systemctl --user cat "$TUNNEL_UNIT" --no-pager)
printf '%s\n' "$MCP_UNIT_TEXT" | grep -F "workspace-mcp-manager-instance=$INSTANCE" >/dev/null
printf '%s\n' "$MCP_UNIT_TEXT" | grep -F "$QUAL_NVM_BIN" >/dev/null
printf '%s\n' "$MCP_UNIT_TEXT" | grep -F "CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=$QUAL_NVM_ROOT" >/dev/null
printf '%s\n' "$TUNNEL_UNIT_TEXT" | grep -F "workspace-mcp-manager-instance=$INSTANCE" >/dev/null
printf '%s\n' "$TUNNEL_UNIT_TEXT" | grep -F '_runtime tunnel' >/dev/null
if printf '%s\n' "$TUNNEL_UNIT_TEXT" | grep -F 'workspace-mcp _run-tunnel' >/dev/null; then
  fail "legacy workspace-mcp tunnel wrapper present"
fi
pass "manager-owned services include NVM PATH/root and no legacy tunnel wrapper"

run_capture "second apply (NOOP)" "$EVIDENCE_DIR/apply-2.json" \
  workspace-mcp-manager instance apply "$INSTANCE" --pretty
assert_transaction_shape "$EVIDENCE_DIR/apply-2.json"
python3 - "$EVIDENCE_DIR/apply-2.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)
with open(result["journal"], encoding="utf-8") as handle:
    journal = json.load(handle)
prohibited = {
    "CREATE", "UPDATE", "ENABLE", "DISABLE", "START", "STOP",
    "RESTART", "REMOVE", "DAEMON_RELOAD", "FORCE_RESTART",
}
mutations = [item for item in journal.get("attempted", []) if item.get("operation") in prohibited]
if mutations:
    raise SystemExit(f"second apply was not a managed-resource NOOP: {mutations!r}")
PY
pass "second apply is effectively NOOP"

run_capture "status after NOOP apply" "$EVIDENCE_DIR/status-2.json" \
  workspace-mcp-manager instance status "$INSTANCE" --pretty
assert_status "$EVIDENCE_DIR/status-2.json" present running

run_capture "stop" "$EVIDENCE_DIR/stop.json" \
  workspace-mcp-manager instance stop "$INSTANCE" --pretty
assert_transaction_shape "$EVIDENCE_DIR/stop.json"
! systemctl --user is-active --quiet "$TUNNEL_UNIT"
! systemctl --user is-active --quiet "$MCP_UNIT"
run_capture "status after stop" "$EVIDENCE_DIR/status-stop.json" \
  workspace-mcp-manager instance status "$INSTANCE" --pretty
assert_status "$EVIDENCE_DIR/status-stop.json" present stopped
pass "stop converged"

run_capture "start" "$EVIDENCE_DIR/start.json" \
  workspace-mcp-manager instance start "$INSTANCE" --pretty
assert_transaction_shape "$EVIDENCE_DIR/start.json"
assert_running_endpoints
run_capture "status after start" "$EVIDENCE_DIR/status-start.json" \
  workspace-mcp-manager instance status "$INSTANCE" --pretty
assert_status "$EVIDENCE_DIR/status-start.json" present running
pass "start converged"

section "restart"
MCP_PID_BEFORE=$(systemctl --user show "$MCP_UNIT" -p MainPID --value)
TUNNEL_PID_BEFORE=$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)
run_capture "restart mutation" "$EVIDENCE_DIR/restart.json" \
  workspace-mcp-manager instance restart "$INSTANCE" --pretty
assert_transaction_shape "$EVIDENCE_DIR/restart.json"
MCP_PID_AFTER=$(systemctl --user show "$MCP_UNIT" -p MainPID --value)
TUNNEL_PID_AFTER=$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)
[ "$MCP_PID_BEFORE" != "$MCP_PID_AFTER" ] || fail "MCP PID did not change on restart"
[ "$TUNNEL_PID_BEFORE" != "$TUNNEL_PID_AFTER" ] || fail "tunnel PID did not change on restart"
assert_running_endpoints
run_capture "status after restart" "$EVIDENCE_DIR/status-restart.json" \
  workspace-mcp-manager instance status "$INSTANCE" --pretty
assert_status "$EVIDENCE_DIR/status-restart.json" present running
pass "restart converged"

run_capture "remove" "$EVIDENCE_DIR/remove.json" \
  workspace-mcp-manager instance remove "$INSTANCE" --pretty
assert_transaction_shape "$EVIDENCE_DIR/remove.json"
! systemctl --user is-active --quiet "$TUNNEL_UNIT"
! systemctl --user is-active --quiet "$MCP_UNIT"
test ! -e "$HOME/.config/systemd/user/$MCP_UNIT"
test ! -e "$HOME/.config/systemd/user/$TUNNEL_UNIT"
test ! -e "$PROFILE"
test ! -e "$STATE_DIR"
test -d "$HOME/.config/workspace-mcp-manager/tunnel-profiles"
ss -H -ltn | grep -Eq ":${MCP_PORT}([[:space:]]|$)" && fail "MCP port still listening"
ss -H -ltn | grep -Eq ":${HEALTH_PORT}([[:space:]]|$)" && fail "health port still listening"
run_capture "status after remove" "$EVIDENCE_DIR/status-remove.json" \
  workspace-mcp-manager instance status "$INSTANCE" --pretty
assert_status "$EVIDENCE_DIR/status-remove.json" absent stopped
python3 - "$APPLIED" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("deployment") != "absent" or data.get("runtime") != "stopped":
    raise SystemExit(f"unexpected applied remove state: {data!r}")
if data.get("owned_resources") != []:
    raise SystemExit("removed applied state still claims owned resources")
PY
OTHER_AFTER_REMOVE="$EVIDENCE_DIR/other-after-remove.txt"
snapshot_other_instances >"$OTHER_AFTER_REMOVE"
cmp -s "$OTHER_BEFORE" "$OTHER_AFTER_REMOVE" || {
  diff -u "$OTHER_BEFORE" "$OTHER_AFTER_REMOVE" >&2 || true
  fail "remove changed another managed instance"
}
pass "remove affected only manager-qual and retained shared profile directory"

section "restore present+running desired state through manager"
workspace-mcp-manager instance update "$EFFECTIVE_DECLARATION" --pretty

run_capture "recreate apply" "$EVIDENCE_DIR/recreate.json" \
  workspace-mcp-manager instance apply "$INSTANCE" --pretty
assert_transaction_shape "$EVIDENCE_DIR/recreate.json"
assert_running_endpoints
run_capture "status after recreate" "$EVIDENCE_DIR/status-recreate.json" \
  workspace-mcp-manager instance status "$INSTANCE" --pretty
assert_status "$EVIDENCE_DIR/status-recreate.json" present running

test -f "$APPLIED"
python3 - "$APPLIED" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("deployment") != "present" or data.get("runtime") != "running":
    raise SystemExit(f"unexpected recreated applied state: {data!r}")
if not data.get("owned_resources"):
    raise SystemExit("recreated applied state has no owned resources")
PY

OTHER_AFTER_RECREATE="$EVIDENCE_DIR/other-after-recreate.txt"
snapshot_other_instances >"$OTHER_AFTER_RECREATE"
cmp -s "$OTHER_BEFORE" "$OTHER_AFTER_RECREATE" || {
  diff -u "$OTHER_BEFORE" "$OTHER_AFTER_RECREATE" >&2 || true
  fail "qualification changed another managed instance"
}

section "transaction evidence"
find "$HOME/.local/state/workspace-mcp-manager/transactions/$INSTANCE" \
  -maxdepth 1 -type f -name '*.json' -printf '%f\n' | tail -30

printf '\nP6_WSL_QUALIFICATION=PASS\n'
