#!/usr/bin/env bash
set -euo pipefail

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11
TEXTUAL_VERSION="8.2.8"

die() {
  printf 'workspace-mcp-manager installer: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage: scripts/install_toolkit.sh [options]

Options:
  --repo PATH          Repository checkout (default: parent of scripts/)
  --account-home PATH  Target account home (default: $HOME)
  --prefix PATH        Install prefix (default: <account-home>/.local)
  --upgrade            Permit replacement of a different installed release
  --check              Validate candidate/installed state without mutation
  -h, --help           Show this help
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

absolute_path() {
  realpath -m -- "$1"
}

json_escape() {
  local value=${1-}
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '%s' "$value"
}

atomic_symlink() {
  local path=$1
  local target=$2
  local directory temporary
  directory=$(dirname -- "$path")
  mkdir -p -- "$directory"
  temporary="$directory/.${path##*/}.$$.$RANDOM.tmp"
  rm -f -- "$temporary"
  ln -s -- "$target" "$temporary"
  mv -Tf -- "$temporary" "$path"
}

atomic_file_from_stdin() {
  local path=$1
  local mode=$2
  local directory temporary
  directory=$(dirname -- "$path")
  mkdir -p -- "$directory"
  temporary=$(mktemp "$directory/.${path##*/}.XXXXXX.tmp")
  cat >"$temporary"
  chmod "$mode" "$temporary"
  mv -Tf -- "$temporary" "$path"
}

candidate_files() {
  printf '%s\n' \
    'pyproject.toml' \
    'requirements-tui.lock' \
    'scripts/install_toolkit.sh'
  (
    cd "$REPO"
    LC_ALL=C find src/workspace_mcp_manager -type f -name '*.py' -print | LC_ALL=C sort
  )
}

validate_candidate_layout() {
  [[ -d "$REPO/src/workspace_mcp_manager" ]] || die "candidate source package is missing"
  [[ -f "$REPO/pyproject.toml" && ! -L "$REPO/pyproject.toml" ]] || die "candidate pyproject.toml is missing/not regular"
  [[ -f "$REPO/requirements-tui.lock" && ! -L "$REPO/requirements-tui.lock" ]] || die "candidate TUI dependency lock is missing/not regular"
  [[ -f "$REPO/scripts/install_toolkit.sh" && ! -L "$REPO/scripts/install_toolkit.sh" ]] || die "candidate shell installer is missing/not regular"
  if find "$REPO/src/workspace_mcp_manager" -type l -print -quit | grep -q .; then
    die "candidate source package contains symlinks"
  fi
  local relative
  while IFS= read -r relative; do
    [[ -f "$REPO/$relative" && ! -L "$REPO/$relative" ]] || die "candidate source file is missing/not regular: $relative"
  done < <(candidate_files)
}

source_fingerprint() {
  local relative size
  {
    while IFS= read -r relative; do
      size=$(stat -c '%s' -- "$REPO/$relative")
      printf '%s\0%s\0' "$relative" "$size"
      cat -- "$REPO/$relative"
      printf '\0'
    done < <(candidate_files)
  } | sha256sum | awk '{print $1}'
}

validate_python_runtime() {
  PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -c \
    'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} or newer is required"
}

validate_candidate_python() {
  local output
  if ! output=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/src" "$PYTHON" -c \
    'from workspace_mcp_manager.cli import build_parser; from workspace_mcp_manager.domain import DesiredInstance; build_parser(); print("OK")' \
    2>&1); then
    die "candidate manager validation failed: ${output: -2000}"
  fi
  [[ "$output" == *OK* ]] || die "candidate manager validation returned an unexpected result"
}

INSTANCE_COUNT=0
INSTANCES_VALID=true
VALIDATION_ERROR=""

validate_declarations() {
  local registry="$ACCOUNT_HOME/.config/workspace-mcp-manager/instances"
  local declaration output
  INSTANCE_COUNT=0
  INSTANCES_VALID=true
  VALIDATION_ERROR=""
  [[ -d "$registry" ]] || return 0
  while IFS= read -r -d '' declaration; do
    INSTANCE_COUNT=$((INSTANCE_COUNT + 1))
    if ! output=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/src" "$PYTHON" -c \
      'import sys; from workspace_mcp_manager.domain import DesiredInstance; DesiredInstance.from_json(sys.stdin.read())' \
      <"$declaration" 2>&1); then
      INSTANCES_VALID=false
      VALIDATION_ERROR="cannot validate declaration $declaration: ${output: -1800}"
      return 1
    fi
  done < <(find "$registry" -maxdepth 1 -type f -name '*.json' -print0 | sort -z)
}

installed_fingerprint() {
  local current="$TOOLKIT_ROOT/current"
  local target fingerprint
  [[ -L "$current" ]] || return 0
  target=$(readlink -- "$current") || return 0
  [[ "$target" == releases/* ]] || return 0
  fingerprint=${target#releases/}
  [[ -n "$fingerprint" && "$fingerprint" != */* ]] || return 0
  [[ -d "$TOOLKIT_ROOT/releases/$fingerprint" ]] || return 0
  printf '%s' "$fingerprint"
}

entrypoints_valid() {
  local name expected path
  local names=(workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot)
  for name in "${names[@]}"; do
    expected="../lib/workspace-mcp-manager/current/bin/$name"
    path="$PREFIX/bin/$name"
    [[ -L "$path" ]] || return 1
    [[ "$(readlink -- "$path")" == "$expected" ]] || return 1
    [[ -x "$path" ]] || return 1
  done
  return 0
}

release_layout_valid() {
  local release=$1
  [[ -d "$release/src/workspace_mcp_manager" ]] || return 1
  [[ -f "$release/requirements-tui.lock" ]] || return 1
  [[ -f "$release/install.json" ]] || return 1
  [[ -x "$release/bin/workspace-mcp-manager" ]] || return 1
  [[ -x "$release/bin/workspace-mcp-manager-tui" ]] || return 1
  [[ -x "$release/bin/workspace-mcp-reboot" ]] || return 1
  [[ -d "$release/tui-runtime/site-packages/textual" ]] || return 1
  return 0
}

report_json() {
  local candidate=$1 installed=$2 links_valid=$3
  local installed_bool=false upgrade_required=false validation_suffix=""
  [[ -n "$installed" ]] && installed_bool=true
  [[ -n "$installed" && "$installed" != "$candidate" ]] && upgrade_required=true
  if [[ -n "$VALIDATION_ERROR" ]]; then
    validation_suffix=",\"validation_error\":\"$(json_escape "$VALIDATION_ERROR")\""
  fi
  printf '{"candidate_fingerprint":"%s","installed_fingerprint":%s,"installed":%s,"upgrade_required":%s,"instance_count":%d,"instances_valid":%s,"entrypoints_valid":%s%s}\n' \
    "$candidate" \
    "$(if [[ -n "$installed" ]]; then printf '\"%s\"' "$installed"; else printf 'null'; fi)" \
    "$installed_bool" "$upgrade_required" "$INSTANCE_COUNT" "$INSTANCES_VALID" "$links_valid" "$validation_suffix"
}

write_wrapper_manager() {
  local path=$1 final=$2
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'PYTHON=%q\n' "$PYTHON"
    printf 'SRC=%q\n' "$final/src"
    printf 'export PYTHONPATH="$SRC"\nexec "$PYTHON" -m workspace_mcp_manager.cli "$@"\n'
  } | atomic_file_from_stdin "$path" 0755
}

