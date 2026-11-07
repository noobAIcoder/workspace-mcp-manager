#!/usr/bin/env bash
# Shared interpreter selection. This file never mutates the distribution.
select_bootstrap_python() {
  local minimum=$1 candidate
  for candidate in "${BASH_SOURCE[0]%/*}/../runtimes/python/bin/python3" \
      "${WORKSPACE_MCP_BOOTSTRAP_PYTHON:-}" \
      python3 python3.12 python3.11 python3.10; do
    [[ -n "$candidate" ]] || continue
    candidate=$(type -P -- "$candidate") || continue
    if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,int(sys.argv[1])))' "$minimum" >/dev/null 2>&1; then
      WORKSPACE_MCP_BOOTSTRAP_PYTHON=$candidate
      export WORKSPACE_MCP_BOOTSTRAP_PYTHON
      return 0
    fi
  done
  return 1
}

acquire_bootstrap_python() {
  local checkout=$1 temporary=$2 allow_download=${3:-true} lock arch url digest expected
  arch=$(uname -m)
  lock="$checkout/install-versions.lock"
  [[ "$arch" != aarch64 ]] || lock="$checkout/install-versions.aarch64.lock"
  select_bootstrap_python 10 || die 'Python >=3.10 is required to validate downloads; prepare prerequisites first'
  [[ -f "$checkout/scripts/lock_validate.py" && ! -L "$checkout/scripts/lock_validate.py" ]] || die 'strict lock helper is missing/not regular'
  "$WORKSPACE_MCP_BOOTSTRAP_PYTHON" "$checkout/scripts/lock_validate.py" "$lock" "$checkout/requirements-coding-tools.lock" "$arch" || die 'strict lock validation failed'
  select_bootstrap_python 11 && return 0
  [[ "$allow_download" == true ]] || die 'Python >=3.11 is required for local candidate validation; local checks never download runtimes'
  for candidate in curl sha256sum tar; do require_command "$candidate"; done
  mapfile -t runtime_fields < <("$WORKSPACE_MCP_BOOTSTRAP_PYTHON" - "$lock" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))['components']['python']
for key in ('acquisition_location','sha256','expected_observable_identity'): print(p[key])
PY
  )
  [[ ${#runtime_fields[@]} == 3 ]] || die 'invalid Python lock fields'
  url=${runtime_fields[0]}; digest=${runtime_fields[1]}; expected=${runtime_fields[2]}
  mkdir -p -- "$temporary/bootstrap-runtime"
  curl --fail --location --proto '=https' --proto-redir '=https' --output "$temporary/python.tar.gz" "$url" || die 'Python acquisition failed'
  printf '%s  %s\n' "$digest" "$temporary/python.tar.gz" | sha256sum --check --status || die 'Python runtime digest mismatch'
  "$WORKSPACE_MCP_BOOTSTRAP_PYTHON" - "$temporary/python.tar.gz" <<'PY' || die 'unsafe Python archive'
import pathlib,sys,tarfile
with tarfile.open(sys.argv[1]) as archive:
 for entry in archive:
  p=pathlib.PurePosixPath(entry.name)
  if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='python': raise SystemExit(1)
  if entry.isdev() or entry.isfifo(): raise SystemExit(1)
  if entry.issym() or entry.islnk():
   target=pathlib.PurePosixPath(entry.linkname)
   if target.is_absolute() or '..' in target.parts: raise SystemExit(1)
PY
  tar -xzf "$temporary/python.tar.gz" -C "$temporary/bootstrap-runtime" || die 'Python extraction failed'
  candidate="$temporary/bootstrap-runtime/python/bin/python3"
  [[ "$("$candidate" --version)" == "$expected" ]] || die 'Python runtime identity mismatch'
  WORKSPACE_MCP_BOOTSTRAP_PYTHON=$candidate
  export WORKSPACE_MCP_BOOTSTRAP_PYTHON
}
