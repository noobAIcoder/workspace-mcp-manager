#!/usr/bin/env bash
set -euo pipefail

PRODUCT_NAME="workspace-mcp-manager"
PRODUCTION_REPOSITORY="https://github.com/noobAIcoder/workspace-mcp-manager.git"
PRODUCTION_REF="refs/heads/release"
SUPPORTED_OS_ID="ubuntu"
SUPPORTED_OS_VERSION="24.04"
SUPPORTED_ARCH="x86_64"
MIN_BOOTSTRAP_PYTHON_MAJOR=3
MIN_BOOTSTRAP_PYTHON_MINOR=11
LOCK_TIMEOUT_SECONDS=5
JOURNAL_SCHEMA_VERSION=1
RECEIPT_SCHEMA_VERSION=1
INSTALL_SCHEMA_VERSION=3

die() {
  printf '%s installer: %s\n' "$PRODUCT_NAME" "$*" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage: scripts/install.sh [options]

Modes:
  (default)              Install a new standalone distribution
  --upgrade              Upgrade an existing standalone distribution
  --recover-only         Recover unfinished local distribution state only
  --check                Read-only local/candidate inspection

Options:
  --configure            Launch the installed TUI after a successful commit/NOOP
  --repo PATH            Source checkout (default: parent of scripts/)
  --account-home PATH    Target account home (default: $HOME)
  --prefix PATH          Install prefix (default: <account-home>/.local)
  --repository URL       Acquisition repository provenance
  --release-ref REF      Acquisition ref provenance
  --release-commit SHA   Exact acquired checkout commit

Qualification/development only:
  --qualification
  --qualification-artifact-dir PATH
  --qualification-failpoint NAME

Production repository/ref are fixed. Overrides are rejected unless
--qualification is explicit.
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

absolute_path() {
  realpath -m -- "$1"
}

is_sha256() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

is_git_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

fsync_path() {
  local path=$1
  [[ -e "$path" || -L "$path" ]] || return 0
  "$BOOTSTRAP_PYTHON" - "$path" <<'PY'
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
flags = os.O_RDONLY
if path.is_dir():
    flags |= getattr(os, "O_DIRECTORY", 0)
fd = os.open(path, flags)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

fsync_tree() {
  local root=$1 python=$2
  "$python" - "$root" <<'PY'
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
directories = []
for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
    base_path = pathlib.Path(base)
    directories.append(base_path)
    for name in files:
        path = base_path / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            raise SystemExit(f"candidate changed during fsync: {path}")
        if stat.S_ISREG(mode):
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
for path in reversed(directories):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
PY
}

atomic_symlink() {
  local path=$1 target=$2
  local directory temporary
  directory=$(dirname -- "$path")
  mkdir -p -- "$directory"
  temporary="$directory/.${path##*/}.$$.$RANDOM.tmp"
  rm -f -- "$temporary"
  ln -s -- "$target" "$temporary"
  mv -Tf -- "$temporary" "$path"
  fsync_path "$directory"
}

atomic_file_from_stdin() {
  local path=$1 mode=$2
  local directory temporary
  directory=$(dirname -- "$path")
  mkdir -p -- "$directory"
  temporary=$(mktemp "$directory/.${path##*/}.XXXXXX.tmp")
  cat >"$temporary"
  chmod "$mode" "$temporary"
  fsync_path "$temporary"
  mv -Tf -- "$temporary" "$path"
  fsync_path "$directory"
}

bootstrap_python_check() {
  BOOTSTRAP_PYTHON=$(command -v python3) || die "Python 3.11 or newer is required for bounded bootstrap validation"
  BOOTSTRAP_PYTHON=$(absolute_path "$BOOTSTRAP_PYTHON")
  PYTHONDONTWRITEBYTECODE=1 "$BOOTSTRAP_PYTHON" -c \
    'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python ${MIN_BOOTSTRAP_PYTHON_MAJOR}.${MIN_BOOTSTRAP_PYTHON_MINOR} or newer is required"
}

strict_lock_validate() {
  local lock=$1
  [[ -f "$lock" && ! -L "$lock" ]] || die "install-versions.lock is missing/not regular"
  "$BOOTSTRAP_PYTHON" - "$lock" "$REPO/requirements-coding-tools.lock" <<'PY' || die "install-versions.lock validation failed"
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse

lock_path = pathlib.Path(sys.argv[1])
coding_lock = pathlib.Path(sys.argv[2])

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result

try:
    data = json.loads(lock_path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
except Exception as exc:
    raise SystemExit(f"invalid strict JSON: {exc}")

def exact_keys(value, expected, where):
    if not isinstance(value, dict):
        raise SystemExit(f"{where} must be an object")
    actual = set(value)
    expected = set(expected)
    if actual != expected:
        raise SystemExit(f"{where} fields mismatch: missing={sorted(expected-actual)} unknown={sorted(actual-expected)}")

exact_keys(data, {"schema_version", "platform", "architecture", "components"}, "root")
if data["schema_version"] != 1:
    raise SystemExit("unsupported schema_version")
if data["platform"] != "linux" or data["architecture"] != "x86_64":
    raise SystemExit("unsupported lock platform/architecture")

components = data["components"]
exact_keys(components, {"python", "uv", "coding_tools_mcp", "tunnel_client"}, "components")
component_fields = {
    "version", "source_kind", "source_identity", "acquisition_location", "sha256",
    "platform", "architecture", "dependency_lock_identity", "dependencies",
    "validator_id", "expected_observable_identity",
}
dependency_fields = {"name", "version", "source_identity", "acquisition_location", "sha256"}
validators = {
    "python": "cpython-3.12",
    "uv": "uv-cli",
    "coding_tools_mcp": "coding-tools-mcp-0.3",
    "tunnel_client": "tunnel-client-0.0.11",
}
source_kinds = {"github_release_asset", "pypi_wheel"}
hex64 = re.compile(r"^[0-9a-f]{64}$")

for name, component in components.items():
    exact_keys(component, component_fields, f"components.{name}")
    for field in ("version", "source_kind", "source_identity", "acquisition_location", "sha256", "platform", "architecture", "validator_id", "expected_observable_identity"):
        if not isinstance(component[field], str) or not component[field]:
            raise SystemExit(f"components.{name}.{field} must be a non-empty string")
    if component["source_kind"] not in source_kinds:
        raise SystemExit(f"unsupported source_kind for {name}")
    if not hex64.fullmatch(component["sha256"]):
        raise SystemExit(f"invalid sha256 for {name}")
    if component["platform"] != data["platform"] or component["architecture"] != data["architecture"]:
        raise SystemExit(f"component platform/architecture mismatch for {name}")
    if component["validator_id"] != validators[name]:
        raise SystemExit(f"unsupported validator_id for {name}")
    parsed = urllib.parse.urlparse(component["acquisition_location"])
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit(f"acquisition_location must be HTTPS for {name}")
    dependency_lock = component["dependency_lock_identity"]
    if dependency_lock is not None and (not isinstance(dependency_lock, str) or not dependency_lock):
        raise SystemExit(f"invalid dependency_lock_identity for {name}")
    dependencies = component["dependencies"]
    if not isinstance(dependencies, list):
        raise SystemExit(f"dependencies must be an array for {name}")
    seen_dependencies = set()
    for index, dependency in enumerate(dependencies):
        exact_keys(dependency, dependency_fields, f"components.{name}.dependencies[{index}]")
        for field in dependency_fields:
            if not isinstance(dependency[field], str) or not dependency[field]:
                raise SystemExit(f"invalid dependency field {field} for {name}")
        if dependency["name"].lower() in seen_dependencies:
            raise SystemExit(f"duplicate dependency for {name}: {dependency['name']}")
        seen_dependencies.add(dependency["name"].lower())
        if not hex64.fullmatch(dependency["sha256"]):
            raise SystemExit(f"invalid dependency sha256 for {name}")
        parsed = urllib.parse.urlparse(dependency["acquisition_location"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise SystemExit(f"dependency acquisition_location must be HTTPS for {name}")

coding = components["coding_tools_mcp"]
if not coding_lock.is_file() or coding_lock.is_symlink():
    raise SystemExit("requirements-coding-tools.lock is missing/not regular")
digest = hashlib.sha256(coding_lock.read_bytes()).hexdigest()
expected_identity = f"requirements-coding-tools.lock@sha256:{digest}"
if coding["dependency_lock_identity"] != expected_identity:
    raise SystemExit("coding-tools dependency lock identity mismatch")
if len(coding["dependencies"]) != 1 or coding["dependencies"][0]["name"] != "PyJWT":
    raise SystemExit("coding-tools dependency closure must contain exactly pinned PyJWT")
coding_lock_text = coding_lock.read_text(encoding="utf-8")
if f"coding-tools-mcp=={coding['version']}" not in coding_lock_text or f"--hash=sha256:{coding['sha256']}" not in coding_lock_text:
    raise SystemExit("coding-tools component is inconsistent with its dependency lock")
dependency = coding["dependencies"][0]
if f"PyJWT=={dependency['version']}" not in coding_lock_text or f"--hash=sha256:{dependency['sha256']}" not in coding_lock_text:
    raise SystemExit("PyJWT artifact is inconsistent with coding-tools dependency lock")
PY
}

lock_get() {
  local component=$1 field=$2
  "$BOOTSTRAP_PYTHON" - "$REPO/install-versions.lock" "$component" "$field" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding="utf-8"))
value=data["components"][sys.argv[2]][sys.argv[3]]
if value is None:
    raise SystemExit(0)
if not isinstance(value, str):
    raise SystemExit("requested lock value is not scalar")
print(value)
PY
}

lock_dependencies() {
  local component=$1
  "$BOOTSTRAP_PYTHON" - "$REPO/install-versions.lock" "$component" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding="utf-8"))
for dependency in data["components"][sys.argv[2]]["dependencies"]:
    values=[dependency[k] for k in ("name","version","source_identity","acquisition_location","sha256")]
    if any("\t" in v or "\n" in v for v in values):
        raise SystemExit("unsafe dependency scalar")
    print("\t".join(values))
PY
}

product_version() {
  "$BOOTSTRAP_PYTHON" - "$REPO/pyproject.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    data=tomllib.load(handle)
value=data.get("project", {}).get("version")
if not isinstance(value, str) or not value:
    raise SystemExit("project.version is unavailable")
print(value)
PY
}

validate_candidate_source() {
  local required
  for required in \
    pyproject.toml \
    requirements-tui.lock \
    requirements-coding-tools.lock \
    install-versions.lock \
    scripts/install.sh \
    scripts/upgrade.sh; do
    [[ -f "$REPO/$required" && ! -L "$REPO/$required" ]] || die "candidate file is missing/not regular: $required"
  done
  [[ -d "$REPO/src/workspace_mcp_manager" && ! -L "$REPO/src/workspace_mcp_manager" ]] || die "candidate source package is missing/not regular"
  if find "$REPO/src/workspace_mcp_manager" -type l -print -quit | grep -q .; then
    die "candidate source package contains symlinks"
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/src" "$BOOTSTRAP_PYTHON" -c \
      'from workspace_mcp_manager.cli import build_parser; from workspace_mcp_manager.domain import DesiredInstance; build_parser()' \
      >/dev/null 2>&1; then
    die "candidate manager source validation failed"
  fi
}

manager_source_digest() {
  "$BOOTSTRAP_PYTHON" - "$REPO" <<'PY'
import hashlib, pathlib, sys
root=pathlib.Path(sys.argv[1])
paths=[root/"pyproject.toml", root/"requirements-tui.lock"]
paths.extend(sorted((root/"src/workspace_mcp_manager").rglob("*.py")))
h=hashlib.sha256()
for path in paths:
    rel=path.relative_to(root).as_posix().encode()
    body=path.read_bytes()
    h.update(len(rel).to_bytes(8,"big")); h.update(rel)
    h.update(len(body).to_bytes(8,"big")); h.update(body)
print(h.hexdigest())
PY
}

validate_checkout_provenance() {
  [[ -n "$RELEASE_COMMIT" ]] || die "--release-commit is required"
  is_git_sha "$RELEASE_COMMIT" || die "release commit must be a 40-character lowercase Git SHA"

  if ! $QUALIFICATION; then
    [[ "$REPOSITORY" == "$PRODUCTION_REPOSITORY" ]] || die "production repository is fixed to $PRODUCTION_REPOSITORY"
    [[ "$RELEASE_REF" == "$PRODUCTION_REF" ]] || die "production ref is fixed to $PRODUCTION_REF"
  fi

  if git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local head remote fetched dirty
    head=$(git -C "$REPO" rev-parse HEAD) || die "cannot resolve checkout HEAD"
    [[ "$head" == "$RELEASE_COMMIT" ]] || die "checkout HEAD disagrees with release commit provenance"
    dirty=$(git -C "$REPO" status --porcelain=v1 --untracked-files=all) || die "cannot inspect checkout worktree state"
    [[ -z "$dirty" ]] || die "acquired checkout is not clean; release commit would not identify candidate content exactly"
    remote=$(git -C "$REPO" config --get remote.origin.url || true)
    if [[ -n "$remote" ]]; then
      [[ "$remote" == "$REPOSITORY" ]] || die "checkout remote.origin.url disagrees with repository provenance"
    elif ! $QUALIFICATION; then
      die "production checkout has no origin repository provenance"
    fi
    if ! $QUALIFICATION; then
      fetched=$(git -C "$REPO" rev-parse 'refs/remotes/origin/workspace-mcp-manager-distribution^{commit}' 2>/dev/null || true)
      [[ "$fetched" == "$RELEASE_COMMIT" ]] || die "production checkout does not retain the verified fetched-ref provenance"
    fi
  elif ! $QUALIFICATION; then
    die "production installation requires a Git checkout with acquisition provenance"
  fi
}

preflight_read_only() {
  local command os_id os_version kernel arch
  for command in bash git curl python3 sha256sum realpath mktemp flock stat find sort grep awk cat cp mv ln readlink chmod dirname tar unzip date systemctl; do
    require_command "$command"
  done
  bootstrap_python_check
  os_id=""; os_version=""
  if [[ -f /etc/os-release ]]; then
    os_id=$(grep -E '^ID=' /etc/os-release | head -1 | cut -d= -f2- | tr -d '"')
    os_version=$(grep -E '^VERSION_ID=' /etc/os-release | head -1 | cut -d= -f2- | tr -d '"')
  fi
  [[ "$os_id" == "$SUPPORTED_OS_ID" && "$os_version" == "$SUPPORTED_OS_VERSION" ]] \
    || die "supported host is Ubuntu $SUPPORTED_OS_VERSION; observed ${os_id:-unknown} ${os_version:-unknown}"
  kernel=$(uname -r)
  [[ "$kernel" == *microsoft* || "$kernel" == *Microsoft* || "$kernel" == *WSL* ]] \
    || die "supported host requires WSL2"
  arch=$(uname -m)
  [[ "$arch" == "$SUPPORTED_ARCH" ]] || die "supported architecture is $SUPPORTED_ARCH; observed $arch"
  systemctl --user show-environment >/dev/null 2>&1 || die "systemd --user is unavailable"
}

current_target() {
  local current="$TOOLKIT_ROOT/current"
  [[ -L "$current" ]] || return 0
  readlink -- "$current"
}

expected_entrypoint_target() {
  printf '../lib/workspace-mcp-manager/current/bin/%s' "$1"
}

entrypoint_exact() {
  local name=$1 path="$PREFIX/bin/$1" expected
  expected=$(expected_entrypoint_target "$name")
  [[ -L "$path" ]] || return 1
  [[ "$(readlink -- "$path")" == "$expected" ]] || return 1
  return 0
}

stable_entrypoints_standalone_valid() {
  local name
  for name in workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot workspace-mcp-manager-upgrade; do
    entrypoint_exact "$name" || return 1
  done
  return 0
}

stable_entrypoints_predecessor_valid() {
  local name path
  for name in workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot; do
    entrypoint_exact "$name" || return 1
  done
  path="$PREFIX/bin/workspace-mcp-manager-upgrade"
  [[ ! -e "$path" && ! -L "$path" ]] || return 1
  return 0
}

predecessor_release_valid() {
  local release=$1
  [[ -d "$release" && ! -L "$release" ]] || return 1
  [[ -d "$release/src/workspace_mcp_manager" ]] || return 1
  [[ -f "$release/requirements-tui.lock" && ! -L "$release/requirements-tui.lock" ]] || return 1
  [[ -f "$release/install.json" && ! -L "$release/install.json" ]] || return 1
  [[ -x "$release/bin/workspace-mcp-manager" ]] || return 1
  [[ -x "$release/bin/workspace-mcp-manager-tui" ]] || return 1
  [[ -x "$release/bin/workspace-mcp-reboot" ]] || return 1
  [[ -d "$release/tui-runtime/site-packages/textual" ]] || return 1
  "$BOOTSTRAP_PYTHON" - "$release/install.json" <<'PY' >/dev/null 2>&1 || return 1
import json,sys
data=json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if data.get("schema_version") == 2 else 1)
PY
  return 0
}

