#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-prepare}"
INSTANCE_ID="${P8_INSTANCE_ID:-manager-qual}"
WORKSPACE="${P8_WORKSPACE:-/home/cloudtoor/repos/workspace-mcp-manager-qual}"
QUAL_ROOT="${P8_QUAL_ROOT:-/home/cloudtoor/.local/state/workspace-mcp-manager/p8-qualification}"
RO_SOURCE="$QUAL_ROOT/ro"
RW_SOURCE="$QUAL_ROOT/rw"
ACCESS_ROOT="$WORKSPACE/.workspace-mcp-access"
MCP_UNIT="/home/cloudtoor/.config/systemd/user/coding-tools-mcp-${INSTANCE_ID}.service"

fail() {
  printf 'P8_QUALIFICATION_ERROR=%s\n' "$*" >&2
  exit 1
}

assert_clean_git() {
  local dirty
  dirty="$(git -C "$WORKSPACE" status --porcelain --untracked-files=all)"
  [ -z "$dirty" ] || fail "qualification workspace is dirty: $dirty"
}

prepare() {
  assert_clean_git

  local baseline
  baseline="$(workspace-mcp-manager access list "$INSTANCE_ID")"
  printf '%s\n' "$baseline" | grep -F '"entries":[]' >/dev/null \
    || fail "manager-qual must begin P8 qualification with no external access entries"

  if [ -e "$QUAL_ROOT" ] || [ -L "$QUAL_ROOT" ]; then
    fail "qualification source root already exists; run cleanup or inspect it first: $QUAL_ROOT"
  fi
  install -d -m 0700 "$RO_SOURCE" "$RW_SOURCE"
  printf 'p8-read-only-source\n' > "$RO_SOURCE/readonly.txt"
  printf 'p8-read-write-source\n' > "$RW_SOURCE/readwrite.txt"
  chmod 0600 "$RO_SOURCE/readonly.txt" "$RW_SOURCE/readwrite.txt"

  workspace-mcp-manager access add-ro "$INSTANCE_ID" p8-ro "$RO_SOURCE" --pretty
  workspace-mcp-manager access add-rw "$INSTANCE_ID" p8-rw "$RW_SOURCE" --pretty
  workspace-mcp-manager access list "$INSTANCE_ID" --pretty

  local plan
  plan="$(workspace-mcp-manager instance plan "$INSTANCE_ID")"
  printf '%s\n' "$plan"
  printf '%s\n' "$plan" | grep -F '"valid":true' >/dev/null || fail "P8 prepare plan is invalid"
  printf '%s\n' "$plan" | grep -F '"resource_id":"access-root"' >/dev/null \
    || fail "P8 prepare plan does not create/reconcile the access root"
  printf '%s\n' "$plan" | grep -F '"resource_id":"mcp-unit"' >/dev/null \
    || fail "P8 prepare plan does not reconcile the MCP unit"

  workspace-mcp-manager instance apply "$INSTANCE_ID" --pretty

  [ -f "$MCP_UNIT" ] || fail "generated MCP unit is missing"
  grep -F 'PrivateUsers=true' "$MCP_UNIT" >/dev/null || fail "PrivateUsers=true is missing"
  grep -F 'BindReadOnlyPaths=' "$MCP_UNIT" | grep -F "$RO_SOURCE" >/dev/null \
    || fail "RO bind is missing from generated MCP unit"
  grep -F 'BindPaths=' "$MCP_UNIT" | grep -F "$RW_SOURCE" >/dev/null \
    || fail "RW bind is missing from generated MCP unit"
  [ -f "$ACCESS_ROOT/.owner" ] || fail "access ownership marker is missing"
  [ -f "$ACCESS_ROOT/.gitignore" ] || fail "access .gitignore is missing"
  [ -d "$ACCESS_ROOT/p8-ro" ] || fail "RO mountpoint directory is missing"
  [ -d "$ACCESS_ROOT/p8-rw" ] || fail "RW mountpoint directory is missing"
  assert_clean_git

  printf 'P8_RO_SOURCE=%s\n' "$RO_SOURCE"
  printf 'P8_RW_SOURCE=%s\n' "$RW_SOURCE"
  printf 'P8_HOST_PREPARED=PASS\n'
}

cleanup() {
  workspace-mcp-manager access remove "$INSTANCE_ID" p8-ro --pretty
  workspace-mcp-manager access remove "$INSTANCE_ID" p8-rw --pretty

  local plan
  plan="$(workspace-mcp-manager instance plan "$INSTANCE_ID")"
  printf '%s\n' "$plan"
  printf '%s\n' "$plan" | grep -F '"valid":true' >/dev/null || fail "P8 cleanup plan is invalid"
  printf '%s\n' "$plan" | grep -F '"operation":"REMOVE"' >/dev/null \
    || fail "P8 cleanup plan does not remove obsolete access resources"

  workspace-mcp-manager instance apply "$INSTANCE_ID" --pretty
  workspace-mcp-manager access list "$INSTANCE_ID" --pretty

  [ ! -e "$ACCESS_ROOT" ] && [ ! -L "$ACCESS_ROOT" ] \
    || fail "access root remains after final access removal"
  assert_clean_git

  if [ -d "$QUAL_ROOT" ] && [ ! -L "$QUAL_ROOT" ]; then
    rm -f "$RO_SOURCE/readonly.txt" "$RW_SOURCE/readwrite.txt"
    rmdir "$RO_SOURCE" "$RW_SOURCE" "$QUAL_ROOT"
  elif [ -e "$QUAL_ROOT" ] || [ -L "$QUAL_ROOT" ]; then
    fail "refusing to clean unexpected qualification source-root type"
  fi

  printf 'P8_HOST_CLEANUP=PASS\n'
}

case "$MODE" in
  prepare) prepare ;;
  cleanup) cleanup ;;
  *) fail "usage: $0 [prepare|cleanup]" ;;
esac