write_wrapper_reboot() {
  local path=$1 final=$2
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'PYTHON=%q\n' "$PYTHON"
    printf 'SRC=%q\n' "$final/src"
    printf 'export PYTHONPATH="$SRC"\n'
    printf 'exec "$PYTHON" -c '\''from workspace_mcp_manager.reboot import reboot_bridge_main; raise SystemExit(reboot_bridge_main())'\'' "$@"\n'
  } | atomic_file_from_stdin "$path" 0755
}

write_wrapper_tui() {
  local path=$1 final=$2
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'PYTHON=%q\n' "$PYTHON"
    printf 'SRC=%q\n' "$final/src"
    printf 'TUI_SITE=%q\n' "$final/tui-runtime/site-packages"
    printf 'MANAGER=%q\n' "$final/bin/workspace-mcp-manager"
    printf 'if [[ ! -d "$TUI_SITE/textual" ]]; then\n'
    printf '  printf '\''workspace-mcp-manager-tui: isolated Textual runtime is unavailable\\n'\'' >&2\n'
    printf '  exit 70\nfi\n'
    printf 'export WORKSPACE_MCP_MANAGER_BIN="$MANAGER" WORKSPACE_MCP_TUI_SITE="$TUI_SITE" WORKSPACE_MCP_SRC="$SRC"\n'
    printf 'exec "$PYTHON" -I -S -c '\''import os,sys; sys.path[:0]=[os.environ["WORKSPACE_MCP_TUI_SITE"],os.environ["WORKSPACE_MCP_SRC"]]; from workspace_mcp_manager.tui import main; raise SystemExit(main())'\'' "$@"\n'
  } | atomic_file_from_stdin "$path" 0755
}