release_validate() {
  local release=$1 expected_commit=${2-}
  [[ -d "$release" && ! -L "$release" ]] || return 1
  [[ -f "$release/install.json" && ! -L "$release/install.json" ]] || return 1
  "$BOOTSTRAP_PYTHON" - "$release" "$expected_commit" <<'PY' >/dev/null 2>&1
import hashlib
import json
import os
import pathlib
import stat
import sys

root=pathlib.Path(sys.argv[1])
expected_commit=sys.argv[2]
try:
    data=json.loads((root/"install.json").read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
required={
    "schema_version","product_version","repository","release_ref","release_commit",
    "distribution_id","manager_source_digest","locked_inputs","components",
    "manifest_scope","content_manifest",
}
if set(data) != required or data["schema_version"] != 3:
    raise SystemExit(1)
if not isinstance(data["release_commit"], str) or not __import__("re").fullmatch(r"[0-9a-f]{40}", data["release_commit"]):
    raise SystemExit(1)
if expected_commit and data["release_commit"] != expected_commit:
    raise SystemExit(1)
if data["distribution_id"] != f"dist-{data['release_commit']}" or root.name != data["distribution_id"]:
    raise SystemExit(1)
if data["manifest_scope"] != "all-immutable-files-except-install.json; install.json-sha256-is-anchored-by-receipt":
    raise SystemExit(1)
manifest=data["content_manifest"]
if not isinstance(manifest, list):
    raise SystemExit(1)
expected={}
for item in manifest:
    if not isinstance(item, dict) or item.get("path") in expected:
        raise SystemExit(1)
    path=item.get("path")
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in pathlib.PurePosixPath(path).parts:
        raise SystemExit(1)
    expected[path]=item

actual={}
for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
    base_path=pathlib.Path(base)
    names=list(files)+[name for name in dirs if (base_path/name).is_symlink()]
    for name in names:
        path=base_path/name
        rel=path.relative_to(root).as_posix()
        if rel == "install.json":
            continue
        st=path.lstat()
        mode=f"{stat.S_IMODE(st.st_mode):04o}"
        if stat.S_ISREG(st.st_mode):
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            actual[rel]={"path":rel,"type":"file","sha256":digest,"mode":mode}
        elif stat.S_ISLNK(st.st_mode):
            actual[rel]={"path":rel,"type":"symlink","target":os.readlink(path),"mode":mode}
        else:
            raise SystemExit(1)
if actual != expected:
    raise SystemExit(1)
PY
}

root_release_names_valid() {
  local releases="$TOOLKIT_ROOT/releases" name
  [[ -d "$releases" && ! -L "$releases" ]] || return 1
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    if [[ "$name" =~ ^dist-[0-9a-f]{40}$ || "$name" =~ ^[0-9a-f]{64}$ ]]; then
      continue
    fi
    return 1
  done < <(find "$releases" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)
  return 0
}

classify_ownership() {
  local root="$TOOLKIT_ROOT" target release entry name
  local stable=(workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot workspace-mcp-manager-upgrade)
  if [[ ! -e "$root" && ! -L "$root" ]]; then
    for name in "${stable[@]}"; do
      entry="$PREFIX/bin/$name"
      if [[ -e "$entry" || -L "$entry" ]]; then
        printf 'foreign_or_ambiguous'
        return 0
      fi
    done
    printf 'absent'
    return 0
  fi
  [[ -d "$root" && ! -L "$root" ]] || { printf 'foreign_or_ambiguous'; return 0; }
  for entry in "$root"/* "$root"/.[!.]* "$root"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    case "${entry##*/}" in
      releases|current) ;;
      *) printf 'foreign_or_ambiguous'; return 0 ;;
    esac
  done
  [[ -L "$root/current" ]] || { printf 'foreign_or_ambiguous'; return 0; }
  root_release_names_valid || { printf 'foreign_or_ambiguous'; return 0; }
  target=$(readlink -- "$root/current") || { printf 'foreign_or_ambiguous'; return 0; }
  if [[ "$target" =~ ^releases/dist-([0-9a-f]{40})$ ]]; then
    release="$root/$target"
    release_validate "$release" "${BASH_REMATCH[1]}" && stable_entrypoints_standalone_valid \
      && { printf 'standalone_distribution'; return 0; }
    printf 'foreign_or_ambiguous'
    return 0
  fi
  if [[ "$target" =~ ^releases/([0-9a-f]{64})$ ]]; then
    release="$root/$target"
    predecessor_release_valid "$release" && stable_entrypoints_predecessor_valid \
      && { printf 'recognized_existing'; return 0; }
    printf 'foreign_or_ambiguous'
    return 0
  fi
  printf 'foreign_or_ambiguous'
}

