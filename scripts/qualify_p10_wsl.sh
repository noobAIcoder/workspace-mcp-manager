#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
WORKSPACE="${WORKSPACE:-/home/cloudtoor/repos/workspace-mcp-manager-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
CODEX_BIN_DIR="${CODEX_BIN_DIR:-$HOME/.local/lib/codex-wsl/bin}"
CODEX_ENTRY="${CODEX_ENTRY:-$CODEX_BIN_DIR/codex}"
CODEX_WRAPPER_ROOT="${CODEX_WRAPPER_ROOT:-$HOME/.local/lib/codex-wsl}"
CODEX_STANDALONE_ROOT="${CODEX_STANDALONE_ROOT:-$HOME/.codex/packages/standalone}"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

json_field() {
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d'"$1"')'
}

if [ ! -x "$MANAGER" ]; then
  fail "manager executable is unavailable: $MANAGER"
fi

if [ ! -x "$CODEX_ENTRY" ]; then
  printf 'P10_EXTERNAL_CODEX_BLOCKED=missing-executable\n' >&2
  printf 'codex_entry=%s\n' "$CODEX_ENTRY" >&2
  exit 2
fi

if ! CODEX_HOME="$HOME/.codex" "$CODEX_ENTRY" --version >/tmp/workspace-mcp-p10-codex-version.$$ 2>&1; then
  printf 'P10_EXTERNAL_CODEX_BLOCKED=version-preflight\n' >&2
  sed -n '1,20p' /tmp/workspace-mcp-p10-codex-version.$$ >&2 || true
  rm -f /tmp/workspace-mcp-p10-codex-version.$$
  exit 2
fi
rm -f /tmp/workspace-mcp-p10-codex-version.$$

TMP_ROOT="$(mktemp -d -t workspace-mcp-p10-XXXXXX)"
BASELINE="$TMP_ROOT/baseline.json"
UPDATED="$TMP_ROOT/updated.json"
RESTORE_NEEDED=0

cleanup() {
  local rc=$?
  rm -f "$WORKSPACE/.p10-codex-write-check"
  if [ "$RESTORE_NEEDED" -eq 1 ] && [ -f "$BASELINE" ]; then
    "$MANAGER" instance update "$BASELINE" >/dev/null 2>&1 || true
    "$MANAGER" instance apply "$INSTANCE_ID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
  return "$rc"
}
trap cleanup EXIT

"$MANAGER" instance show "$INSTANCE_ID" >"$TMP_ROOT/show.json"
python3 - "$TMP_ROOT/show.json" "$BASELINE" <<'PY'
import json, pathlib, sys
source = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
pathlib.Path(sys.argv[2]).write_text(
    json.dumps(source["desired"], indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

python3 - "$BASELINE" "$UPDATED" "$CODEX_BIN_DIR" "$CODEX_WRAPPER_ROOT" "$CODEX_STANDALONE_ROOT" <<'PY'
import json, os, pathlib, sys
raw = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
bin_dir, wrapper_root, standalone_root = sys.argv[3:6]
parts = [p for p in raw["mcp"]["exec_path"].split(os.pathsep) if p]
parts = [bin_dir, *[p for p in parts if p != bin_dir]]
raw["mcp"]["exec_path"] = os.pathsep.join(parts)
roots = list(raw["mcp"]["external_roots"])
for root in (wrapper_root, standalone_root):
    if root not in roots:
        roots.append(root)
raw["mcp"]["external_roots"] = roots
pathlib.Path(sys.argv[2]).write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

"$MANAGER" instance update "$UPDATED" >/dev/null
RESTORE_NEEDED=1
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

UNIT="$HOME/.config/systemd/user/coding-tools-mcp-$INSTANCE_ID.service"
grep -F "PATH=$CODEX_BIN_DIR:" "$UNIT" >/dev/null || fail "standalone Codex bin is missing from generated MCP PATH"
grep -F "$CODEX_WRAPPER_ROOT" "$UNIT" >/dev/null || fail "Codex wrapper root is missing from MCP external roots"
grep -F "$CODEX_STANDALONE_ROOT" "$UNIT" >/dev/null || fail "Codex standalone root is missing from MCP external roots"

start_job() {
  local mode=$1 prompt=$2 json
  json="$("$MANAGER" codex start "$INSTANCE_ID" --mode "$mode" --prompt "$prompt")"
  printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
}

job_state() {
  "$MANAGER" codex status "$INSTANCE_ID" "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])'
}

wait_terminal() {
  local job=$1 state i
  for i in $(seq 1 240); do
    state="$(job_state "$job")"
    case "$state" in
      succeeded|failed|cancelled|interrupted) printf '%s\n' "$state"; return 0 ;;
    esac
    sleep 1
  done
  fail "Codex job did not become terminal: $job"
}

READ_JOB="$(start_job read 'Read README.md and reply exactly P10_READ_OK. Do not modify files.')"
[ "$(wait_terminal "$READ_JOB")" = succeeded ] || fail "read Codex job failed"
"$MANAGER" codex output "$INSTANCE_ID" "$READ_JOB" --limit-bytes 65536 >/dev/null

WRITE_JOB="$(start_job write 'Create .p10-codex-write-check containing exactly P10_WRITE_OK followed by a newline, then reply exactly P10_WRITE_OK.')"
[ "$(wait_terminal "$WRITE_JOB")" = succeeded ] || fail "write Codex job failed"
[ "$(cat "$WORKSPACE/.p10-codex-write-check")" = P10_WRITE_OK ] || fail "write Codex job did not create expected file"
rm -f "$WORKSPACE/.p10-codex-write-check"

START_SECONDS="$(date +%s)"
CANCEL_JOB="$(start_job read 'Run the shell command sleep 60. After it completes, reply exactly P10_CANCEL_TIMEOUT.')"
ELAPSED=$(( $(date +%s) - START_SECONDS ))
[ "$ELAPSED" -lt 15 ] || fail "Codex submission waited for worker completion"
"$MANAGER" codex cancel "$INSTANCE_ID" "$CANCEL_JOB" >/dev/null
[ "$(job_state "$CANCEL_JOB")" = cancelled ] || fail "cancel did not persist cancelled state"

"$MANAGER" instance restart "$INSTANCE_ID" >/dev/null
[ "$(job_state "$READ_JOB")" = succeeded ] || fail "persisted Codex state was lost after instance restart"

"$MANAGER" instance plan "$INSTANCE_ID" >/dev/null
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

cleanup
trap - EXIT
printf 'P10_WSL_QUALIFICATION=PASS\n'