install_tui_runtime() {
  local stage=$1 lock_file=$2
  local runtime_site="$stage/tui-runtime/site-packages"
  local pip_bootstrap version
  mkdir -p -- "$runtime_site"
  pip_bootstrap='import mimetypes,sys; mimetypes.knownfiles=[]; from pip._internal.cli.main import main; raise SystemExit(main(sys.argv[1:]))'
  if ! PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -c "$pip_bootstrap" \
      install --disable-pip-version-check --require-hashes --only-binary=:all: \
      --target "$runtime_site" -r "$lock_file"; then
    die "cannot install hash-locked TUI runtime"
  fi
  if ! version=$(TEXTUAL_SITE="$runtime_site" "$PYTHON" -I -S -c \
      'import os,sys; sys.path.insert(0,os.environ["TEXTUAL_SITE"]); import textual; print(textual.__version__)' 2>&1); then
    die "isolated TUI runtime validation failed: ${version: -2000}"
  fi
  [[ "$version" == "$TEXTUAL_VERSION" ]] || die "isolated TUI runtime version mismatch: $version"
}

write_manifest() {
  local path=$1 fingerprint=$2 lock_sha=$3
  {
    printf '{\n'
    printf '  "schema_version": 2,\n'
    printf '  "source_fingerprint": "%s",\n' "$fingerprint"
    printf '  "python": "%s",\n' "$(json_escape "$PYTHON")"
    printf '  "installer": "bash",\n'
    printf '  "tui_runtime": {\n'
    printf '    "textual_version": "%s",\n' "$TEXTUAL_VERSION"
    printf '    "lock_sha256": "%s",\n' "$lock_sha"
    printf '    "isolation": "python-I-S+release-site-packages"\n'
    printf '  }\n'
    printf '}\n'
  } | atomic_file_from_stdin "$path" 0644
}

stage_release() {
  local fingerprint=$1
  local releases="$TOOLKIT_ROOT/releases"
  local final="$releases/$fingerprint"
  local stage lock_sha
  if [[ -e "$final" ]]; then
    [[ -d "$final" && ! -L "$final" ]] || die "release path is not a regular directory: $final"
    release_layout_valid "$final" || die "existing release fingerprint is incomplete/corrupt: $fingerprint"
    printf '%s' "$final"
    return 0
  fi

  mkdir -p -- "$releases"
  stage="$releases/.stage-${fingerprint:0:12}-$$-$RANDOM"
  [[ ! -e "$stage" ]] || die "release staging collision: $stage"
  mkdir -m 0700 -- "$stage"
  ACTIVE_STAGE=$stage

  cp -a -- "$REPO/src" "$stage/src"
  cp -- "$REPO/pyproject.toml" "$stage/pyproject.toml"
  cp -- "$REPO/requirements-tui.lock" "$stage/requirements-tui.lock"
  cp -- "$REPO/scripts/install_toolkit.sh" "$stage/install_toolkit.sh"
  chmod 0755 "$stage/install_toolkit.sh"

  install_tui_runtime "$stage" "$stage/requirements-tui.lock"
  mkdir -m 0755 -- "$stage/bin"
  write_wrapper_manager "$stage/bin/workspace-mcp-manager" "$final"
  write_wrapper_tui "$stage/bin/workspace-mcp-manager-tui" "$final"
  write_wrapper_reboot "$stage/bin/workspace-mcp-reboot" "$final"
  lock_sha=$(sha256sum -- "$stage/requirements-tui.lock" | awk '{print $1}')
  write_manifest "$stage/install.json" "$fingerprint" "$lock_sha"

  if command -v sync >/dev/null 2>&1; then
    sync -f "$stage/install.json" 2>/dev/null || true
    sync -f "$stage" 2>/dev/null || true
  fi
  mv -T -- "$stage" "$final"
  ACTIVE_STAGE=""
  printf '%s' "$final"
}

switch_current() {
  local fingerprint=$1
  atomic_symlink "$TOOLKIT_ROOT/current" "releases/$fingerprint"
  atomic_symlink "$PREFIX/bin/workspace-mcp-manager" "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager"
  atomic_symlink "$PREFIX/bin/workspace-mcp-manager-tui" "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager-tui"
  atomic_symlink "$PREFIX/bin/workspace-mcp-reboot" "../lib/workspace-mcp-manager/current/bin/workspace-mcp-reboot"
}