created_entrypoint_contains() {
  local created=$1 wanted=$2 item
  while IFS= read -r item; do
    [[ "$item" == "$wanted" ]] && return 0
  done <<<"$created"
  return 1
}

classify_ownership_for_check() {
  local journal="$STATE_ROOT/distribution-transaction.json"
  if [[ ! -f "$journal" ]]; then
    classify_ownership
    return 0
  fi
  journal_validate || { printf 'foreign_or_ambiguous'; return 0; }
  local previous_class previous_current candidate_id candidate_commit candidate_release actual created name path
  previous_class=$(journal_get previous.classification)
  previous_current=$(journal_get previous.current_target || true)
  candidate_id=$(journal_get candidate.distribution_id)
  candidate_commit=$(journal_get candidate.release_commit)
  candidate_release=$(journal_get candidate.release_path)
  created=$(journal_get previous.transaction_created_entrypoints || true)
  actual=$(current_target || true)
  if [[ "$actual" == "releases/$candidate_id" ]]; then
    if release_validate "$candidate_release" "$candidate_commit" && stable_entrypoints_standalone_valid; then
      printf 'standalone_distribution'
    else
      printf 'foreign_or_ambiguous'
    fi
    return 0
  fi
  [[ "$actual" == "$previous_current" ]] || { printf 'foreign_or_ambiguous'; return 0; }
  case "$previous_class" in
    absent)
      [[ -z "$actual" ]] || { printf 'foreign_or_ambiguous'; return 0; }
      for name in workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot workspace-mcp-manager-upgrade; do
        path="$PREFIX/bin/$name"
        if created_entrypoint_contains "$created" "$name"; then
          entrypoint_exact "$name" || { printf 'foreign_or_ambiguous'; return 0; }
        elif [[ -e "$path" || -L "$path" ]]; then
          printf 'foreign_or_ambiguous'; return 0
        fi
      done
      printf 'absent'
      ;;
    recognized_existing)
      predecessor_release_valid "$TOOLKIT_ROOT/$previous_current" \
        || { printf 'foreign_or_ambiguous'; return 0; }
      for name in workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot; do
        entrypoint_exact "$name" || { printf 'foreign_or_ambiguous'; return 0; }
      done
      path="$PREFIX/bin/workspace-mcp-manager-upgrade"
      if created_entrypoint_contains "$created" workspace-mcp-manager-upgrade; then
        entrypoint_exact workspace-mcp-manager-upgrade || { printf 'foreign_or_ambiguous'; return 0; }
      elif [[ -e "$path" || -L "$path" ]]; then
        printf 'foreign_or_ambiguous'; return 0
      fi
      printf 'recognized_existing'
      ;;
    standalone_distribution)
      release_validate "$TOOLKIT_ROOT/$previous_current" && stable_entrypoints_standalone_valid \
        && printf 'standalone_distribution' || printf 'foreign_or_ambiguous'
      ;;
    *) printf 'foreign_or_ambiguous' ;;
  esac
}

receipt_distribution_id() {
  local receipt="$STATE_ROOT/distribution.json"
  [[ -f "$receipt" && ! -L "$receipt" ]] || return 0
  "$BOOTSTRAP_PYTHON" - "$receipt" <<'PY' 2>/dev/null || true
import json,sys
try: data=json.load(open(sys.argv[1], encoding="utf-8"))
except Exception: raise SystemExit(1)
value=data.get("distribution_id")
if isinstance(value,str): print(value)
PY
}

receipt_consistent_with_release() {
  local release=$1 receipt="$STATE_ROOT/distribution.json"
  [[ -f "$receipt" && ! -L "$receipt" ]] || return 1
  "$BOOTSTRAP_PYTHON" - "$release" "$receipt" <<'PY' >/dev/null 2>&1
import hashlib,json,pathlib,sys
release=pathlib.Path(sys.argv[1]); receipt=pathlib.Path(sys.argv[2])
try:
    install=json.loads((release/"install.json").read_text(encoding="utf-8"))
    evidence=json.loads(receipt.read_text(encoding="utf-8"))
except Exception: raise SystemExit(1)
if evidence.get("schema_version") != 1: raise SystemExit(1)
checks={
    "distribution_id": install.get("distribution_id"),
    "release_commit": install.get("release_commit"),
    "product_version": install.get("product_version"),
    "install_json_sha256": hashlib.sha256((release/"install.json").read_bytes()).hexdigest(),
}
raise SystemExit(0 if all(evidence.get(k)==v for k,v in checks.items()) else 1)
PY
}

artifact_filename() {
  "$BOOTSTRAP_PYTHON" - "$1" <<'PY'
import pathlib,sys,urllib.parse
path=urllib.parse.urlparse(sys.argv[1]).path
name=pathlib.PurePosixPath(urllib.parse.unquote(path)).name
if not name: raise SystemExit(1)
print(name)
PY
}

acquire_artifact() {
  local url=$1 expected_sha=$2 destination_dir=$3
  local name destination actual source
  name=$(artifact_filename "$url") || die "cannot derive artifact filename"
  destination="$destination_dir/$name"
  mkdir -p -- "$destination_dir"
  if [[ -n "$QUALIFICATION_ARTIFACT_DIR" ]]; then
    source="$QUALIFICATION_ARTIFACT_DIR/$name"
    [[ -f "$source" && ! -L "$source" ]] || die "qualification artifact is missing: $name"
    cp -- "$source" "$destination"
  else
    curl --proto '=https' --tlsv1.2 -fsSL -o "$destination" "$url" \
      || die "cannot acquire locked artifact: $name"
  fi
  actual=$(sha256sum -- "$destination" | awk '{print $1}')
  [[ "$actual" == "$expected_sha" ]] || die "artifact digest mismatch: $name"
  printf '%s' "$destination"
}

