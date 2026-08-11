#!/usr/bin/env bash
set -Eeuo pipefail
export SYSTEMD_PAGER=cat

REPO=${REPO:-/home/cloudtoor/repos/workspace-mcp-manager}
QUAL_WORKSPACE=${QUAL_WORKSPACE:-/home/cloudtoor/repos/workspace-mcp-manager-qual}
DECLARATION=${DECLARATION:-$REPO/examples/registry/manager-qual.json}
QUAL_TUNNEL_ID=${QUAL_TUNNEL_ID:-}
MCP_PORT=${MCP_PORT:-7657}
HEALTH_PORT=${HEALTH_PORT:-7373}
INSTANCE=${INSTANCE:-manager-qual}

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

cd "$REPO"

section "preflight"
test -d "$QUAL_WORKSPACE" || fail "qualification workspace missing: $QUAL_WORKSPACE"
test -f "$DECLARATION" || fail "declaration missing: $DECLARATION"
if [ -n "$QUAL_TUNNEL_ID" ]; then
  EFFECTIVE_DECLARATION=$(mktemp /tmp/workspace-mcp-manager-qual.XXXXXX.json)
  python3 - "$DECLARATION" "$EFFECTIVE_DECLARATION" "$QUAL_TUNNEL_ID" <<'PY'
import json, sys
src, dst, tunnel_id = sys.argv[1:]
data = json.load(open(src, encoding="utf-8"))
data["tunnel"]["id"] = tunnel_id
with open(dst, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
else
  EFFECTIVE_DECLARATION=$DECLARATION
fi
command -v workspace-mcp-manager >/dev/null || fail "workspace-mcp-manager not installed"
command -v systemd-run >/dev/null || fail "systemd-run missing"
command -v systemd-analyze >/dev/null || fail "systemd-analyze missing"

python3 -m unittest discover -s tests -v
pass "unit tests"
git diff --check
pass "git diff --check"

section "install editable tool"
uv tool install --editable . --force >/dev/null
command -v workspace-mcp-manager
pass "manager executable installed"

section "desired registry"
if workspace-mcp-manager instance show "$INSTANCE" >/dev/null 2>&1; then
  workspace-mcp-manager instance update "$EFFECTIVE_DECLARATION" --pretty
else
  workspace-mcp-manager instance create "$EFFECTIVE_DECLARATION" --pretty
fi

section "host-side plan"
set +e
PLAN=$(workspace-mcp-manager instance plan "$INSTANCE" --pretty 2>&1)
PLAN_RC=$?
set -e
printf '%s\n' "$PLAN"
[ "$PLAN_RC" -eq 0 ] || fail "P6 plan is not valid (exit $PLAN_RC)"
pass "plan valid"

section "apply"
workspace-mcp-manager instance apply "$INSTANCE" --pretty

section "running qualification"
systemctl --user is-active "coding-tools-mcp-$INSTANCE.service" | grep -qx active
systemctl --user is-active "tunnel-client-$INSTANCE.service" | grep -qx active
curl -fsS "http://127.0.0.1:${MCP_PORT}/.well-known/mcp.json" >/dev/null
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/healthz" >/dev/null
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "initial apply ready"

section "generated unit authority"
systemctl --user cat "tunnel-client-$INSTANCE.service" --no-pager | grep -F 'workspace-mcp-manager-instance=' >/dev/null
systemctl --user cat "tunnel-client-$INSTANCE.service" --no-pager | grep -F 'workspace-mcp _run-tunnel' >/dev/null && fail "legacy workspace-mcp wrapper present"
pass "manager-owned tunnel service"

section "stop"
workspace-mcp-manager instance stop "$INSTANCE" --pretty
! systemctl --user is-active --quiet "tunnel-client-$INSTANCE.service"
! systemctl --user is-active --quiet "coding-tools-mcp-$INSTANCE.service"
pass "stop converged"

section "start"
workspace-mcp-manager instance start "$INSTANCE" --pretty
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "start converged"

section "restart"
MCP_PID_BEFORE=$(systemctl --user show "coding-tools-mcp-$INSTANCE.service" -p MainPID --value)
TUNNEL_PID_BEFORE=$(systemctl --user show "tunnel-client-$INSTANCE.service" -p MainPID --value)
workspace-mcp-manager instance restart "$INSTANCE" --pretty
MCP_PID_AFTER=$(systemctl --user show "coding-tools-mcp-$INSTANCE.service" -p MainPID --value)
TUNNEL_PID_AFTER=$(systemctl --user show "tunnel-client-$INSTANCE.service" -p MainPID --value)
[ "$MCP_PID_BEFORE" != "$MCP_PID_AFTER" ] || fail "MCP PID did not change on restart"
[ "$TUNNEL_PID_BEFORE" != "$TUNNEL_PID_AFTER" ] || fail "tunnel PID did not change on restart"
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "restart converged"

section "remove"
workspace-mcp-manager instance remove "$INSTANCE" --pretty
! systemctl --user is-active --quiet "tunnel-client-$INSTANCE.service"
! systemctl --user is-active --quiet "coding-tools-mcp-$INSTANCE.service"
test ! -e "$HOME/.config/systemd/user/coding-tools-mcp-$INSTANCE.service"
test ! -e "$HOME/.config/systemd/user/tunnel-client-$INSTANCE.service"
test ! -e "$HOME/.config/workspace-mcp-manager/tunnel-profiles/workspace-mcp-manager-qual.yaml"
ss -H -ltn | grep -Eq ":${MCP_PORT}([[:space:]]|$)" && fail "MCP port still listening"
ss -H -ltn | grep -Eq ":${HEALTH_PORT}([[:space:]]|$)" && fail "health port still listening"
pass "remove converged"

section "recreate identically"
workspace-mcp-manager instance update "$EFFECTIVE_DECLARATION" --pretty
workspace-mcp-manager instance apply "$INSTANCE" --pretty
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "recreate ready"

section "evidence"
APPLIED="$HOME/.local/state/workspace-mcp-manager/applied/$INSTANCE.json"
test -f "$APPLIED" || fail "applied state missing"
ls -l "$APPLIED"
find "$HOME/.local/state/workspace-mcp-manager/transactions/$INSTANCE" -maxdepth 1 -type f -name '*.json' -printf '%f\n' | tail -20

printf '\nP6_WSL_QUALIFICATION=PASS\n'
