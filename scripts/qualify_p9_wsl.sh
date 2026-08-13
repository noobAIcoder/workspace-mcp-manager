#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
WORKSPACE="${WORKSPACE:-/home/cloudtoor/repos/workspace-mcp-manager-qual}"
REMOTE_NAME="${P9_REMOTE_NAME:-p9-qual}"
REMOTE_URL="${P9_REMOTE_URL:-https://github.com/noobAIcoder/workspace-mcp-manager.git}"
MANAGER_BIN="${MANAGER_BIN:-$HOME/.local/bin/workspace-mcp-manager}"

fail() {
  printf 'P9_FAIL: %s\n' "$*" >&2
  exit 1
}

assert_clean_git() {
  local dirty
  dirty="$(git -C "$WORKSPACE" status --porcelain --untracked-files=all)"
  [ -z "$dirty" ] || fail "qualification workspace is dirty: $dirty"
}

[ -d "$WORKSPACE/.git" ] || fail "qualification workspace is not a Git repository"
[ -x "$MANAGER_BIN" ] || fail "manager executable is missing: $MANAGER_BIN"
assert_clean_git

if git -C "$WORKSPACE" remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  fail "temporary remote already exists: $REMOTE_NAME"
fi

cleanup() {
  if git -C "$WORKSPACE" remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    git -C "$WORKSPACE" remote remove "$REMOTE_NAME"
  fi
}
trap cleanup EXIT

git -C "$WORKSPACE" remote add "$REMOTE_NAME" "$REMOTE_URL"

desired_json="$("$MANAGER_BIN" instance show "$INSTANCE_ID")"
expected_path="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["desired"]["mcp"]["exec_path"])' <<<"$desired_json")"
real_home="$(python3 -c 'import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')"
payload="$("$MANAGER_BIN" instance git "$INSTANCE_ID")"

python3 - "$real_home" "$expected_path" "$REMOTE_NAME" "$payload" <<'PY'
import json
import sys

real_home, expected_path, remote_name, raw = sys.argv[1:]
payload = json.loads(raw)

def require(condition, message):
    if not condition:
        raise SystemExit(f"P9_FAIL: {message}")

require(payload.get("ok") is True, "instance git diagnostic returned ok=false")
require(payload.get("repository", {}).get("present") is True, "Git repository was not detected")
require(payload.get("working_tree", {}).get("clean") is True, "qualification Git working tree is not clean")
require(payload.get("environment", {}).get("home") == real_home, "diagnostic HOME is not the real account home")
require(payload.get("environment", {}).get("path") == expected_path, "diagnostic PATH differs from desired MCP PATH")
remote = payload.get("remote", {})
require(remote.get("configured") is True, "temporary Git remote was not detected")
require(remote.get("name") == remote_name, "unexpected Git remote selected")
require(remote.get("reachable") is True, "git ls-remote did not succeed")
require(remote.get("host") == "github.com", "remote host metadata is unexpected")
require("url" not in remote, "remote URL leaked into diagnostic result")
github = payload.get("github_cli", {})
require(github.get("present") is True, "gh is not present on configured PATH")
require(github.get("authenticated") is True, "GitHub CLI authentication diagnostic did not pass")
identity = payload.get("identity", {})
require(isinstance(identity.get("name_present"), bool), "Git identity name diagnostic missing")
require(isinstance(identity.get("email_present"), bool), "Git identity email diagnostic missing")
PY

cleanup
trap - EXIT
assert_clean_git

printf 'P9_HOST_QUALIFICATION=PASS\n'
