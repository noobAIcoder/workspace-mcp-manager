#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="${REPOSITORY:-https://github.com/noobAIcoder/workspace-mcp-manager.git}"
RELEASE_REF="${RELEASE_REF:-refs/heads/main}"
EXPECTED_PRODUCT_VERSION="${EXPECTED_PRODUCT_VERSION:-0.2.0}"

fail() {
  printf 'DISTRIBUTION_FAIL=%s\n' "$*" >&2
  exit 1
}

for command in bash git curl python3 sha256sum readlink tar mktemp; do
  command -v "$command" >/dev/null 2>&1 || fail "required qualification command is unavailable: $command"
done

TMP_ROOT="$(mktemp -d -t workspace-mcp-distribution-XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT
ISO_HOME="$TMP_ROOT/home"
BOOTSTRAP="$TMP_ROOT/bootstrap.sh"
mkdir -p "$TMP_ROOT/git-home" "$TMP_ROOT/git-xdg"

git_isolated() {
  env \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
    HOME="$TMP_ROOT/git-home" \
    XDG_CONFIG_HOME="$TMP_ROOT/git-xdg" \
    GIT_TERMINAL_PROMPT=0 \
    git -c credential.helper= -c core.askPass= "$@"
}

REMOTE_SHA="$(git_isolated ls-remote "$REPOSITORY" "$RELEASE_REF" | cut -f1)"
[[ "$REMOTE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "cannot resolve qualification ref to an exact commit"

curl -fsSL -o "$BOOTSTRAP" \
  "https://raw.githubusercontent.com/noobAIcoder/workspace-mcp-manager/$REMOTE_SHA/bootstrap.sh" \
  || fail "cannot download exact qualification bootstrap"
bash -n "$BOOTSTRAP" || fail "bootstrap syntax validation failed"

bash "$BOOTSTRAP" \
  --qualification \
  --repository "$REPOSITORY" \
  --ref "$RELEASE_REF" \
  --account-home "$ISO_HOME" \
  >"$TMP_ROOT/install.out" 2>"$TMP_ROOT/install.err" \
  || { cat "$TMP_ROOT/install.err" >&2; fail "isolated clean installation failed"; }

python3 - "$TMP_ROOT/install.out" "$REMOTE_SHA" "$EXPECTED_PRODUCT_VERSION" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True and p["action"] == "install", p
assert p["release_commit"] == sys.argv[2], p
assert p["distribution_id"] == f"dist-{sys.argv[2]}", p
assert p["product_version"] == sys.argv[3], p
PY

PREFIX="$ISO_HOME/.local"
CURRENT="$PREFIX/lib/workspace-mcp-manager/current"
EXPECTED_TARGET="releases/dist-$REMOTE_SHA"
[ -L "$CURRENT" ] || fail "current is not a symlink"
[ "$(readlink "$CURRENT")" = "$EXPECTED_TARGET" ] || fail "current does not select exact qualification SHA"
RELEASE="$(readlink -f "$CURRENT")"

python3 - "$ISO_HOME/.local/state/workspace-mcp-manager/distribution.json" "$RELEASE/install.json" "$REMOTE_SHA" "$EXPECTED_PRODUCT_VERSION" <<'PY'
import hashlib, json, pathlib, sys
receipt=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
install_path=pathlib.Path(sys.argv[2])
install=json.loads(install_path.read_text(encoding="utf-8"))
commit=sys.argv[3]; version=sys.argv[4]
assert receipt["release_commit"] == install["release_commit"] == commit
assert receipt["distribution_id"] == install["distribution_id"] == f"dist-{commit}"
assert receipt["product_version"] == install["product_version"] == version
assert receipt["install_json_sha256"] == hashlib.sha256(install_path.read_bytes()).hexdigest()
PY

[ "$($PREFIX/bin/workspace-mcp-manager --version)" = "workspace-mcp-manager $EXPECTED_PRODUCT_VERSION" ] \
  || fail "manager version mismatch"
$PREFIX/bin/workspace-mcp-manager-tui --help >/dev/null || fail "TUI frontend cannot execute"
[ -x "$PREFIX/bin/workspace-mcp-reboot" ] || fail "reboot bridge is unavailable"
[ -x "$PREFIX/bin/workspace-mcp-manager-upgrade" ] || fail "upgrade frontend is unavailable"

for name in workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot workspace-mcp-manager-upgrade; do
  [ "$(readlink "$PREFIX/bin/$name")" = "../lib/workspace-mcp-manager/current/bin/$name" ] \
    || fail "stable entrypoint target mismatch: $name"
done

[ "$($RELEASE/runtimes/python/bin/python3 --version)" = "Python 3.12.13" ] || fail "distribution Python mismatch"
[ "$($RELEASE/tools/uv/uv --version)" = "uv 0.12.3 (x86_64-unknown-linux-gnu)" ] || fail "distribution uv mismatch"
[ "$($RELEASE/runtimes/tunnel-client/tunnel-client --version)" = \
  "0.0.11+8d55683eeef80bc5e360d95abf4692454fafc615 (git sha: 8d55683eeef80bc5e360d95abf4692454fafc615)" ] \
  || fail "distribution tunnel-client mismatch"
CODING_VERSION="$(CODING_SITE="$RELEASE/runtimes/coding-tools-mcp/site-packages" \
  "$RELEASE/runtimes/python/bin/python3" -B -I -S -c \
  'import importlib.metadata,os,sys; sys.path.insert(0,os.environ["CODING_SITE"]); print(importlib.metadata.version("coding-tools-mcp"))')"
[ "$CODING_VERSION" = "0.3.0" ] || fail "distribution coding-tools-mcp mismatch"

tree_digest() {
  tar -C "$ISO_HOME" --sort=name --format=gnu -cf - \
    .local/bin .local/lib/workspace-mcp-manager .local/state/workspace-mcp-manager \
    | sha256sum | cut -d' ' -f1
}
BEFORE="$(tree_digest)"
bash "$RELEASE/distribution/install.sh" --check --account-home "$ISO_HOME" >"$TMP_ROOT/check.json" \
  || fail "live local --check failed"
AFTER="$(tree_digest)"
[ "$BEFORE" = "$AFTER" ] || fail "live --check changed managed state"
python3 - "$TMP_ROOT/check.json" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True and p["ownership"] == "standalone_distribution", p
assert p["recovery_required"] is False and p["receipt_consistent"] is True, p
PY

bash "$BOOTSTRAP" \
  --qualification --repository "$REPOSITORY" --ref "$RELEASE_REF" --account-home "$ISO_HOME" \
  >"$TMP_ROOT/reinstall.out" 2>"$TMP_ROOT/reinstall.err" \
  || fail "same-release bootstrap rerun failed"
python3 - "$TMP_ROOT/reinstall.out" "$REMOTE_SHA" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True and p["action"] == "noop", p
assert p["release_commit"] == sys.argv[2], p
PY

$PREFIX/bin/workspace-mcp-manager-upgrade \
  --check --qualification --repository "$REPOSITORY" --ref "$RELEASE_REF" --account-home "$ISO_HOME" \
  >"$TMP_ROOT/upgrade-check.json" || fail "remote upgrade check failed"
python3 - "$TMP_ROOT/upgrade-check.json" "$REMOTE_SHA" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True and p["update_available"] is False, p
assert p["current_commit"] == p["remote_commit"] == sys.argv[2], p
PY

$PREFIX/bin/workspace-mcp-manager-upgrade \
  --qualification --repository "$REPOSITORY" --ref "$RELEASE_REF" --account-home "$ISO_HOME" \
  >"$TMP_ROOT/upgrade-noop.out" 2>"$TMP_ROOT/upgrade-noop.err" \
  || fail "unchanged-release installed upgrade failed"
python3 - "$TMP_ROOT/upgrade-noop.out" "$REMOTE_SHA" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["ok"] is True and p["action"] == "noop", p
assert p["release_commit"] == sys.argv[2], p
PY

[ ! -e "$ISO_HOME/.local/state/workspace-mcp-manager/distribution-transaction.json" ] \
  || fail "qualification left an unresolved transaction"

. /etc/os-release
printf 'DISTRIBUTION_QUALIFIED_SHA=%s\n' "$REMOTE_SHA"
printf 'DISTRIBUTION_QUALIFIED_HOST=%s %s %s %s\n' "$ID" "$VERSION_ID" "$(uname -m)" "$(uname -r)"
printf 'DISTRIBUTION_CLEAN_INSTALL=PASS\n'
printf 'DISTRIBUTION_REINSTALL_NOOP=PASS\n'
printf 'DISTRIBUTION_CHECK_READ_ONLY=PASS\n'
printf 'DISTRIBUTION_DEPENDENCY_INTEGRITY=PASS\n'
printf 'DISTRIBUTION_RECEIPT=PASS\n'
printf 'DISTRIBUTION_ACQUISITION_PROVENANCE=PASS\n'
printf 'DISTRIBUTION_LOCAL_UPGRADE_NOOP=PASS\n'
printf 'DISTRIBUTION_WSL_QUALIFICATION=PASS\n'
