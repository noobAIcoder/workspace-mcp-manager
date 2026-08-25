#!/usr/bin/env bash
set -euo pipefail

PRODUCTION_REPOSITORY="https://github.com/noobAIcoder/workspace-mcp-manager.git"
PRODUCTION_REF="refs/heads/release"

die() {
  printf 'workspace-mcp-manager bootstrap: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage: bootstrap.sh [options]

Options:
  --configure            Launch installed TUI after installation
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
  command -v "$1" >/dev/null 2>&1 || die "required bootstrap command is unavailable: $1"
}

REPOSITORY="$PRODUCTION_REPOSITORY"
RELEASE_REF="$PRODUCTION_REF"
ACCOUNT_HOME=${HOME:-}
PREFIX=""
CONFIGURE=false
QUALIFICATION=false
QUALIFICATION_ARTIFACT_DIR=""
QUALIFICATION_FAILPOINT=""

while (($#)); do
  case "$1" in
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

for command in bash git mktemp rm mkdir; do require_command "$command"; done

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
  || die "cannot resolve fetched ref to a commit"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "fetched object is not an exact Git commit"
git_isolated -C "$CHECKOUT" checkout -q --detach "$COMMIT"
HEAD=$(git_isolated -C "$CHECKOUT" rev-parse HEAD) || die "cannot resolve detached checkout HEAD"
[[ "$HEAD" == "$COMMIT" ]] || die "detached checkout HEAD disagrees with fetched commit"
[[ "$(git_isolated -C "$CHECKOUT" config --get remote.origin.url)" == "$REPOSITORY" ]] \
  || die "temporary checkout repository provenance changed"

args=(
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
