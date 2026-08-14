#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTANCE_ID=${PM3_INSTANCE_ID:-manager-qual}
SOURCE_MODE=${PM3_SOURCE_MODE:-0}
TMP=$(mktemp -d)
BASELINE_SHOW="$TMP/baseline-show.json"
BASELINE_DESIRED="$TMP/baseline-desired.json"
RESTORATION_ATTEMPTED=0
TOOLKIT_PREFIX="$TMP/toolkit"
RO_SOURCE="$TMP/ro-source"
RW_SOURCE="$TMP/rw-source"
mkdir -p "$RO_SOURCE" "$RW_SOURCE"

manager() {
  if [[ "$SOURCE_MODE" == "1" ]]; then
    PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m workspace_mcp_manager.cli "$@"
  else
    workspace-mcp-manager "$@"
  fi
}

json_field() {
  local path=$1
  python3 -c '
import json, sys
value = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    if isinstance(value, list):
        value = value[int(part)]
    else:
        value = value[part]
if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("")
else:
    print(value)
' "$path"
}

assert_json_expr() {
  local expression=$1
  python3 -c '
import json, sys
d = json.load(sys.stdin)
safe = {"all": all, "any": any, "bool": bool, "len": len}
if not eval(sys.argv[1], {"__builtins__": {}}, {"d": d, **safe}):
    raise SystemExit(f"assertion failed: {sys.argv[1]}\n{json.dumps(d, indent=2, sort_keys=True)}")
' "$expression"
}

expect_manager_error() {
  local expected=$1
  shift
  local output rc
  set +e
  output=$(manager "$@" 2>&1)
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    echo "PM3_FAIL=expected $expected from: $*" >&2
    return 1
  fi
  printf '%s\n' "$output" | python3 -c '
import json, sys
expected = sys.argv[1]
lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
for line in reversed(lines):
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        continue
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if error.get("code") == expected:
        raise SystemExit(0)
raise SystemExit(f"expected manager error {expected}, got: {lines[-1] if lines else '<empty>'}")
' "$expected"
}

plan_fingerprint() {
  manager instance plan "$INSTANCE_ID" | json_field plan_fingerprint
}

declaration_fingerprint() {
  manager instance show "$INSTANCE_ID" | json_field fingerprint
}

apply_reviewed_plan() {
  local fingerprint
  fingerprint=$(plan_fingerprint)
  manager instance apply "$INSTANCE_ID" --expected-plan-fingerprint "$fingerprint" >/dev/null
}

assert_all_noop() {
  manager instance plan "$INSTANCE_ID" | assert_json_expr \
    'd["valid"] is True and all(item.get("operation") == "NOOP" for item in d.get("operations", []))'
}

