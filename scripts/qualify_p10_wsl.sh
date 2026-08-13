#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
WORKSPACE="${WORKSPACE:-/home/cloudtoor/repos/workspace-mcp-manager-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

if [ ! -x "$MANAGER" ]; then
  fail "manager executable is unavailable: $MANAGER"
fi

TMP_ROOT="$(mktemp -d -t workspace-mcp-p10-XXXXXX)"
SHOW_JSON="$TMP_ROOT/show.json"
VERSION_OUTPUT="$TMP_ROOT/codex-version.txt"

cleanup() {
  local rc=$?
  rm -f "$WORKSPACE/.p10-codex-write-check"
  rm -rf "$TMP_ROOT"
  return "$rc"
}
trap cleanup EXIT

"$MANAGER" instance show "$INSTANCE_ID" >"$SHOW_JSON"
BASELINE_FINGERPRINT="$(python3 - "$SHOW_JSON" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["fingerprint"])
PY
)"
EXPECTED_PATH="$(python3 - "$SHOW_JSON" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["desired"]["mcp"]["exec_path"])
PY
)"
CODEX_ENTRY="$(PATH="$EXPECTED_PATH" command -v codex || true)"

if [ -z "$CODEX_ENTRY" ] || [ ! -x "$CODEX_ENTRY" ]; then
  printf 'P10_EXTERNAL_CODEX_BLOCKED=missing-configured-executable\n' >&2
  printf 'configured_path=%s\n' "$EXPECTED_PATH" >&2
  exit 2
fi

if ! HOME="$HOME" CODEX_HOME="$HOME/.codex" PATH="$EXPECTED_PATH" "$CODEX_ENTRY" --version >"$VERSION_OUTPUT" 2>&1; then
  printf 'P10_EXTERNAL_CODEX_BLOCKED=version-preflight\n' >&2
  sed -n '1,20p' "$VERSION_OUTPUT" >&2 || true
  exit 2
fi

python3 - "$SHOW_JSON" "$CODEX_ENTRY" <<'PY'
import json, os, pathlib, sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
desired = payload["desired"]
entry = pathlib.Path(sys.argv[2])
resolved = pathlib.Path(os.path.realpath(entry))
roots = [pathlib.Path(os.path.realpath(p)) for p in desired["mcp"]["external_roots"]]
system_roots = tuple(pathlib.Path(p) for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64"))

def under(path, root):
    return path == root or root in path.parents

for candidate in (entry, resolved):
    if any(under(candidate, root) for root in system_roots):
        continue
    if not any(under(candidate, root) for root in roots):
        raise SystemExit(
            f"configured Codex path is outside declared external roots: {candidate}"
        )
PY

UNIT="$HOME/.config/systemd/user/coding-tools-mcp-$INSTANCE_ID.service"
grep -F "PATH=$EXPECTED_PATH" "$UNIT" >/dev/null || fail "generated MCP PATH differs from desired mcp.exec_path"

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

FINAL_FINGERPRINT="$("$MANAGER" instance show "$INSTANCE_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"
[ "$FINAL_FINGERPRINT" = "$BASELINE_FINGERPRINT" ] || fail "P10 qualification changed desired-state fingerprint"

cleanup
trap - EXIT
printf 'P10_WSL_QUALIFICATION=PASS\n'