validate_tar_paths() {
  local archive=$1 prefix=$2
  local member
  while IFS= read -r member; do
    [[ "$member" == "$prefix"* ]] || die "archive member escapes expected prefix: $member"
    [[ "$member" != /* && "$member" != *'/../'* && "$member" != '../'* && "$member" != *'/..' ]] \
      || die "unsafe archive member: $member"
  done < <(tar -tzf "$archive")
}

provision_python() {
  local stage=$1 artifact_dir=$2 artifact url sha expected output
  url=$(lock_get python acquisition_location)
  sha=$(lock_get python sha256)
  expected=$(lock_get python expected_observable_identity)
  artifact=$(acquire_artifact "$url" "$sha" "$artifact_dir")
  mkdir -p -- "$stage/runtimes/python"
  validate_tar_paths "$artifact" 'python/'
  tar -xzf "$artifact" -C "$stage/runtimes/python" --strip-components=1
  [[ -x "$stage/runtimes/python/bin/python3" ]] || die "distribution Python executable is missing"
  output=$("$stage/runtimes/python/bin/python3" --version 2>&1) || die "distribution Python validation failed"
  [[ "$output" == "$expected" ]] || die "distribution Python identity mismatch: $output"
}

provision_uv() {
  local stage=$1 artifact_dir=$2 artifact url sha expected extract_root uv_path uvx_path output
  url=$(lock_get uv acquisition_location)
  sha=$(lock_get uv sha256)
  expected=$(lock_get uv expected_observable_identity)
  artifact=$(acquire_artifact "$url" "$sha" "$artifact_dir")
  validate_tar_paths "$artifact" 'uv-x86_64-unknown-linux-gnu/'
  extract_root="$artifact_dir/uv-extract"
  mkdir -p -- "$extract_root" "$stage/tools/uv"
  tar -xzf "$artifact" -C "$extract_root"
  uv_path="$extract_root/uv-x86_64-unknown-linux-gnu/uv"
  uvx_path="$extract_root/uv-x86_64-unknown-linux-gnu/uvx"
  [[ -x "$uv_path" && -x "$uvx_path" ]] || die "uv archive is incomplete"
  cp -- "$uv_path" "$stage/tools/uv/uv"
  cp -- "$uvx_path" "$stage/tools/uv/uvx"
  chmod 0755 "$stage/tools/uv/uv" "$stage/tools/uv/uvx"
  output=$("$stage/tools/uv/uv" --version 2>&1) || die "uv validation failed"
  [[ "$output" == "$expected" ]] || die "uv identity mismatch: $output"
}

write_manager_wrappers() {
  local stage=$1 final=$2
  mkdir -p -- "$stage/bin"
  cat <<EOF | atomic_file_from_stdin "$stage/bin/workspace-mcp-manager" 0755
#!/usr/bin/env bash
set -euo pipefail
PYTHON=$(printf '%q' "$final/runtimes/python/bin/python3")
SRC=$(printf '%q' "$final/manager/src")
export WORKSPACE_MCP_MANAGER_SRC="\$SRC"
exec "\$PYTHON" -B -I -S -c 'import os,sys; sys.path.insert(0,os.environ["WORKSPACE_MCP_MANAGER_SRC"]); from workspace_mcp_manager.cli import main; raise SystemExit(main())' "\$@"
EOF
  cat <<EOF | atomic_file_from_stdin "$stage/bin/workspace-mcp-manager-tui" 0755
#!/usr/bin/env bash
set -euo pipefail
PYTHON=$(printf '%q' "$final/runtimes/python/bin/python3")
SRC=$(printf '%q' "$final/manager/src")
SITE=$(printf '%q' "$final/manager/tui-runtime/site-packages")
export WORKSPACE_MCP_MANAGER_SRC="\$SRC" WORKSPACE_MCP_MANAGER_TUI_SITE="\$SITE"
exec "\$PYTHON" -B -I -S -c 'import os,sys; sys.path[:0]=[os.environ["WORKSPACE_MCP_MANAGER_TUI_SITE"],os.environ["WORKSPACE_MCP_MANAGER_SRC"]]; from workspace_mcp_manager.tui import main; raise SystemExit(main())' "\$@"
EOF
  cat <<EOF | atomic_file_from_stdin "$stage/bin/workspace-mcp-reboot" 0755
#!/usr/bin/env bash
set -euo pipefail
PYTHON=$(printf '%q' "$final/runtimes/python/bin/python3")
SRC=$(printf '%q' "$final/manager/src")
export WORKSPACE_MCP_MANAGER_SRC="\$SRC"
exec "\$PYTHON" -B -I -S -c 'import os,sys; sys.path.insert(0,os.environ["WORKSPACE_MCP_MANAGER_SRC"]); from workspace_mcp_manager.reboot import reboot_bridge_main; raise SystemExit(reboot_bridge_main())' "\$@"
EOF
  cat <<EOF | atomic_file_from_stdin "$stage/bin/workspace-mcp-manager-upgrade" 0755
#!/usr/bin/env bash
set -euo pipefail
exec $(printf '%q' "$final/distribution/upgrade.sh") "\$@"
EOF
}

provision_manager() {
  local stage=$1 final=$2
  local python="$stage/runtimes/python/bin/python3" uv="$stage/tools/uv/uv" site output
  mkdir -p -- "$stage/manager"
  cp -a -- "$REPO/src" "$stage/manager/src"
  cp -- "$REPO/pyproject.toml" "$stage/manager/pyproject.toml"
  cp -- "$REPO/requirements-tui.lock" "$stage/manager/requirements-tui.lock"
  site="$stage/manager/tui-runtime/site-packages"
  mkdir -p -- "$site"
  UV_CACHE_DIR="$stage/.artifacts/uv-cache" "$uv" pip install \
    --python "$python" --target "$site" --no-cache --require-hashes \
    --only-binary=:all: -r "$stage/manager/requirements-tui.lock" \
    >/dev/null || die "cannot provision hash-locked TUI runtime"
  output=$(WORKSPACE_MCP_TUI_SITE="$site" "$python" -I -S -c \
    'import os,sys; sys.path.insert(0,os.environ["WORKSPACE_MCP_TUI_SITE"]); import textual; print(textual.__version__)' 2>&1) \
    || die "TUI runtime validation failed"
  [[ "$output" == "8.2.8" ]] || die "TUI runtime identity mismatch: $output"
  write_manager_wrappers "$stage" "$final"
}

provision_coding_tools() {
  local stage=$1 artifact_dir=$2 final=$3
  local python="$stage/runtimes/python/bin/python3" uv="$stage/tools/uv/uv"
  local wheel_dir="$artifact_dir/coding-tools-wheels" artifact url sha expected output
  local dep_name dep_version dep_identity dep_url dep_sha
  mkdir -p -- "$wheel_dir" "$stage/runtimes/coding-tools-mcp/site-packages" "$stage/runtimes/coding-tools-mcp/bin"
  url=$(lock_get coding_tools_mcp acquisition_location)
  sha=$(lock_get coding_tools_mcp sha256)
  expected=$(lock_get coding_tools_mcp expected_observable_identity)
  artifact=$(acquire_artifact "$url" "$sha" "$wheel_dir")
  while IFS=$'\t' read -r dep_name dep_version dep_identity dep_url dep_sha; do
    [[ -n "$dep_name" ]] || continue
    acquire_artifact "$dep_url" "$dep_sha" "$wheel_dir" >/dev/null
  done < <(lock_dependencies coding_tools_mcp)
  cp -- "$REPO/requirements-coding-tools.lock" "$stage/runtimes/coding-tools-mcp/requirements.lock"
  UV_CACHE_DIR="$artifact_dir/uv-cache" "$uv" pip install \
    --python "$python" --target "$stage/runtimes/coding-tools-mcp/site-packages" \
    --no-cache --no-index --find-links "$wheel_dir" --require-hashes \
    --only-binary=:all: -r "$stage/runtimes/coding-tools-mcp/requirements.lock" \
    >/dev/null || die "cannot provision coding-tools-mcp from locked local wheels"
  output=$(CODING_SITE="$stage/runtimes/coding-tools-mcp/site-packages" "$python" -I -S -c \
    'import importlib.metadata,os,sys; sys.path.insert(0,os.environ["CODING_SITE"]); print(importlib.metadata.version("coding-tools-mcp"))' 2>&1) \
    || die "coding-tools-mcp package identity validation failed"
  [[ "$output" == "$expected" ]] || die "coding-tools-mcp identity mismatch: $output"
  cat <<EOF | atomic_file_from_stdin "$stage/runtimes/coding-tools-mcp/bin/coding-tools-mcp" 0755
#!/usr/bin/env bash
set -euo pipefail
PYTHON=$(printf '%q' "$final/runtimes/python/bin/python3")
SITE=$(printf '%q' "$final/runtimes/coding-tools-mcp/site-packages")
export WORKSPACE_MCP_CODING_TOOLS_SITE="\$SITE"
exec "\$PYTHON" -B -I -S -c 'import os,sys; sys.path.insert(0,os.environ["WORKSPACE_MCP_CODING_TOOLS_SITE"]); from coding_tools_mcp.server import main; raise SystemExit(main())' "\$@"
EOF
}

provision_tunnel_client() {
  local stage=$1 artifact_dir=$2 artifact url sha expected output
  url=$(lock_get tunnel_client acquisition_location)
  sha=$(lock_get tunnel_client sha256)
  expected=$(lock_get tunnel_client expected_observable_identity)
  artifact=$(acquire_artifact "$url" "$sha" "$artifact_dir")
  local entries
  entries=$(unzip -Z1 "$artifact" | LC_ALL=C sort)
  [[ "$entries" == $'LICENSE\ncloudflared\ncloudflared-manifest.json\ntunnel-client' ]] \
    || die "tunnel-client archive layout is unexpected"
  mkdir -p -- "$stage/runtimes/tunnel-client"
  unzip -q "$artifact" -d "$stage/runtimes/tunnel-client"
  chmod 0755 "$stage/runtimes/tunnel-client/tunnel-client" "$stage/runtimes/tunnel-client/cloudflared"
  output=$("$stage/runtimes/tunnel-client/tunnel-client" --version 2>&1) || die "tunnel-client validation failed"
  [[ "$output" == "$expected" ]] || die "tunnel-client identity mismatch: $output"
}

stage_distribution_frontends() {
  local stage=$1
  mkdir -p -- "$stage/distribution"
  cp -- "$REPO/scripts/install.sh" "$stage/distribution/install.sh"
  cp -- "$REPO/scripts/upgrade.sh" "$stage/distribution/upgrade.sh"
  cp -- "$REPO/install-versions.lock" "$stage/distribution/install-versions.lock"
  chmod 0755 "$stage/distribution/install.sh" "$stage/distribution/upgrade.sh"
}

write_install_json() {
  local stage=$1 source_digest=$2 product=$3
  local lock_sha tui_sha coding_sha
  lock_sha=$(sha256sum -- "$REPO/install-versions.lock" | awk '{print $1}')
  tui_sha=$(sha256sum -- "$REPO/requirements-tui.lock" | awk '{print $1}')
  coding_sha=$(sha256sum -- "$REPO/requirements-coding-tools.lock" | awk '{print $1}')
  "$stage/runtimes/python/bin/python3" - \
    "$stage" "$source_digest" "$product" "$REPOSITORY" "$RELEASE_REF" "$RELEASE_COMMIT" "$DIST_ID" \
    "$REPO/install-versions.lock" "$lock_sha" "$tui_sha" "$coding_sha" <<'PY'
import hashlib
import json
import os
import pathlib
import stat
import sys

stage=pathlib.Path(sys.argv[1])
source_digest, product, repository, release_ref, release_commit, distribution_id=sys.argv[2:8]
lock_path=pathlib.Path(sys.argv[8])
lock_sha,tui_sha,coding_sha=sys.argv[9:12]
lock=json.loads(lock_path.read_text(encoding="utf-8"))
manifest=[]
for base, dirs, files in os.walk(stage, topdown=True, followlinks=False):
    base_path=pathlib.Path(base)
    dirs[:] = [d for d in dirs if (base_path/d).name != ".artifacts"]
    names=list(files)+[name for name in dirs if (base_path/name).is_symlink()]
    for name in names:
        path=base_path/name
        rel=path.relative_to(stage).as_posix()
        if rel == "install.json" or rel.startswith(".artifacts/"):
            continue
        st=path.lstat(); mode=f"{stat.S_IMODE(st.st_mode):04o}"
        if stat.S_ISREG(st.st_mode):
            manifest.append({"path":rel,"type":"file","sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"mode":mode})
        elif stat.S_ISLNK(st.st_mode):
            manifest.append({"path":rel,"type":"symlink","target":os.readlink(path),"mode":mode})
        else:
            raise SystemExit(f"unsupported immutable content type: {rel}")
manifest.sort(key=lambda item:item["path"])
payload={
    "schema_version":3,
    "product_version":product,
    "repository":repository,
    "release_ref":release_ref,
    "release_commit":release_commit,
    "distribution_id":distribution_id,
    "manager_source_digest":source_digest,
    "locked_inputs":{
        "install_versions_sha256":lock_sha,
        "requirements_tui_sha256":tui_sha,
        "requirements_coding_tools_sha256":coding_sha,
    },
    "components":lock["components"],
    "manifest_scope":"all-immutable-files-except-install.json; install.json-sha256-is-anchored-by-receipt",
    "content_manifest":manifest,
}
path=stage/"install.json"
path.write_text(json.dumps(payload,sort_keys=True,indent=2)+"\n",encoding="utf-8")
os.chmod(path,0o644)
PY
  fsync_path "$stage/install.json"
}

validate_release_runtime() {
  local release=$1 output expected site python
  python="$release/runtimes/python/bin/python3"
  expected=$(lock_get python expected_observable_identity)
  output=$("$python" --version 2>&1) || die "candidate Python cannot execute"
  [[ "$output" == "$expected" ]] || die "candidate Python identity changed after finalization"
  expected=$(lock_get uv expected_observable_identity)
  output=$("$release/tools/uv/uv" --version 2>&1) || die "candidate uv cannot execute"
  [[ "$output" == "$expected" ]] || die "candidate uv identity changed after finalization"
  expected=$(lock_get tunnel_client expected_observable_identity)
  output=$("$release/runtimes/tunnel-client/tunnel-client" --version 2>&1) || die "candidate tunnel-client cannot execute"
  [[ "$output" == "$expected" ]] || die "candidate tunnel-client identity changed after finalization"
  site="$release/runtimes/coding-tools-mcp/site-packages"
  expected=$(lock_get coding_tools_mcp expected_observable_identity)
  output=$(CODING_SITE="$site" "$python" -I -S -c \
    'import importlib.metadata,os,sys; sys.path.insert(0,os.environ["CODING_SITE"]); print(importlib.metadata.version("coding-tools-mcp"))' 2>&1) \
    || die "candidate coding-tools package cannot be inspected"
  [[ "$output" == "$expected" ]] || die "candidate coding-tools identity changed after finalization"
  "$release/bin/workspace-mcp-manager" --help >/dev/null || die "candidate manager wrapper validation failed"
  "$release/bin/workspace-mcp-manager-tui" --help >/dev/null || die "candidate TUI wrapper validation failed"
  set +e
  "$release/bin/workspace-mcp-reboot" --invalid >/dev/null 2>&1
  local reboot_status=$?
  set -e
  [[ "$reboot_status" == 2 ]] || die "candidate reboot wrapper validation failed"
  "$release/bin/workspace-mcp-manager-upgrade" --help >/dev/null || die "candidate upgrade wrapper validation failed"
  "$release/runtimes/coding-tools-mcp/bin/coding-tools-mcp" --help >/dev/null \
    || die "candidate coding-tools wrapper validation failed"
}

INSTANCE_COUNT=0
INSTANCES_VALID=true
VALIDATION_ERROR=""

validate_declarations_with_python() {
  local python=$1 source=$2
  local registry="$ACCOUNT_HOME/.config/workspace-mcp-manager/instances" declaration output
  INSTANCE_COUNT=0; INSTANCES_VALID=true; VALIDATION_ERROR=""
  [[ -d "$registry" ]] || return 0
  while IFS= read -r -d '' declaration; do
    INSTANCE_COUNT=$((INSTANCE_COUNT + 1))
    if ! output=$(PYTHONDONTWRITEBYTECODE=1 MANAGER_SRC="$source" "$python" -I -S -c \
        'import os,sys; sys.path.insert(0,os.environ["MANAGER_SRC"]); from workspace_mcp_manager.domain import DesiredInstance; DesiredInstance.from_json(sys.stdin.read())' \
        <"$declaration" 2>&1); then
      INSTANCES_VALID=false
      VALIDATION_ERROR="cannot validate declaration $declaration: ${output: -1600}"
      return 1
    fi
  done < <(find "$registry" -maxdepth 1 -type f -name '*.json' -print0 | LC_ALL=C sort -z)
}

validate_declarations_source() {
  validate_declarations_with_python "$BOOTSTRAP_PYTHON" "$REPO/src"
}

validate_declarations_release() {
  local release=$1
  validate_declarations_with_python "$release/runtimes/python/bin/python3" "$release/manager/src"
}

journal_write() {
  local phase=$1
  local journal="$STATE_ROOT/distribution-transaction.json" temporary
  temporary="$STATE_ROOT/.distribution-transaction.$TRANSACTION_ID.tmp"
  "$BOOTSTRAP_PYTHON" - "$temporary" "$TRANSACTION_ID" "$OPERATION" "$phase" \
    "$PREVIOUS_CLASSIFICATION" "$PREVIOUS_CURRENT" "$PREVIOUS_RECEIPT_ID" "$CREATED_ENTRYPOINTS" \
    "$REPOSITORY" "$RELEASE_REF" "$RELEASE_COMMIT" "$DIST_ID" "$FINAL_RELEASE" "$STAGE_PATH" \
    "$CANDIDATE_PREEXISTING" "$RECEIPT_TEMP" <<'PY'
import json,os,sys
(
 path, transaction_id, operation, phase,
 previous_classification, previous_current, previous_receipt, created,
 repository, release_ref, release_commit, distribution_id, release_path, staging_path,
 preexisting, receipt_temp,
)=sys.argv[1:17]
payload={
 "schema_version":1,
 "transaction_id":transaction_id,
 "operation":operation,
 "phase":phase,
 "previous":{
   "classification":previous_classification,
   "current_target":previous_current or None,
   "receipt_identity":previous_receipt or None,
   "transaction_created_entrypoints":[x for x in created.split(",") if x],
 },
 "candidate":{
   "repository":repository,
   "release_ref":release_ref,
   "release_commit":release_commit,
   "distribution_id":distribution_id,
   "release_path":release_path,
   "staging_path":staging_path,
   "preexisting":preexisting == "true",
   "receipt_temp":receipt_temp or None,
 },
}
with open(path,"w",encoding="utf-8") as handle:
 json.dump(payload,handle,sort_keys=True,indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
os.chmod(path,0o600)
PY
  mv -Tf -- "$temporary" "$journal"
  fsync_path "$STATE_ROOT"
}

journal_validate() {
  local journal="$STATE_ROOT/distribution-transaction.json"
  "$BOOTSTRAP_PYTHON" - "$journal" <<'PY' >/dev/null 2>&1
import json,re,sys
try: data=json.load(open(sys.argv[1],encoding="utf-8"))
except Exception: raise SystemExit(1)
if set(data)!={"schema_version","transaction_id","operation","phase","previous","candidate"} or data["schema_version"]!=1: raise SystemExit(1)
if set(data["previous"])!={"classification","current_target","receipt_identity","transaction_created_entrypoints"}: raise SystemExit(1)
if set(data["candidate"])!={"repository","release_ref","release_commit","distribution_id","release_path","staging_path","preexisting","receipt_temp"}: raise SystemExit(1)
if not re.fullmatch(r"[0-9a-f]{40}",data["candidate"]["release_commit"]): raise SystemExit(1)
if data["candidate"]["distribution_id"]!=f"dist-{data['candidate']['release_commit']}": raise SystemExit(1)
if data["previous"]["classification"] not in {"absent","recognized_existing","standalone_distribution"}: raise SystemExit(1)
if not isinstance(data["previous"]["transaction_created_entrypoints"],list): raise SystemExit(1)
PY
}

journal_get() {
  local key=$1 journal="$STATE_ROOT/distribution-transaction.json"
  "$BOOTSTRAP_PYTHON" - "$journal" "$key" <<'PY'
import json,sys
data=json.load(open(sys.argv[1],encoding="utf-8"))
value=data
for part in sys.argv[2].split("."):
 value=value[part]
if value is None: raise SystemExit(0)
if isinstance(value,bool): print("true" if value else "false")
elif isinstance(value,list): print("\n".join(str(x) for x in value))
elif isinstance(value,(str,int)): print(value)
else: raise SystemExit(1)
PY
}

resolve_journal() {
  local journal="$STATE_ROOT/distribution-transaction.json"
  rm -f -- "$journal"
  fsync_path "$STATE_ROOT"
}

prepare_receipt() {
  local release=$1 temp=$2
  "$BOOTSTRAP_PYTHON" - "$release" "$temp" <<'PY'
import datetime,hashlib,json,os,pathlib,sys
release=pathlib.Path(sys.argv[1]); path=pathlib.Path(sys.argv[2])
install_path=release/"install.json"
install=json.loads(install_path.read_text(encoding="utf-8"))
payload={
 "schema_version":1,
 "distribution_id":install["distribution_id"],
 "product_version":install["product_version"],
 "repository":install["repository"],
 "release_ref":install["release_ref"],
 "release_commit":install["release_commit"],
 "release_path":str(release),
 "install_json_sha256":hashlib.sha256(install_path.read_bytes()).hexdigest(),
 "verified_at":datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),
}
with open(path,"w",encoding="utf-8") as handle:
 json.dump(payload,handle,sort_keys=True,indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
os.chmod(path,0o600)
PY
}

publish_receipt() {
  local temp=$1 receipt="$STATE_ROOT/distribution.json"
  [[ -f "$temp" && ! -L "$temp" ]] || die "prepared distribution receipt is unavailable"
  mv -Tf -- "$temp" "$receipt"
  fsync_path "$STATE_ROOT"
}

repair_receipt_for_release() {
  local release=$1 temp="$STATE_ROOT/.distribution-recovery.$$.tmp"
  prepare_receipt "$release" "$temp"
  publish_receipt "$temp"
}

remove_transaction_entrypoints() {
  local names=$1 name path expected
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    case "$name" in
      workspace-mcp-manager|workspace-mcp-manager-tui|workspace-mcp-reboot|workspace-mcp-manager-upgrade) ;;
      *) die "journal contains unsafe entrypoint name" ;;
    esac
    path="$PREFIX/bin/$name"; expected=$(expected_entrypoint_target "$name")
    if [[ -L "$path" && "$(readlink -- "$path")" == "$expected" ]]; then
      rm -f -- "$path"
    elif [[ -e "$path" || -L "$path" ]]; then
      die "cannot remove changed transaction-created entrypoint: $path"
    fi
  done <<<"$names"
  [[ ! -d "$PREFIX/bin" ]] || fsync_path "$PREFIX/bin"
}

recover_transaction() {
  local journal="$STATE_ROOT/distribution-transaction.json"
  [[ -f "$journal" ]] || return 0
  RECOVERING=true
  journal_validate || die "distribution transaction journal is malformed"
  local previous_class previous_current candidate_commit candidate_id candidate_release staging preexisting receipt_temp created actual candidate_target current_temp
  previous_class=$(journal_get previous.classification)
  previous_current=$(journal_get previous.current_target || true)
  candidate_commit=$(journal_get candidate.release_commit)
  candidate_id=$(journal_get candidate.distribution_id)
  candidate_release=$(journal_get candidate.release_path)
  staging=$(journal_get candidate.staging_path)
  preexisting=$(journal_get candidate.preexisting)
  receipt_temp=$(journal_get candidate.receipt_temp || true)
  created=$(journal_get previous.transaction_created_entrypoints || true)
  actual=$(current_target || true)
  candidate_target="releases/$candidate_id"
  current_temp="$TOOLKIT_ROOT/.current.$(journal_get transaction_id).tmp"

  [[ "$candidate_release" == "$TOOLKIT_ROOT/releases/$candidate_id" ]] || die "journal candidate release path is outside authority"
  [[ "$staging" == "$TOOLKIT_ROOT/releases/.stage-$candidate_id-"* ]] || die "journal staging path is outside authority"
  if [[ -n "$receipt_temp" ]]; then
    [[ "$receipt_temp" == "$STATE_ROOT/.distribution.json."*".tmp" ]] || die "journal receipt temp path is outside authority"
  fi

  if [[ "$actual" == "$candidate_target" ]]; then
    release_validate "$candidate_release" "$candidate_commit" || die "committed candidate is invalid during recovery"
    ensure_stable_entrypoints_postcommit
    if ! receipt_consistent_with_release "$candidate_release"; then
      repair_receipt_for_release "$candidate_release"
    fi
    [[ -z "$receipt_temp" ]] || rm -f -- "$receipt_temp"
    [[ ! -e "$staging" && ! -L "$staging" ]] || { [[ -d "$staging" && ! -L "$staging" ]] || die "unsafe staging state during recovery"; rm -rf -- "$staging"; }
    rm -f -- "$current_temp"
    resolve_journal
    RECOVERY_RESULT="committed_candidate"
    RECOVERING=false
    return 0
  fi

  if [[ "$actual" == "$previous_current" ]]; then
    if [[ -e "$staging" || -L "$staging" ]]; then
      [[ -d "$staging" && ! -L "$staging" ]] || die "unsafe staging state during rollback"
      rm -rf -- "$staging"
    fi
    if [[ "$preexisting" == false && ( -e "$candidate_release" || -L "$candidate_release" ) ]]; then
      [[ -d "$candidate_release" && ! -L "$candidate_release" ]] || die "unsafe candidate release state during rollback"
      release_validate "$candidate_release" "$candidate_commit" || die "transaction-created finalized candidate is corrupt"
      rm -rf -- "$candidate_release"
    fi
    [[ -z "$receipt_temp" ]] || rm -f -- "$receipt_temp"
    rm -f -- "$current_temp"
    remove_transaction_entrypoints "$created"
    case "$previous_class" in
      absent)
        [[ -z "$actual" ]] || die "rollback did not restore absent current"
        if [[ -d "$TOOLKIT_ROOT/releases" ]]; then rmdir "$TOOLKIT_ROOT/releases" 2>/dev/null || true; fi
        if [[ -d "$TOOLKIT_ROOT" ]]; then rmdir "$TOOLKIT_ROOT" 2>/dev/null || true; fi
        ;;
      recognized_existing)
        [[ -n "$previous_current" && -L "$TOOLKIT_ROOT/current" ]] || die "recognized predecessor current was not restored"
        predecessor_release_valid "$TOOLKIT_ROOT/$previous_current" || die "recognized predecessor is invalid after rollback"
        ;;
      standalone_distribution)
        [[ -n "$previous_current" && -L "$TOOLKIT_ROOT/current" ]] || die "previous standalone current was not restored"
        release_validate "$TOOLKIT_ROOT/$previous_current" || die "previous standalone distribution is invalid after rollback"
        ;;
      *) die "unsupported previous classification during recovery" ;;
    esac
    resolve_journal
    RECOVERY_RESULT="previous_distribution"
    RECOVERING=false
    return 0
  fi

  die "transaction recovery found ambiguous current authority: ${actual:-<absent>}"
}

ensure_stable_entrypoints_postcommit() {
  local name path expected
  for name in workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot workspace-mcp-manager-upgrade; do
    path="$PREFIX/bin/$name"; expected=$(expected_entrypoint_target "$name")
    if entrypoint_exact "$name"; then
      continue
    fi
    if [[ -e "$path" || -L "$path" ]]; then
      die "foreign/changed stable entrypoint blocks recovery: $path"
    fi
    atomic_symlink "$path" "$expected"
  done
}

prepare_stable_entrypoints_precommit() {
  local name path expected
  for name in workspace-mcp-manager workspace-mcp-manager-tui workspace-mcp-reboot workspace-mcp-manager-upgrade; do
    path="$PREFIX/bin/$name"; expected=$(expected_entrypoint_target "$name")
    if entrypoint_exact "$name"; then
      continue
    fi
    if [[ -e "$path" || -L "$path" ]]; then
      die "foreign stable entrypoint collision: $path"
    fi
    atomic_symlink "$path" "$expected"
    if [[ -n "$CREATED_ENTRYPOINTS" ]]; then CREATED_ENTRYPOINTS+=","; fi
    CREATED_ENTRYPOINTS+="$name"
    journal_write "stable_entrypoints_prepared"
  done
}

maybe_failpoint() {
  local point=$1
  [[ -n "$QUALIFICATION_FAILPOINT" && "$QUALIFICATION_FAILPOINT" == "$point" ]] || return 0
  printf '%s installer: qualification failpoint: %s\n' "$PRODUCT_NAME" "$point" >&2
  kill -KILL $$
}

construct_candidate() {
  local source_digest product artifact_dir
  if [[ "$CANDIDATE_PREEXISTING" == true ]]; then
    release_validate "$FINAL_RELEASE" "$RELEASE_COMMIT" || die "pre-existing immutable release is corrupt"
    return 0
  fi
  mkdir -p -- "$TOOLKIT_ROOT/releases"
  [[ ! -e "$STAGE_PATH" && ! -L "$STAGE_PATH" ]] || die "candidate staging collision"
  mkdir -m 0700 -- "$STAGE_PATH"
  journal_write "candidate_construction"
  artifact_dir="$STAGE_PATH/.artifacts"
  mkdir -m 0700 -- "$artifact_dir"
  provision_python "$STAGE_PATH" "$artifact_dir"
  maybe_failpoint during_candidate_construction
  provision_uv "$STAGE_PATH" "$artifact_dir"
  provision_manager "$STAGE_PATH" "$FINAL_RELEASE"
  provision_coding_tools "$STAGE_PATH" "$artifact_dir" "$FINAL_RELEASE"
  provision_tunnel_client "$STAGE_PATH" "$artifact_dir"
  stage_distribution_frontends "$STAGE_PATH"
  rm -rf -- "$artifact_dir"
  source_digest=$(manager_source_digest)
  product=$(product_version)
  write_install_json "$STAGE_PATH" "$source_digest" "$product"
  fsync_tree "$STAGE_PATH" "$STAGE_PATH/runtimes/python/bin/python3"
  mv -T -- "$STAGE_PATH" "$FINAL_RELEASE"
  fsync_path "$TOOLKIT_ROOT/releases"
  journal_write "candidate_finalized"
  maybe_failpoint after_candidate_finalization
}

commit_current() {
  local temp="$TOOLKIT_ROOT/.current.$TRANSACTION_ID.tmp"
  rm -f -- "$temp"
  ln -s -- "releases/$DIST_ID" "$temp"
  maybe_failpoint during_current_commit
  mv -Tf -- "$temp" "$TOOLKIT_ROOT/current"
  fsync_path "$TOOLKIT_ROOT"
}

check_active_authority() {
  local classification=$1 target release
  case "$classification" in
    absent) return 0 ;;
    recognized_existing)
      target=$(current_target); predecessor_release_valid "$TOOLKIT_ROOT/$target" || return 1; stable_entrypoints_predecessor_valid || return 1
      ;;
    standalone_distribution)
      target=$(current_target); release="$TOOLKIT_ROOT/$target"; release_validate "$release" || return 1; stable_entrypoints_standalone_valid || return 1
      ;;
    *) return 1 ;;
  esac
}

check_report() {
  local classification=$1 recovery_required=$2 candidate_known=$3 candidate_id=$4 installed_id=$5 install_required=$6 upgrade_required=$7 receipt_ok=$8 instances_valid=$9
  "$BOOTSTRAP_PYTHON" - "$classification" "$recovery_required" "$candidate_known" "$candidate_id" "$installed_id" "$install_required" "$upgrade_required" "$receipt_ok" "$INSTANCE_COUNT" "$instances_valid" <<'PY'
import json,sys
classification,recovery,candidate_known,candidate_id,installed_id,install_required,upgrade_required,receipt_ok,count,instances_valid=sys.argv[1:11]
def b(v): return v=="true"
payload={
 "ok": classification != "foreign_or_ambiguous" and b(instances_valid),
 "mode":"check",
 "ownership":classification,
 "recovery_required":b(recovery),
 "candidate_distribution_id":candidate_id or None if b(candidate_known) else None,
 "installed_distribution_id":installed_id or None,
 "install_required":b(install_required),
 "upgrade_required": None if upgrade_required=="unknown" else b(upgrade_required),
 "receipt_consistent": None if receipt_ok=="unknown" else b(receipt_ok),
 "instance_count":int(count),
 "instances_valid":b(instances_valid),
}
print(json.dumps(payload,sort_keys=True,separators=(",",":")))
PY
}

run_check() {
  local classification recovery_required=false candidate_known=false candidate_id="" installed_id="" install_required=false upgrade_required=unknown receipt_ok=unknown target release
  [[ -f "$STATE_ROOT/distribution-transaction.json" ]] && recovery_required=true
  classification=$(classify_ownership_for_check)
  if [[ "$classification" == standalone_distribution ]]; then
    target=$(current_target); release="$TOOLKIT_ROOT/$target"
    installed_id=${target#releases/}
    if receipt_consistent_with_release "$release"; then receipt_ok=true; else receipt_ok=false; fi
    validate_declarations_release "$release" || true
  elif [[ "$classification" == recognized_existing ]]; then
    validate_declarations_source || true
  elif [[ "$classification" == absent ]]; then
    validate_declarations_source || true
  else
    INSTANCE_COUNT=0; INSTANCES_VALID=false
  fi

  if [[ -f "$REPO/install-versions.lock" && -f "$REPO/pyproject.toml" && -d "$REPO/src/workspace_mcp_manager" ]]; then
    validate_candidate_source
    strict_lock_validate "$REPO/install-versions.lock"
    validate_checkout_provenance
    candidate_known=true
    candidate_id="dist-$RELEASE_COMMIT"
    case "$classification" in
      absent|recognized_existing) install_required=true; upgrade_required=false ;;
      standalone_distribution)
        if [[ "$installed_id" == "$candidate_id" ]]; then install_required=false; upgrade_required=false
        else install_required=true; upgrade_required=true; fi
        ;;
      *) install_required=false; upgrade_required=unknown ;;
    esac
  fi
  check_report "$classification" "$recovery_required" "$candidate_known" "$candidate_id" "$installed_id" "$install_required" "$upgrade_required" "$receipt_ok" "$INSTANCES_VALID"
  [[ "$classification" != foreign_or_ambiguous && "$INSTANCES_VALID" == true ]]
}

success_report() {
  local action=$1 release=$2
  "$BOOTSTRAP_PYTHON" - "$action" "$release/install.json" "$STATE_ROOT/distribution.json" <<'PY'
import json,sys
action=sys.argv[1]
install=json.load(open(sys.argv[2],encoding="utf-8"))
payload={
 "ok":True,
 "action":action,
 "distribution_id":install["distribution_id"],
 "release_commit":install["release_commit"],
 "product_version":install["product_version"],
 "receipt":sys.argv[3],
}
print(json.dumps(payload,sort_keys=True,separators=(",",":")))
PY
}

acquire_mutation_lock() {
  mkdir -p -- "$STATE_ROOT"
  chmod 0700 "$STATE_ROOT"
  exec 9>"$STATE_ROOT/distribution.lock"
  if ! flock -w "$LOCK_TIMEOUT_SECONDS" 9; then
    die "another distribution mutation holds the account-level lock"
  fi
  LOCK_HELD=true
}

release_mutation_lock() {
  if $LOCK_HELD; then
    flock -u 9 || true
    exec 9>&-
    LOCK_HELD=false
  fi
}

on_exit() {
  local status=$?
  trap - EXIT
  if ((status != 0)) && $IN_MUTATION && ! $RECOVERING && [[ -f "$STATE_ROOT/distribution-transaction.json" ]]; then
    set +e
    RECOVERING=true
    recover_transaction
    local recovery_status=$?
    if ((recovery_status != 0)); then
      printf '%s installer: rollback/recovery also failed (status %d)\n' "$PRODUCT_NAME" "$recovery_status" >&2
    fi
    set -e
  fi
  release_mutation_lock
  exit "$status"
}
trap on_exit EXIT

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ -f "$SCRIPT_DIR/../install.json" && -d "$SCRIPT_DIR/../manager" ]]; then
  INSTALLED_CONTEXT=true
  REPO=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
else
  INSTALLED_CONTEXT=false
  REPO=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
fi
ACCOUNT_HOME=${HOME:-}
PREFIX=""
REPOSITORY="$PRODUCTION_REPOSITORY"
RELEASE_REF="$PRODUCTION_REF"
RELEASE_COMMIT=""
UPGRADE=false
RECOVER_ONLY=false
CHECK=false
CONFIGURE=false
QUALIFICATION=false
QUALIFICATION_ARTIFACT_DIR=""
QUALIFICATION_FAILPOINT=""
LOCK_HELD=false
IN_MUTATION=false
RECOVERING=false
RECOVERY_RESULT="none"

while (($#)); do
  case "$1" in
    --repo) (($#>=2)) || die "--repo requires a path"; REPO=$2; shift 2 ;;
    --account-home) (($#>=2)) || die "--account-home requires a path"; ACCOUNT_HOME=$2; shift 2 ;;
    --prefix) (($#>=2)) || die "--prefix requires a path"; PREFIX=$2; shift 2 ;;
    --repository) (($#>=2)) || die "--repository requires a URL"; REPOSITORY=$2; shift 2 ;;
    --release-ref) (($#>=2)) || die "--release-ref requires a ref"; RELEASE_REF=$2; shift 2 ;;
    --release-commit) (($#>=2)) || die "--release-commit requires a SHA"; RELEASE_COMMIT=$2; shift 2 ;;
    --upgrade) UPGRADE=true; shift ;;
    --recover-only) RECOVER_ONLY=true; shift ;;
    --check) CHECK=true; shift ;;
    --configure) CONFIGURE=true; shift ;;
    --qualification) QUALIFICATION=true; shift ;;
    --qualification-artifact-dir) (($#>=2)) || die "--qualification-artifact-dir requires a path"; QUALIFICATION_ARTIFACT_DIR=$2; shift 2 ;;
    --qualification-failpoint) (($#>=2)) || die "--qualification-failpoint requires a name"; QUALIFICATION_FAILPOINT=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n "$ACCOUNT_HOME" ]] || die "account home is unavailable"
if $CHECK && $RECOVER_ONLY; then die "--check and --recover-only are mutually exclusive"; fi
if $CHECK && $CONFIGURE; then die "--check cannot be combined with --configure"; fi
if $RECOVER_ONLY && $CONFIGURE; then die "--recover-only cannot be combined with --configure"; fi
if [[ -n "$QUALIFICATION_ARTIFACT_DIR" || -n "$QUALIFICATION_FAILPOINT" ]]; then
  $QUALIFICATION || die "qualification artifact/failpoint options require --qualification"
fi

for command in realpath python3; do require_command "$command"; done
bootstrap_python_check
REPO=$(absolute_path "$REPO")
ACCOUNT_HOME=$(absolute_path "$ACCOUNT_HOME")
if [[ -z "$PREFIX" ]]; then PREFIX="$ACCOUNT_HOME/.local"; fi
PREFIX=$(absolute_path "$PREFIX")
if [[ -n "$QUALIFICATION_ARTIFACT_DIR" ]]; then QUALIFICATION_ARTIFACT_DIR=$(absolute_path "$QUALIFICATION_ARTIFACT_DIR"); fi
TOOLKIT_ROOT="$PREFIX/lib/workspace-mcp-manager"
STATE_ROOT="$ACCOUNT_HOME/.local/state/workspace-mcp-manager"

if $CHECK; then
  preflight_read_only
  run_check
  exit $?
fi

for command in flock sha256sum stat find sort grep awk cat cp mv ln readlink chmod dirname date; do require_command "$command"; done
acquire_mutation_lock
recover_transaction

if $RECOVER_ONLY; then
  classification=$(classify_ownership)
  if [[ "$classification" == standalone_distribution ]]; then
    target=$(current_target); release="$TOOLKIT_ROOT/$target"
    release_validate "$release" || die "active standalone distribution is invalid"
    stable_entrypoints_standalone_valid || ensure_stable_entrypoints_postcommit
    if ! receipt_consistent_with_release "$release"; then repair_receipt_for_release "$release"; fi
  elif [[ "$classification" == recognized_existing ]]; then
    check_active_authority "$classification" || die "recognized predecessor is invalid"
  elif [[ "$classification" != absent ]]; then
    die "cannot recover foreign or ambiguous distribution ownership"
  fi
  release_mutation_lock
  "$BOOTSTRAP_PYTHON" - "$RECOVERY_RESULT" "$classification" <<'PY'
import json,sys
print(json.dumps({"ok":True,"action":"recover","recovery_result":sys.argv[1],"ownership":sys.argv[2]},sort_keys=True,separators=(",",":")))
PY
  exit 0
fi

preflight_read_only
validate_candidate_source
strict_lock_validate "$REPO/install-versions.lock"
validate_checkout_provenance
validate_declarations_source || die "existing declaration validation failed: $VALIDATION_ERROR"

DIST_ID="dist-$RELEASE_COMMIT"
FINAL_RELEASE="$TOOLKIT_ROOT/releases/$DIST_ID"
PREVIOUS_CLASSIFICATION=$(classify_ownership)
[[ "$PREVIOUS_CLASSIFICATION" != foreign_or_ambiguous ]] || die "foreign or ambiguous distribution ownership blocks installation"
PREVIOUS_CURRENT=$(current_target || true)
PREVIOUS_RECEIPT_ID=$(receipt_distribution_id || true)

if [[ "$PREVIOUS_CLASSIFICATION" == standalone_distribution && "$PREVIOUS_CURRENT" == "releases/$DIST_ID" ]]; then
  release_validate "$FINAL_RELEASE" "$RELEASE_COMMIT" || die "current immutable release is corrupt"
  validate_release_runtime "$FINAL_RELEASE"
  validate_declarations_release "$FINAL_RELEASE" || die "current release cannot parse existing declarations: $VALIDATION_ERROR"
  if receipt_consistent_with_release "$FINAL_RELEASE" && stable_entrypoints_standalone_valid; then
    release_mutation_lock
    success_report "noop" "$FINAL_RELEASE"
    if $CONFIGURE; then exec "$PREFIX/bin/workspace-mcp-manager-tui"; fi
    exit 0
  fi
fi

if [[ "$PREVIOUS_CLASSIFICATION" == standalone_distribution && "$PREVIOUS_CURRENT" != "releases/$DIST_ID" ]] && ! $UPGRADE; then
  die "a different standalone distribution is installed; use workspace-mcp-manager-upgrade"
fi

CANDIDATE_PREEXISTING=false
if [[ -e "$FINAL_RELEASE" || -L "$FINAL_RELEASE" ]]; then
  [[ -d "$FINAL_RELEASE" && ! -L "$FINAL_RELEASE" ]] || die "immutable release path collision: $FINAL_RELEASE"
  release_validate "$FINAL_RELEASE" "$RELEASE_COMMIT" || die "pre-existing immutable release is incomplete/corrupt"
  CANDIDATE_PREEXISTING=true
fi

TRANSACTION_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
OPERATION="install"
[[ "$PREVIOUS_CLASSIFICATION" == standalone_distribution ]] && OPERATION="upgrade"
[[ "$PREVIOUS_CLASSIFICATION" == recognized_existing ]] && OPERATION="migration"
STAGE_PATH="$TOOLKIT_ROOT/releases/.stage-$DIST_ID-$TRANSACTION_ID"
RECEIPT_TEMP="$STATE_ROOT/.distribution.json.$TRANSACTION_ID.tmp"
CREATED_ENTRYPOINTS=""
IN_MUTATION=true
journal_write "journal_persisted"
maybe_failpoint after_journal_persistence

construct_candidate
release_validate "$FINAL_RELEASE" "$RELEASE_COMMIT" || die "finalized candidate manifest validation failed"
validate_release_runtime "$FINAL_RELEASE"
validate_declarations_release "$FINAL_RELEASE" || die "candidate release cannot parse existing declarations: $VALIDATION_ERROR"

prepare_receipt "$FINAL_RELEASE" "$RECEIPT_TEMP"
journal_write "receipt_prepared"
maybe_failpoint after_receipt_preparation

prepare_stable_entrypoints_precommit
journal_write "ready_to_commit"
commit_current
journal_write "current_committed"
maybe_failpoint after_current_commit

[[ "$(current_target)" == "releases/$DIST_ID" ]] || die "current commit verification failed"
release_validate "$FINAL_RELEASE" "$RELEASE_COMMIT" || die "committed candidate content validation failed"
stable_entrypoints_standalone_valid || die "stable entrypoint projection is invalid after commit"
maybe_failpoint during_receipt_publication
publish_receipt "$RECEIPT_TEMP"
journal_write "receipt_published"
maybe_failpoint after_receipt_publication
receipt_consistent_with_release "$FINAL_RELEASE" || die "published receipt does not match committed release"
maybe_failpoint before_journal_resolution
resolve_journal
IN_MUTATION=false
release_mutation_lock

success_report "$OPERATION" "$FINAL_RELEASE"
if $CONFIGURE; then
  exec "$PREFIX/bin/workspace-mcp-manager-tui"
fi