wait_summary() {
  local field=$1
  local expected=$2
  local attempts=${3:-30}
  local current
  for ((i=0; i<attempts; i++)); do
    current=$(manager instance summary "$INSTANCE_ID" | json_field "$field" || true)
    if [[ "$current" == "$expected" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "PM3_FAIL=summary $field did not become '$expected' (last='$current')" >&2
  return 1
}

restore_baseline() {
  local rc=0 current expected plan_fp
  RESTORATION_ATTEMPTED=1
  if [[ ! -s "$BASELINE_DESIRED" ]]; then
    return 0
  fi
  set +e
  current=$(declaration_fingerprint 2>/dev/null)
  if [[ -n "$current" ]]; then
    manager instance update "$BASELINE_DESIRED" --expected-current-fingerprint "$current" >/dev/null 2>&1
    rc=$?
  else
    rc=1
  fi
  if [[ $rc -eq 0 ]]; then
    plan_fp=$(plan_fingerprint 2>/dev/null)
    if [[ -n "$plan_fp" ]]; then
      manager instance apply "$INSTANCE_ID" --expected-plan-fingerprint "$plan_fp" >/dev/null 2>&1
      rc=$?
    else
      rc=1
    fi
  fi
  set -e
  return "$rc"
}

cleanup() {
  local original_rc=$?
  local restore_rc=0
  trap - EXIT INT TERM
  if [[ $RESTORATION_ATTEMPTED -eq 0 && -s "$BASELINE_DESIRED" ]]; then
    restore_baseline || restore_rc=$?
  fi
  rm -rf -- "$TMP"
  if [[ $restore_rc -ne 0 ]]; then
    echo "PM3_FAIL=baseline restoration failed" >&2
    exit "$restore_rc"
  fi
  exit "$original_rc"
}
trap cleanup EXIT INT TERM

echo "=== PM3 baseline ==="
manager instance show "$INSTANCE_ID" >"$BASELINE_SHOW"
python3 -c '
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(payload["desired"], handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
' "$BASELINE_SHOW" "$BASELINE_DESIRED"
BASELINE_FP=$(json_field fingerprint <"$BASELINE_SHOW")
BASELINE_SUMMARY=$(manager instance summary "$INSTANCE_ID")
BASELINE_PLAN=$(manager instance plan "$INSTANCE_ID")
BASELINE_ACCESS=$(manager access list "$INSTANCE_ID")
printf '%s\n' "$BASELINE_SUMMARY" | assert_json_expr 'd["projection_version"] == 1'
printf '%s\n' "$BASELINE_PLAN" | assert_json_expr 'len(d["plan_fingerprint"]) == 64'
printf '%s\n' "$BASELINE_ACCESS" | assert_json_expr 'd["instance_id"] == "manager-qual" or bool(d["instance_id"])'
assert_all_noop
echo "PM3_BASELINE=PASS"

echo "=== PM3 stale-state protection ==="
python3 -c '
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
d["lifecycle"]["runtime"] = "stopped" if d["lifecycle"]["runtime"] == "running" else "running"
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"), sort_keys=True, separators=(",", ":"))
' "$BASELINE_DESIRED" "$TMP/stale-declaration.json"
expect_manager_error STALE_STATE instance update "$TMP/stale-declaration.json" --expected-current-fingerprint "$(printf '0%.0s' {1..64})"
expect_manager_error STALE_STATE access add-ro "$INSTANCE_ID" pm3-stale-probe "$RO_SOURCE" --expected-current-fingerprint "$(printf '0%.0s' {1..64})"
expect_manager_error STALE_STATE instance apply "$INSTANCE_ID" --expected-plan-fingerprint "$(printf '0%.0s' {1..64})"
[[ "$(declaration_fingerprint)" == "$BASELINE_FP" ]]
echo "PM3_STALE_DECLARATION=PASS"
echo "PM3_STALE_ACCESS=PASS"
echo "PM3_STALE_REVIEWED_PLAN=PASS"

echo "=== PM3 reversible access reconciliation ==="
manager access add-ro "$INSTANCE_ID" pm3-qual-access "$RO_SOURCE" --expected-current-fingerprint "$BASELINE_FP" >/dev/null
manager instance plan "$INSTANCE_ID" | assert_json_expr 'd["valid"] is True and any(item.get("operation") != "NOOP" for item in d["operations"])'
apply_reviewed_plan
ACCESS_FP=$(declaration_fingerprint)
manager access update "$INSTANCE_ID" pm3-qual-access rw pm3-qual-access-rw "$RW_SOURCE" --expected-current-fingerprint "$ACCESS_FP" >/dev/null
apply_reviewed_plan
ACCESS_FP=$(declaration_fingerprint)
manager access remove "$INSTANCE_ID" pm3-qual-access-rw --expected-current-fingerprint "$ACCESS_FP" >/dev/null
apply_reviewed_plan
[[ "$(declaration_fingerprint)" == "$BASELINE_FP" ]]
assert_all_noop
echo "PM3_REVERSIBLE_ACCESS=PASS"
echo "PM3_ATOMIC_ACCESS_EDIT=PASS"

echo "=== PM3 lifecycle and semantic state ==="
manager instance stop "$INSTANCE_ID" >/dev/null
wait_summary desired_lifecycle "Present + Stopped"
wait_summary runtime "Stopped"
manager instance start "$INSTANCE_ID" >/dev/null
wait_summary desired_lifecycle "Present + Running"
wait_summary runtime "Running"
manager instance restart "$INSTANCE_ID" >/dev/null
wait_summary runtime "Running"
wait_summary tunnel "Ready" 45
assert_all_noop
echo "PM3_DESIRED_OBSERVED_STATE=PASS"
echo "PM3_LIFECYCLE_ACTIONS=PASS"

echo "=== PM3 diagnostics and logs ==="
manager instance diagnose "$INSTANCE_ID" | assert_json_expr 'd["instance_id"] == "manager-qual" or bool(d["instance_id"])'
for category in mcp tunnel recovery all; do
  manager instance logs "$INSTANCE_ID" --category "$category" --lines 20 | assert_json_expr 'd["ok"] is True'
done
echo "PM3_DIAGNOSTICS_LOGS=PASS"

echo "=== PM3 isolated Textual release and external contracts ==="
INSTALL_JSON=$(bash "$ROOT/scripts/install_toolkit.sh" --repo "$ROOT" --account-home "$HOME" --prefix "$TOOLKIT_PREFIX")
RELEASE_FP=$(printf '%s\n' "$INSTALL_JSON" | json_field installed_fingerprint)
RELEASE="$TOOLKIT_PREFIX/lib/workspace-mcp-manager/releases/$RELEASE_FP"
TUI="$TOOLKIT_PREFIX/bin/workspace-mcp-manager-tui"
[[ -d "$RELEASE/tui-runtime/site-packages/textual" ]]
bash "$ROOT/scripts/install_toolkit.sh" --repo "$ROOT" --account-home "$HOME" --prefix "$TOOLKIT_PREFIX" --check | assert_json_expr 'd["instances_valid"] is True and d["entrypoints_valid"] is True'
"$TUI" --smoke
"$TUI" --snapshot | grep -q '^instances='
"$TUI" --snapshot --instance "$INSTANCE_ID" | grep -q "^instance=$INSTANCE_ID$"
"$TUI" --snapshot --instance "$INSTANCE_ID" | grep -q '^plan_valid=True non_noop=0$'
echo "PM3_TEXTUAL_RUNTIME=PASS"
echo "PM3_PM2_EXTERNAL_CONTRACTS=PASS"

if command -v script >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  set +e
  (sleep 2; printf '\003') | timeout 10 script -qfec "$TUI" /dev/null >/dev/null 2>&1
  PTY_RC=$?
  set -e
  if [[ $PTY_RC -ne 0 && $PTY_RC -ne 130 ]]; then
    echo "PM3_FAIL=pseudo-TTY launch returned $PTY_RC" >&2
    exit 1
  fi
  echo "PM3_PSEUDO_TTY=PASS"
else
  echo "PM3_FAIL=pseudo-TTY qualification requires script and timeout" >&2
  exit 1
fi

echo "=== PM3 verification ==="
PYTHONPATH="$ROOT/src:$ROOT" python3 -m unittest discover -s "$ROOT/tests"
SITE="$RELEASE/tui-runtime/site-packages" ROOT="$ROOT" python3 -I -S -c '
import os, sys, unittest
sys.path[:0] = [os.environ["SITE"], os.path.join(os.environ["ROOT"], "src"), os.environ["ROOT"]]
suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_tui_textual")
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'
echo "PM3_PURE_VERIFICATION=PASS"
echo "PM3_TEXTUAL_PILOT=PASS"

echo "=== PM3 final restoration gate ==="
restore_baseline
[[ "$(declaration_fingerprint)" == "$BASELINE_FP" ]]
assert_all_noop
wait_summary runtime "Running"
wait_summary tunnel "Ready" 45
FINAL_SUMMARY=$(manager instance summary "$INSTANCE_ID")
printf '%s\n' "$FINAL_SUMMARY" | assert_json_expr 'd["recovery"] == "Clean"'
echo "PM3_BASELINE_RESTORED=PASS"
echo "PM3_LIVE_WSL_QUALIFICATION=PASS"
echo "PM3_OPERATOR_UX_QUALIFICATION=PASS"
