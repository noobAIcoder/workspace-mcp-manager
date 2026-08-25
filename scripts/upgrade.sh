#!/usr/bin/env bash
set -euo pipefail

PRODUCTION_REPOSITORY="https://github.com/noobAIcoder/workspace-mcp-manager.git"
PRODUCTION_REF="refs/heads/release"

die() {
  printf 'workspace-mcp-manager-upgrade: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage: workspace-mcp-manager-upgrade [options]

Options:
  --check                Check remote production release availability only
  --configure            Launch installed TUI after a successful upgrade/NOOP
  --account-home PATH    Target account home (default: $HOME)
  --prefix PATH          Install prefix (default: <account-home>/.local)

Qualification/development only:
  --qualification
  --repository URL
  --ref REF
  --qualification-artifact-dir PATH
  --qualification-failpoint NAME
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required upgrade command is unavailable: $1"
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
LOCAL_INSTALLER="$SCRIPT_DIR/install.sh"
[[ -f "$LOCAL_INSTALLER" && ! -L "$LOCAL_INSTALLER" ]] || die "immutable local recovery installer is unavailable"

REPOSITORY="$PRODUCTION_REPOSITORY"
RELEASE_REF="$PRODUCTION_REF"
ACCOUNT_HOME=${HOME:-}
PREFIX=""
CHECK=false
CONFIGURE=false
QUALIFICATION=false
QUALIFICATION_ARTIFACT_DIR=""
QUALIFICATION_FAILPOINT=""

while (($#)); do
  case "$1" in
    --check) CHECK=true; shift ;;
    --configure) CONFIGURE=true; shift ;;
    --account-home) (($#>=2)) || die "--account-home requires a path"; ACCOUNT_HOME=$2; shift 2 ;;
    --prefix) (($#>=2)) || die "--prefix requires a path"; PREFIX=$2; shift 2 ;;
    --qualification) QUALIFICATION=true; shift ;;
    --repository) (($#>=2)) || die "--repository requires a URL"; REPOSITORY=$2; shift 2 ;;
    --ref) (($#>=2)) || die "--ref requires a ref"; RELEASE_REF=$2; shift 2 ;;
    --qualification-artifact-dir) (($#>=2)) || die "--qualification-artifact-dir requires a path"; QUALIFICATION_ARTIFACT_DIR=$2; shift 2 ;;
    --qualification-failpoint) (($#>=2)) || die "--qualification-failpoint requires a name"; QUALIFICATION_FAILPOINT=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n "$ACCOUNT_HOME" ]] || die "account home is unavailable"
if ! $QUALIFICATION; then
  [[ "$REPOSITORY" == "$PRODUCTION_REPOSITORY" ]] || die "production repository is fixed"
  [[ "$RELEASE_REF" == "$PRODUCTION_REF" ]] || die "production ref is fixed"
  [[ -z "$QUALIFICATION_ARTIFACT_DIR" && -z "$QUALIFICATION_FAILPOINT" ]] || die "qualification options require --qualification"
fi

local_args=(--account-home "$ACCOUNT_HOME")
[[ -z "$PREFIX" ]] || local_args+=(--prefix "$PREFIX")

if $CHECK; then
  # Local installer check remains read-only. A pending transaction is reported,
  # not silently recovered by an availability query.
  bash "$LOCAL_INSTALLER" --check "${local_args[@]}" >/dev/null \
    || die "local distribution check failed; recover before checking remote availability"
else
  # Recovery is deliberately the only old-release mutation before acquisition.
  bash "$LOCAL_INSTALLER" --recover-only "${local_args[@]}" >/dev/null \
    || die "local distribution recovery failed"
fi

for command in bash git mktemp rm mkdir python3 realpath; do require_command "$command"; done
TMP_ROOT=$(mktemp -d)
cleanup() { rm -rf -- "$TMP_ROOT"; }
trap cleanup EXIT INT TERM
CHECKOUT="$TMP_ROOT/checkout"
GIT_HOME="$TMP_ROOT/git-home"
GIT_XDG="$TMP_ROOT/git-xdg"
mkdir -p -- "$CHECKOUT" "$GIT_HOME" "$GIT_XDG"

git_isolated() {
  env \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
    HOME="$GIT_HOME" \
    XDG_CONFIG_HOME="$GIT_XDG" \
    GIT_TERMINAL_PROMPT=0 \
    git -c credential.helper= -c core.askPass= "$@"
}

git_isolated -C "$CHECKOUT" init -q
git_isolated -C "$CHECKOUT" remote add origin "$REPOSITORY"
if $QUALIFICATION && [[ "$REPOSITORY" == file://* ]]; then
  git_isolated -c protocol.file.allow=always -C "$CHECKOUT" fetch -q --no-tags --depth=1 origin \
    "$RELEASE_REF:refs/remotes/origin/workspace-mcp-manager-distribution"
else
  git_isolated -c protocol.file.allow=never -C "$CHECKOUT" fetch -q --no-tags --depth=1 origin \
    "$RELEASE_REF:refs/remotes/origin/workspace-mcp-manager-distribution"
fi
COMMIT=$(git_isolated -C "$CHECKOUT" rev-parse 'refs/remotes/origin/workspace-mcp-manager-distribution^{commit}') \
  || die "cannot resolve fetched release ref"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "fetched release is not an exact commit"
git_isolated -C "$CHECKOUT" checkout -q --detach "$COMMIT"
[[ "$(git_isolated -C "$CHECKOUT" rev-parse HEAD)" == "$COMMIT" ]] || die "detached checkout verification failed"

if $CHECK; then
  prefix_effective="$PREFIX"
  [[ -n "$prefix_effective" ]] || prefix_effective="$ACCOUNT_HOME/.local"
  current="$prefix_effective/lib/workspace-mcp-manager/current"
  current_commit=""
  if [[ -L "$current" ]]; then
    target=$(readlink -- "$current")
    if [[ "$target" =~ ^releases/dist-([0-9a-f]{40})$ ]]; then current_commit="${BASH_REMATCH[1]}"; fi
  fi
  python3 - "$current_commit" "$COMMIT" "$REPOSITORY" "$RELEASE_REF" <<'PY'
import json,sys
current,remote,repository,ref=sys.argv[1:5]
print(json.dumps({
 "ok":True,
 "mode":"upgrade-check",
 "current_commit":current or None,
 "remote_commit":remote,
 "update_available":current != remote,
 "repository":repository,
 "release_ref":ref,
},sort_keys=True,separators=(",",":")))
PY
  exit 0
fi

args=(
  --upgrade
  --repo "$CHECKOUT"
  --account-home "$ACCOUNT_HOME"
  --repository "$REPOSITORY"
  --release-ref "$RELEASE_REF"
  --release-commit "$COMMIT"
)
[[ -z "$PREFIX" ]] || args+=(--prefix "$PREFIX")
$CONFIGURE && args+=(--configure)
if $QUALIFICATION; then
  args+=(--qualification)
  [[ -z "$QUALIFICATION_ARTIFACT_DIR" ]] || args+=(--qualification-artifact-dir "$QUALIFICATION_ARTIFACT_DIR")
  [[ -z "$QUALIFICATION_FAILPOINT" ]] || args+=(--qualification-failpoint "$QUALIFICATION_FAILPOINT")
fi

bash "$CHECKOUT/scripts/install.sh" "${args[@]}"
