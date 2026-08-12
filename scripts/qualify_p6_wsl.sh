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
EFFECTIVE_DECLARATION=

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

cleanup() {
  if [ -n "${EFFECTIVE_DECLARATION:-}" ] && [ "$EFFECTIVE_DECLARATION" != "$DECLARATION" ]; then
    rm -f -- "$EFFECTIVE_DECLARATION"
  fi
}
trap cleanup EXIT

manager_evidence() {
  printf '\n== manager status after failure ==\n' >&2
  workspace-mcp-manager instance status "$INSTANCE" --pretty >&2 || true
  printf '\n== manager logs after failure ==\n' >&2
  workspace-mcp-manager instance logs "$INSTANCE" --lines 120 --pretty >&2 || true
}

run_manager_mutation() {
  local label=$1
  shift
  section "$label"
  set +e
  workspace-mcp-manager "$@" --pretty
  local rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    manager_evidence
    fail "$label failed (exit $rc)"
  fi
}

cd "$REPO"
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"

section "preflight"
test -d "$QUAL_WORKSPACE" || fail "qualification workspace missing: $QUAL_WORKSPACE"
test -f "$DECLARATION" || fail "declaration missing: $DECLARATION"
command -v workspace-mcp-manager >/dev/null || fail "workspace-mcp-manager not installed"
command -v systemd-run >/dev/null || fail "systemd-run missing"
command -v systemd-analyze >/dev/null || fail "systemd-analyze missing"
command -v uv >/dev/null || fail "uv missing"

# Remote tunnel creation is external to workspace-mcp-manager. Never silently
# reuse the repository's historical/example tunnel ID during live qualification.
[ -n "$QUAL_TUNNEL_ID" ] || fail "set QUAL_TUNNEL_ID to a currently existing disposable tunnel ID"
case "$QUAL_TUNNEL_ID" in
  tunnel_[A-Za-z0-9]*) ;;
  *) fail "QUAL_TUNNEL_ID must look like tunnel_<id>" ;;
esac

EFFECTIVE_DECLARATION=$(mktemp /tmp/workspace-mcp-manager-qual.XXXXXX.json)
python3 - "$DECLARATION" "$EFFECTIVE_DECLARATION" "$QUAL_TUNNEL_ID" <<'PY'
import json, sys
src, dst, tunnel_id = sys.argv[1:]
with open(src, encoding="utf-8") as handle:
    data = json.load(handle)
data["tunnel"]["id"] = tunnel_id
# First converge the local deployment while stopped. This proves that the
# manager itself creates the profile/systemd resources independently of remote
# tunnel connectivity. The subsequent `instance start` is the first point at
# which OpenAI tunnel readiness is required.
data["lifecycle"] = {"deployment": "present", "runtime": "stopped"}
with open(dst, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

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

section "manager status before apply"
workspace-mcp-manager instance status "$INSTANCE" --pretty || true

section "host-side plan"
set +e
PLAN=$(workspace-mcp-manager instance plan "$INSTANCE" --pretty 2>&1)
PLAN_RC=$?
set -e
printf '%s\n' "$PLAN"
[ "$PLAN_RC" -eq 0 ] || { manager_evidence; fail "P6 plan is not valid (exit $PLAN_RC)"; }
pass "plan valid"

section "apply stopped declaration"
set +e
workspace-mcp-manager instance apply "$INSTANCE" --pretty
APPLY_RC=$?
set -e
if [ "$APPLY_RC" -ne 0 ]; then
  manager_evidence
  fail "stopped apply failed (exit $APPLY_RC)"
fi
! systemctl --user is-active --quiet "tunnel-client-$INSTANCE.service"
! systemctl --user is-active --quiet "coding-tools-mcp-$INSTANCE.service"
test -f "$HOME/.config/systemd/user/coding-tools-mcp-$INSTANCE.service"
test -f "$HOME/.config/systemd/user/tunnel-client-$INSTANCE.service"
test -f "$HOME/.config/workspace-mcp-manager/tunnel-profiles/workspace-mcp-manager-qual.yaml"
pass "local deployment created while stopped"

section "manager status after stopped apply"
workspace-mcp-manager instance status "$INSTANCE" --pretty

run_manager_mutation "start" instance start "$INSTANCE"

section "running qualification"
systemctl --user is-active "coding-tools-mcp-$INSTANCE.service" | grep -qx active
systemctl --user is-active "tunnel-client-$INSTANCE.service" | grep -qx active
curl -fsS "http://127.0.0.1:${MCP_PORT}/.well-known/mcp.json" >/dev/null
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/healthz" >/dev/null
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "start reached full readiness"

section "generated unit authority"
systemctl --user cat "tunnel-client-$INSTANCE.service" --no-pager | grep -F 'workspace-mcp-manager-instance=' >/dev/null
if systemctl --user cat "tunnel-client-$INSTANCE.service" --no-pager | grep -F 'workspace-mcp _run-tunnel' >/dev/null; then
  fail "legacy workspace-mcp wrapper present"
fi
pass "manager-owned tunnel service"

run_manager_mutation "stop" instance stop "$INSTANCE"
! systemctl --user is-active --quiet "tunnel-client-$INSTANCE.service"
! systemctl --user is-active --quiet "coding-tools-mcp-$INSTANCE.service"
pass "stop converged"

run_manager_mutation "start after stop" instance start "$INSTANCE"
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "start after stop converged"

section "restart"
MCP_PID_BEFORE=$(systemctl --user show "coding-tools-mcp-$INSTANCE.service" -p MainPID --value)
TUNNEL_PID_BEFORE=$(systemctl --user show "tunnel-client-$INSTANCE.service" -p MainPID --value)
set +e
workspace-mcp-manager instance restart "$INSTANCE" --pretty
RESTART_RC=$?
set -e
if [ "$RESTART_RC" -ne 0 ]; then
  manager_evidence
  fail "restart failed (exit $RESTART_RC)"
fi
MCP_PID_AFTER=$(systemctl --user show "coding-tools-mcp-$INSTANCE.service" -p MainPID --value)
TUNNEL_PID_AFTER=$(systemctl --user show "tunnel-client-$INSTANCE.service" -p MainPID --value)
[ "$MCP_PID_BEFORE" != "$MCP_PID_AFTER" ] || fail "MCP PID did not change on restart"
[ "$TUNNEL_PID_BEFORE" != "$TUNNEL_PID_AFTER" ] || fail "tunnel PID did not change on restart"
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "restart converged"

run_manager_mutation "remove" instance remove "$INSTANCE"
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
set +e
workspace-mcp-manager instance apply "$INSTANCE" --pretty
RECREATE_RC=$?
set -e
if [ "$RECREATE_RC" -ne 0 ]; then
  manager_evidence
  fail "recreate stopped apply failed (exit $RECREATE_RC)"
fi
run_manager_mutation "recreate start" instance start "$INSTANCE"
curl -fsS "http://127.0.0.1:${HEALTH_PORT}/readyz" >/dev/null
pass "recreate ready"

section "evidence"
workspace-mcp-manager instance status "$INSTANCE" --pretty
APPLIED="$HOME/.local/state/workspace-mcp-manager/applied/$INSTANCE.json"
test -f "$APPLIED" || fail "applied state missing"
ls -l "$APPLIED"
find "$HOME/.local/state/workspace-mcp-manager/transactions/$INSTANCE" -maxdepth 1 -type f -name '*.json' -printf '%f\n' | tail -20

printf '\nP6_WSL_QUALIFICATION=PASS\n'
