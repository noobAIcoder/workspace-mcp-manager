#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
REPO="${REPO:-$HOME/repos/workspace-mcp-manager}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
MCP_PORT="${MCP_PORT:-7657}"
HEALTH_PORT="${HEALTH_PORT:-7373}"

fail() {
  printf 'PM2_FAIL=%s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable"
[ -d "$REPO/.git" ] || fail "manager repository is unavailable"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"
command -v script >/dev/null 2>&1 || fail "script(1) pseudo-TTY utility is unavailable"

TMP_ROOT="$(mktemp -d -t workspace-mcp-pm2-XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT

show_json="$($MANAGER instance show "$INSTANCE_ID")"
BASELINE_FINGERPRINT="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])' <<<"$show_json")"

TUI=(python3 -m workspace_mcp_manager.tui --manager "$MANAGER")

(
  cd "$REPO"
  PYTHONPATH="$REPO/src:$REPO:$REPO/tests" "${TUI[@]}" --snapshot
) >"$TMP_ROOT/dashboard.txt"
grep -F -- "- $INSTANCE_ID deployment=" "$TMP_ROOT/dashboard.txt" >/dev/null \
  || fail "dashboard snapshot does not list manager-qual"

(
  cd "$REPO"
  PYTHONPATH="$REPO/src:$REPO:$REPO/tests" "${TUI[@]}" --snapshot --instance "$INSTANCE_ID"
) >"$TMP_ROOT/instance.txt"
grep -Fx "instance=$INSTANCE_ID" "$TMP_ROOT/instance.txt" >/dev/null \
  || fail "instance snapshot did not resolve manager-qual"
grep -Fx "plan_valid=True non_noop=0" "$TMP_ROOT/instance.txt" >/dev/null \
  || fail "instance snapshot is not converged"

printf -v smoke_cmd 'cd %q && TERM=xterm-256color PYTHONPATH=%q python3 -m workspace_mcp_manager.tui --manager %q --smoke' \
  "$REPO" "$REPO/src:$REPO:$REPO/tests" "$MANAGER"
script -qfec "$smoke_cmd" /dev/null >"$TMP_ROOT/smoke.out" 2>&1 \
  || { tail -n 40 "$TMP_ROOT/smoke.out" >&2; fail "curses pseudo-TTY smoke failed"; }

TOOLKIT_HOME="$TMP_ROOT/toolkit-home"
TOOLKIT_PREFIX="$TOOLKIT_HOME/.local"
mkdir -p "$TOOLKIT_HOME"
python3 "$REPO/scripts/install_toolkit.py" \
  --repo "$REPO" \
  --account-home "$TOOLKIT_HOME" \
  --prefix "$TOOLKIT_PREFIX" \
  >"$TMP_ROOT/toolkit.json"
[ -x "$TOOLKIT_PREFIX/bin/workspace-mcp-manager-tui" ] \
  || fail "standalone toolkit did not install TUI entrypoint"
"$TOOLKIT_PREFIX/bin/workspace-mcp-manager-tui" --help >/dev/null \
  || fail "standalone toolkit TUI entrypoint does not execute"

final_show="$($MANAGER instance show "$INSTANCE_ID")"
FINAL_FINGERPRINT="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])' <<<"$final_show")"
[ "$FINAL_FINGERPRINT" = "$BASELINE_FINGERPRINT" ] \
  || fail "read-only TUI qualification changed manager-qual declaration"

$MANAGER instance plan "$INSTANCE_ID" >"$TMP_ROOT/final-plan.json"
python3 - "$TMP_ROOT/final-plan.json" <<'PY'
import json, pathlib, sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["valid"] is True, payload
assert payload["operations"], payload
assert {item["operation"] for item in payload["operations"]} == {"NOOP"}, payload
PY

curl -fsS "http://127.0.0.1:$MCP_PORT/.well-known/mcp.json" >/dev/null \
  || fail "MCP discovery is unhealthy"
[ "$(curl -fsS "http://127.0.0.1:$HEALTH_PORT/healthz")" = live ] \
  || fail "tunnel health is not live"
[ "$(curl -fsS "http://127.0.0.1:$HEALTH_PORT/readyz")" = ready ] \
  || fail "tunnel readiness is not ready"

if ! (
  cd "$REPO"
  PYTHONPATH="$REPO/src:$REPO:$REPO/tests" python3 -m unittest discover -s tests -v
) >"$TMP_ROOT/pure-suite.out" 2>&1; then
  tail -n 80 "$TMP_ROOT/pure-suite.out" >&2
  fail "complete pure test suite failed"
fi

printf 'PM2_SNAPSHOT=PASS\n'
printf 'PM2_INSTANCE_DETAIL=PASS\n'
printf 'PM2_CURSES_SMOKE=PASS\n'
printf 'PM2_TOOLKIT_ENTRYPOINT=PASS\n'
printf 'PM2_BASELINE_RESTORED=PASS\n'
printf 'PM2_PURE_SUITE=PASS\n'
printf 'PM2_TUI_QUALIFICATION=PASS\n'