validate_installed() {
  "$PREFIX/bin/workspace-mcp-manager" --help >/dev/null || die "installed manager entrypoint validation failed"
  "$PREFIX/bin/workspace-mcp-manager-tui" --help >/dev/null || die "installed TUI entrypoint validation failed"
  [[ -x "$PREFIX/bin/workspace-mcp-reboot" ]] || die "installed reboot bridge entrypoint validation failed"
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
ACCOUNT_HOME=${HOME:-}
PREFIX=""
UPGRADE=false
CHECK=false
ACTIVE_STAGE=""

cleanup() {
  if [[ -n "$ACTIVE_STAGE" && -d "$ACTIVE_STAGE" ]]; then
    rm -rf -- "$ACTIVE_STAGE"
  fi
}
trap cleanup EXIT INT TERM

while (($#)); do
  case "$1" in
    --repo)
      (($# >= 2)) || die "--repo requires a path"
      REPO=$2
      shift 2
      ;;
    --account-home)
      (($# >= 2)) || die "--account-home requires a path"
      ACCOUNT_HOME=$2
      shift 2
      ;;
    --prefix)
      (($# >= 2)) || die "--prefix requires a path"
      PREFIX=$2
      shift 2
      ;;
    --upgrade)
      UPGRADE=true
      shift
      ;;
    --check)
      CHECK=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ -n "$ACCOUNT_HOME" ]] || die "account home is unavailable"

for command in bash python3 sha256sum stat find sort grep awk cat cp mv ln readlink realpath mktemp chmod dirname; do
  require_command "$command"
done

REPO=$(absolute_path "$REPO")
ACCOUNT_HOME=$(absolute_path "$ACCOUNT_HOME")
if [[ -z "$PREFIX" ]]; then
  PREFIX="$ACCOUNT_HOME/.local"
fi
PREFIX=$(absolute_path "$PREFIX")
[[ "$REPO" == /* && "$ACCOUNT_HOME" == /* && "$PREFIX" == /* ]] || die "repo, account home and prefix must resolve to absolute paths"

PYTHON=$(command -v python3)
PYTHON=$(absolute_path "$PYTHON")
TOOLKIT_ROOT="$PREFIX/lib/workspace-mcp-manager"

validate_python_runtime
validate_candidate_layout
validate_candidate_python
validate_declarations || true

CANDIDATE_FINGERPRINT=$(source_fingerprint)
INSTALLED_FINGERPRINT=$(installed_fingerprint)
if entrypoints_valid; then
  LINKS_VALID=true
else
  LINKS_VALID=false
fi

if $CHECK; then
  report_json "$CANDIDATE_FINGERPRINT" "$INSTALLED_FINGERPRINT" "$LINKS_VALID"
  $INSTANCES_VALID && exit 0
  exit 1
fi

$INSTANCES_VALID || die "existing manager declaration validation failed: $VALIDATION_ERROR"

MANAGER_PATH="$PREFIX/bin/workspace-mcp-manager"
FOREIGN_OR_UNMANAGED=false
if [[ -e "$MANAGER_PATH" || -L "$MANAGER_PATH" ]]; then
  [[ -n "$INSTALLED_FINGERPRINT" ]] || FOREIGN_OR_UNMANAGED=true
fi

if [[ -n "$INSTALLED_FINGERPRINT" && "$INSTALLED_FINGERPRINT" == "$CANDIDATE_FINGERPRINT" && "$LINKS_VALID" == true ]]; then
  release_layout_valid "$TOOLKIT_ROOT/releases/$INSTALLED_FINGERPRINT" \
    || die "installed current release is incomplete/corrupt: $INSTALLED_FINGERPRINT"
  report_json "$CANDIDATE_FINGERPRINT" "$INSTALLED_FINGERPRINT" true
  exit 0
fi

if [[ -n "$INSTALLED_FINGERPRINT" && "$INSTALLED_FINGERPRINT" != "$CANDIDATE_FINGERPRINT" ]] || $FOREIGN_OR_UNMANAGED; then
  $UPGRADE || die "shared toolkit replacement requires explicit --upgrade"
fi

stage_release "$CANDIDATE_FINGERPRINT" >/dev/null
switch_current "$CANDIDATE_FINGERPRINT"
validate_installed
entrypoints_valid || die "installed stable entrypoints are invalid"
report_json "$CANDIDATE_FINGERPRINT" "$CANDIDATE_FINGERPRINT" true
