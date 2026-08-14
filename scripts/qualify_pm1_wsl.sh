#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
REPO="${REPO:-$HOME/repos/workspace-mcp-manager}"
WORKSPACE="${WORKSPACE:-$HOME/repos/workspace-mcp-manager-qual}"
REGISTRY="${REGISTRY:-$HOME/.config/workspace-mcp-manager/instances/$INSTANCE_ID.json}"
MCP_PORT="${MCP_PORT:-7657}"
HEALTH_PORT="${HEALTH_PORT:-7373}"

GH_BIN="${GH_BIN:-$(command -v gh || true)}"
GH_ROOT="$HOME/.config/workspace-mcp-manager/github"
GH_PROFILE="$GH_ROOT/$INSTANCE_ID"
HELPER="$HOME/.local/bin/$INSTANCE_ID-git-credential-github"
AGENT_UNIT="workspace-mcp-ssh-agent-$INSTANCE_ID.service"
MCP_UNIT="coding-tools-mcp-$INSTANCE_ID.service"
AGENT_SOCK="/run/user/$(id -u)/workspace-mcp-manager-$INSTANCE_ID/ssh-agent.sock"
QUAL_REMOTE="pm1-qual"

fail() {
  printf 'PM1_FAIL=%s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable"
[ -d "$REPO/.git" ] || fail "manager source repository is unavailable"
[ -d "$WORKSPACE/.git" ] || fail "manager-qual workspace is not a Git repository"
[ -f "$REGISTRY" ] || fail "manager-qual declaration is unavailable"
[ -n "$GH_BIN" ] && [ -x "$GH_BIN" ] || fail "gh executable is unavailable"

TMP_ROOT="$(mktemp -d -t workspace-mcp-pm1-XXXXXX)"
ORIGINAL_DECL="$TMP_ROOT/original-declaration.json"
ORIGINAL_GIT_CONFIG="$TMP_ROOT/original-git-config"
V2_SSH="$TMP_ROOT/v2-ssh.json"
V2_HTTPS="$TMP_ROOT/v2-https.json"
V2_NEUTRAL="$TMP_ROOT/v2-neutral.json"

cp "$REGISTRY" "$ORIGINAL_DECL"
cp "$WORKSPACE/.git/config" "$ORIGINAL_GIT_CONFIG"

PROFILE_EXISTED=0
ROOT_EXISTED=0
[ -e "$GH_PROFILE" ] && PROFILE_EXISTED=1
[ -e "$GH_ROOT" ] && ROOT_EXISTED=1
RESTORE_REQUIRED=1

cleanup_profile() {
  if [ "$PROFILE_EXISTED" -eq 0 ] && [ -d "$GH_PROFILE" ]; then
    if [ "$(find "$GH_PROFILE" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)" = ".owner" ] \
      && grep -Fx "workspace-mcp-manager-instance=$INSTANCE_ID" "$GH_PROFILE/.owner" >/dev/null 2>&1; then
      rm -f "$GH_PROFILE/.owner"
      rmdir "$GH_PROFILE" 2>/dev/null || true
    fi
  fi
  if [ "$ROOT_EXISTED" -eq 0 ] && [ -d "$GH_ROOT" ]; then
    rmdir "$GH_ROOT" 2>/dev/null || true
  fi
}

restore_baseline() {
  set +e
  cp "$ORIGINAL_GIT_CONFIG" "$WORKSPACE/.git/config"
  cp "$ORIGINAL_DECL" "$REGISTRY"
  chmod 600 "$REGISTRY" 2>/dev/null || true
  "$MANAGER" instance apply "$INSTANCE_ID" >/dev/null 2>&1
  cleanup_profile
  set -e
}

cleanup() {
  if [ "$RESTORE_REQUIRED" -eq 1 ]; then
    restore_baseline
  fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

GLOBAL_GIT_HASH_BEFORE="$( (git config --global --null --list 2>/dev/null || true) | sha256sum | cut -d' ' -f1)"
WORKTREE_BEFORE="$(git -C "$WORKSPACE" status --porcelain=v1 --untracked-files=all | sha256sum | cut -d' ' -f1)"

git -C "$WORKSPACE" config --get "remote.$QUAL_REMOTE.url" >/dev/null 2>&1 \
  && fail "qualification remote already exists"

SOURCE_REMOTE=""
if git -C "$REPO" config --get remote.origin.url >/dev/null 2>&1; then
  SOURCE_REMOTE="origin"
else
  SOURCE_REMOTE="$(git -C "$REPO" remote | LC_ALL=C sort | head -n 1)"
fi
[ -n "$SOURCE_REMOTE" ] || fail "source repository has no remote"
SOURCE_URL="$(git -C "$REPO" remote get-url "$SOURCE_REMOTE")"
REPO_PATH="$(python3 - "$SOURCE_URL" <<'PY'
import sys
from urllib.parse import urlparse

value=sys.argv[1].strip()
path=None
if value.startswith("git@github.com:"):
    path=value[len("git@github.com:"):]
elif "://" in value:
    parsed=urlparse(value)
    if parsed.hostname == "github.com" and parsed.password is None:
        if parsed.scheme == "https" and parsed.username is None:
            path=parsed.path.lstrip("/")
        elif parsed.scheme == "ssh" and parsed.username in {None, "git"}:
            path=parsed.path.lstrip("/")
if path and path.endswith(".git"):
    path=path[:-4]
parts=path.split("/") if path else []
if len(parts) != 2 or not all(parts):
    raise SystemExit(2)
print("/".join(parts))
PY
)" || fail "source remote is not a supported credential-free GitHub URL"
QUAL_SSH_URL="git@github.com:$REPO_PATH.git"

LOCAL_NAME="$(git -C "$WORKSPACE" config --local --get user.name 2>/dev/null || true)"
LOCAL_EMAIL="$(git -C "$WORKSPACE" config --local --get user.email 2>/dev/null || true)"
ORIGINAL_LOCAL_NAME="$LOCAL_NAME"
ORIGINAL_LOCAL_EMAIL="$LOCAL_EMAIL"
if { [ -n "$LOCAL_NAME" ] && [ -z "$LOCAL_EMAIL" ]; } || { [ -z "$LOCAL_NAME" ] && [ -n "$LOCAL_EMAIL" ]; }; then
  fail "repository-local Git identity is incomplete"
fi
[ -n "$LOCAL_NAME" ] || LOCAL_NAME="PM1 Qualification"
[ -n "$LOCAL_EMAIL" ] || LOCAL_EMAIL="pm1-qualification@example.invalid"

git -C "$WORKSPACE" remote add "$QUAL_REMOTE" "$QUAL_SSH_URL"

python3 - "$ORIGINAL_DECL" "$V2_SSH" "$GH_PROFILE" "$GH_BIN" "$LOCAL_NAME" "$LOCAL_EMAIL" "$QUAL_REMOTE" <<'PY'
import json, pathlib, sys
source, target, profile, gh, name, email, remote = sys.argv[1:]
payload=json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
payload["config_version"]=2
payload["github"]={"mode":"managed","config_dir":profile,"binary":gh}
payload["git"]={"identity":{"name":name,"email":email},"remote":{"name":remote,"protocol":"ssh"}}
payload["agent"]={"mode":"managed-ssh-agent","ssh_auth_sock":None}
pathlib.Path(target).write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n", encoding="utf-8")
PY

python3 - "$V2_SSH" "$V2_HTTPS" <<'PY'
import json, pathlib, sys
source, target = sys.argv[1:]
payload=json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
payload["git"]["remote"]["protocol"]="https-gh"
pathlib.Path(target).write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n", encoding="utf-8")
PY

python3 - "$V2_SSH" "$V2_NEUTRAL" <<'PY'
import json, pathlib, sys
source, target = sys.argv[1:]
payload=json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
payload["git"]={"identity":None,"remote":None}
payload["agent"]={"mode":"none","ssh_auth_sock":None}
pathlib.Path(target).write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n", encoding="utf-8")
PY

"$MANAGER" instance validate "$V2_SSH" >/dev/null
"$MANAGER" instance validate "$V2_HTTPS" >/dev/null
"$MANAGER" instance validate "$V2_NEUTRAL" >/dev/null

assert_noop_plan() {
  local path="$1"
  python3 - "$path" <<'PY'
import json, pathlib, sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["valid"] is True, payload
assert payload["operations"], payload
assert {item["operation"] for item in payload["operations"]} == {"NOOP"}, payload
PY
}

check_endpoints() {
  curl -fsS "http://127.0.0.1:$MCP_PORT/.well-known/mcp.json" >/dev/null \
    || fail "MCP discovery is unhealthy"
  [ "$(curl -fsS "http://127.0.0.1:$HEALTH_PORT/healthz")" = live ] \
    || fail "tunnel health is not live"
  [ "$(curl -fsS "http://127.0.0.1:$HEALTH_PORT/readyz")" = ready ] \
    || fail "tunnel readiness is not ready"
}

"$MANAGER" instance update "$V2_SSH" >/dev/null
"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/ssh-plan-before.json"
python3 - "$TMP_ROOT/ssh-plan-before.json" <<'PY'
import json, pathlib, sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["valid"] is True, payload
ids={item["resource_id"] for item in payload["operations"]}
assert "dev-git-identity" in ids, payload
assert "dev-git-transport" in ids, payload
assert "ssh-agent-unit" in ids, payload
assert "github-profile-dir" in ids, payload
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

[ "$(systemctl --user is-active "$AGENT_UNIT")" = active ] || fail "managed SSH agent is not active"
[ "$(systemctl --user is-enabled "$AGENT_UNIT")" = enabled ] || fail "managed SSH agent is not enabled"
[ -S "$AGENT_SOCK" ] || fail "managed SSH agent socket is absent"
[ "$(stat -c '%a' "$HOME/.config/systemd/user/$AGENT_UNIT")" = 600 ] || fail "managed SSH agent unit mode is not 0600"
[ -d "$GH_PROFILE" ] || fail "managed GitHub profile directory is absent"
[ "$(stat -c '%a' "$GH_PROFILE")" = 700 ] || fail "managed GitHub profile mode is not 0700"
grep -Fx "workspace-mcp-manager-instance=$INSTANCE_ID" "$GH_PROFILE/.owner" >/dev/null \
  || fail "managed GitHub profile ownership marker is invalid"
grep -F "Requires=$AGENT_UNIT" "$HOME/.config/systemd/user/$MCP_UNIT" >/dev/null \
  || fail "MCP unit does not require managed SSH agent"
grep -F "SSH_AUTH_SOCK=$AGENT_SOCK" "$HOME/.config/systemd/user/$MCP_UNIT" >/dev/null \
  || fail "MCP unit does not expose managed SSH agent socket"

MCP_PID="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
tr '\0' '\n' <"/proc/$MCP_PID/environ" | grep -Fx "SSH_AUTH_SOCK=$AGENT_SOCK" >/dev/null \
  || fail "live MCP process does not use managed SSH agent socket"

[ "$(git -C "$WORKSPACE" config --local --get workspaceMcpManager.identityOwner)" = "$INSTANCE_ID" ] \
  || fail "Git identity ownership marker is invalid"
[ "$(git -C "$WORKSPACE" config --local --get workspaceMcpManager.remoteOwner)" = "$INSTANCE_ID" ] \
  || fail "Git transport ownership marker is invalid"
[ "$(git -C "$WORKSPACE" config --local --get "remote.$QUAL_REMOTE.url")" = "$QUAL_SSH_URL" ] \
  || fail "qualification remote is not using canonical SSH URL"
SSH_COMMAND="$(git -C "$WORKSPACE" config --local --get core.sshCommand)"
[[ "$SSH_COMMAND" == *"IdentityAgent=$AGENT_SOCK"* ]] \
  || fail "repository SSH command is not bound to managed agent"
! git -C "$WORKSPACE" config --local --get credential.https://github.com.helper >/dev/null 2>&1 \
  || fail "HTTPS helper is unexpectedly configured during SSH phase"

"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/ssh-plan-after.json"
assert_noop_plan "$TMP_ROOT/ssh-plan-after.json"
check_endpoints

"$MANAGER" instance update "$V2_HTTPS" >/dev/null
"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/https-plan-before.json"
python3 - "$TMP_ROOT/https-plan-before.json" <<'PY'
import json, pathlib, sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["valid"] is True, payload
assert any(item["resource_id"] == "github-cli-helper" for item in payload["operations"]), payload
assert any(item["resource_id"] == "dev-git-transport" and item["operation"] == "UPDATE" for item in payload["operations"]), payload
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

[ -f "$HELPER" ] && [ ! -L "$HELPER" ] || fail "GitHub CLI helper is absent or not regular"
[ "$(stat -c '%a' "$HELPER")" = 755 ] || fail "GitHub CLI helper mode is not 0755"
grep -F "# workspace-mcp-manager-instance=$INSTANCE_ID" "$HELPER" >/dev/null \
  || fail "GitHub CLI helper ownership marker is invalid"
grep -F "GH_CONFIG_DIR=" "$HELPER" >/dev/null || fail "GitHub CLI helper does not pin profile"
grep -F "auth git-credential" "$HELPER" >/dev/null || fail "GitHub CLI helper dispatch is invalid"
"$HELPER" --help >/dev/null

[ "$(git -C "$WORKSPACE" config --local --get "remote.$QUAL_REMOTE.url")" = "https://github.com/$REPO_PATH.git" ] \
  || fail "qualification remote is not using canonical HTTPS URL"
[ "$(git -C "$WORKSPACE" config --local --get credential.https://github.com.helper)" = "$HELPER" ] \
  || fail "repository-local GitHub helper path is invalid"
! git -C "$WORKSPACE" config --local --get core.sshCommand >/dev/null 2>&1 \
  || fail "SSH command remains configured during HTTPS phase"

"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/https-plan-after.json"
assert_noop_plan "$TMP_ROOT/https-plan-after.json"
check_endpoints

"$MANAGER" instance update "$V2_SSH" >/dev/null
"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/ssh-return-plan.json"
python3 - "$TMP_ROOT/ssh-return-plan.json" <<'PY'
import json, pathlib, sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["valid"] is True, payload
assert any(item["resource_id"] == "github-cli-helper" and item["operation"] == "REMOVE" for item in payload["operations"]), payload
assert any(item["resource_id"] == "dev-git-transport" and item["operation"] == "UPDATE" for item in payload["operations"]), payload
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

[ ! -e "$HELPER" ] || fail "obsolete GitHub CLI helper was not removed"
[ "$(git -C "$WORKSPACE" config --local --get "remote.$QUAL_REMOTE.url")" = "$QUAL_SSH_URL" ] \
  || fail "qualification remote did not return to SSH"
! git -C "$WORKSPACE" config --local --get credential.https://github.com.helper >/dev/null 2>&1 \
  || fail "repository-local GitHub helper key remains after SSH return"
"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/ssh-return-after.json"
assert_noop_plan "$TMP_ROOT/ssh-return-after.json"

"$MANAGER" instance update "$V2_NEUTRAL" >/dev/null
"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/neutral-plan-before.json"
python3 - "$TMP_ROOT/neutral-plan-before.json" <<'PY'
import json, pathlib, sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["valid"] is True, payload
assert any(item["resource_id"] == "dev-git-identity" and item["operation"] == "REMOVE" for item in payload["operations"]), payload
assert any(item["resource_id"] == "dev-git-transport" and item["operation"] == "REMOVE" for item in payload["operations"]), payload
agent=[item["operation"] for item in payload["operations"] if item["resource_id"] == "ssh-agent-unit"]
assert agent == ["STOP", "DISABLE", "REMOVE"], payload
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

systemctl --user is-active "$AGENT_UNIT" >/dev/null 2>&1 \
  && fail "obsolete managed SSH agent remains active"
systemctl --user is-enabled "$AGENT_UNIT" >/dev/null 2>&1 \
  && fail "obsolete managed SSH agent remains enabled"
[ ! -e "$HOME/.config/systemd/user/$AGENT_UNIT" ] || fail "obsolete managed SSH agent unit remains"
[ ! -S "$AGENT_SOCK" ] || fail "obsolete managed SSH agent socket remains"
[ ! -e "$HELPER" ] || fail "GitHub CLI helper remains after neutralization"

! git -C "$WORKSPACE" config --local --get workspaceMcpManager.identityOwner >/dev/null 2>&1 \
  || fail "Git identity ownership metadata remains"
! git -C "$WORKSPACE" config --local --get workspaceMcpManager.remoteOwner >/dev/null 2>&1 \
  || fail "Git transport ownership metadata remains"
! git -C "$WORKSPACE" config --local --get core.sshCommand >/dev/null 2>&1 \
  || fail "repository SSH command remains after neutralization"
[ "$(git -C "$WORKSPACE" config --local --get "remote.$QUAL_REMOTE.url")" = "$QUAL_SSH_URL" ] \
  || fail "qualification remote original URL was not restored"

if [ -n "$ORIGINAL_LOCAL_NAME" ]; then
  [ "$(git -C "$WORKSPACE" config --local --get user.name)" = "$ORIGINAL_LOCAL_NAME" ] \
    || fail "original Git user.name was not restored"
  [ "$(git -C "$WORKSPACE" config --local --get user.email)" = "$ORIGINAL_LOCAL_EMAIL" ] \
    || fail "original Git user.email was not restored"
else
  ! git -C "$WORKSPACE" config --local --get user.name >/dev/null 2>&1 \
    || fail "qualification-created Git user.name remains"
  ! git -C "$WORKSPACE" config --local --get user.email >/dev/null 2>&1 \
    || fail "qualification-created Git user.email remains"
fi

"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/neutral-plan-after.json"
assert_noop_plan "$TMP_ROOT/neutral-plan-after.json"
check_endpoints

cp "$ORIGINAL_GIT_CONFIG" "$WORKSPACE/.git/config"
cp "$ORIGINAL_DECL" "$REGISTRY"
chmod 600 "$REGISTRY"
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null
cleanup_profile
RESTORE_REQUIRED=0

cmp -s "$ORIGINAL_DECL" "$REGISTRY" || fail "original declaration was not restored exactly"
cmp -s "$ORIGINAL_GIT_CONFIG" "$WORKSPACE/.git/config" || fail "original repository Git config was not restored exactly"
git -C "$WORKSPACE" config --get "remote.$QUAL_REMOTE.url" >/dev/null 2>&1 \
  && fail "qualification remote remains after baseline restore"

if [ "$PROFILE_EXISTED" -eq 0 ]; then
  [ ! -e "$GH_PROFILE" ] || fail "qualification-created GitHub profile was not safely removed"
fi

GLOBAL_GIT_HASH_AFTER="$( (git config --global --null --list 2>/dev/null || true) | sha256sum | cut -d' ' -f1)"
WORKTREE_AFTER="$(git -C "$WORKSPACE" status --porcelain=v1 --untracked-files=all | sha256sum | cut -d' ' -f1)"
[ "$GLOBAL_GIT_HASH_AFTER" = "$GLOBAL_GIT_HASH_BEFORE" ] || fail "global Git configuration changed"
[ "$WORKTREE_AFTER" = "$WORKTREE_BEFORE" ] || fail "manager-qual working tree changed"

"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/final-plan.json"
assert_noop_plan "$TMP_ROOT/final-plan.json"
check_endpoints

if ! PYTHONPATH="$REPO:$REPO/src:$REPO/tests" python3 -m unittest discover -s "$REPO/tests" -v \
  >"$TMP_ROOT/pure-suite.out" 2>&1; then
  tail -n 80 "$TMP_ROOT/pure-suite.out" >&2
  fail "complete pure test suite failed"
fi

printf 'PM1_MANAGED_GITHUB_PROFILE=PASS\n'
printf 'PM1_GIT_IDENTITY=PASS\n'
printf 'PM1_SSH_AGENT=PASS\n'
printf 'PM1_HTTPS_HELPER=PASS\n'
printf 'PM1_REVERSIBLE_CLEANUP=PASS\n'
printf 'PM1_PURE_SUITE=PASS\n'
printf 'PM1_DEVELOPMENT_ENVIRONMENT_QUALIFICATION=PASS\n'
