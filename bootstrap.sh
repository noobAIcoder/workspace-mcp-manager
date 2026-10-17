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
  --interactive          Open the numbered setup/maintenance menu
  --non-interactive      Install/update without the menu (required without a TTY)
  --check                Fetch release and print a read-only candidate check
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
INTERACTIVE=auto
CHECK_REMOTE=false
REVIEW=false
QUALIFICATION=false
QUALIFICATION_ARTIFACT_DIR=""
QUALIFICATION_FAILPOINT=""

while (($#)); do
  case "$1" in
    --interactive) INTERACTIVE=true; shift ;;
    --non-interactive) INTERACTIVE=false; shift ;;
    --check) CHECK_REMOTE=true; shift ;;
    --review) REVIEW=true; shift ;;
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

setup_backend() {
  local base anchor
  base=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
  if [[ -f "$base/install.sh" ]]; then printf '%s' "$base/install.sh"; return; fi
  if [[ -f "$base/scripts/install.sh" ]]; then printf '%s' "$base/scripts/install.sh"; return; fi
  if [[ -e "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/maintenance-transaction.json" || -L "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/maintenance-transaction.json" ]]; then
    if [[ -f "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/setup-recovery/install.sh" ]]; then
      printf '%s' "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/setup-recovery/install.sh"
      return
    fi
  fi
  anchor="$PREFIX/bin/workspace-mcp-manager-setup"
  if [[ -L "$anchor" ]]; then
    local target
    target=$(readlink -- "$anchor")
    if [[ "$target" =~ ^\.\./lib/workspace-mcp-manager/releases/dist-[0-9a-f]{40}/bin/workspace-mcp-manager-setup$ ]]; then
      printf '%s' "$(dirname -- "$(realpath -- "$anchor")")/../distribution/install.sh"
      return
    fi
  fi
  if [[ -f "$PREFIX/lib/workspace-mcp-manager/current/distribution/install.sh" ]]; then
    printf '%s' "$PREFIX/lib/workspace-mcp-manager/current/distribution/install.sh"
  elif [[ -f "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/setup-recovery/install.sh" ]]; then
    printf '%s' "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/setup-recovery/install.sh"
  fi
}

json_value() {
  python3 -c 'import json,sys; value=json.load(sys.stdin); print(value.get(sys.argv[1],""))' "$1"
}

installation_fingerprint() {
  python3 - "$ACCOUNT_HOME" "$PREFIX" <<'PYFINGERPRINT'
import hashlib,json,pathlib,sys,os
home,prefix=[pathlib.Path(p).resolve() for p in sys.argv[1:]]
registry=home/'.config/workspace-mcp-manager/instances'
current=prefix/'lib/workspace-mcp-manager/current'
state=home/'.local/state/workspace-mcp-manager'
files=sorted(registry.glob('*.json'))
files.extend(p for p in [state/'distribution.json', state/'distribution-transaction.json', state/'maintenance-transaction.json'] if p.exists() or p.is_symlink())
entries={}
for name in ['workspace-mcp-manager','workspace-mcp-manager-tui','workspace-mcp-reboot','workspace-mcp-manager-upgrade','workspace-mcp-manager-setup']:
 p=prefix/'bin'/name
 if p.is_symlink(): entries[name]={'symlink':os.readlink(p)}
 elif p.exists(): entries[name]={'file':hashlib.sha256(p.read_bytes()).hexdigest()}
payload={'current':os.readlink(current) if current.is_symlink() else None,
         'files':[(str(p),hashlib.sha256(p.read_bytes()).hexdigest()) for p in files], 'entrypoints':entries}
print(hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest())
PYFINGERPRINT
}

setup_result() {
  python3 -c '
import json,sys
p=json.load(sys.stdin)
if not p.get("ok",True): print("Blocked: "+str(p.get("error", "operation failed")))
else:
 print("Result: "+str(p.get("result") or p.get("recovery_result") or p.get("action") or p.get("ownership") or "complete"))
 if p.get("current"): print("Active release: "+p["current"])
 if p.get("backup"): print("Preserved data: "+p["backup"])
 if p.get("completed_instances"): print("Removed deployments: "+", ".join(p["completed_instances"]))
 if p.get("remaining_instances"): print("Deployments still requiring removal: "+", ".join(p["remaining_instances"]))
'
}

setup_summary() {
  python3 -c '
import json,sys
s=json.load(sys.stdin)
print("Manager installation: "+str(s.get("current") or s.get("installed_distribution_id") or s.get("ownership", "unknown")))
print("Instances: "+str(len(s["instances"]) if "instances" in s else s.get("instance_count", "unknown")))
print("Recovery: "+("Required" if s.get("recovery_required") else "Not required"))
for r in s.get("releases", []):
 if r["id"]==s.get("current"):
  print("Runtime versions: "+", ".join(k+" "+v for k,v in r.get("components",{}).items()))
'
}

setup_preview_summary() {
  python3 -c '
import json,sys
p=json.load(sys.stdin)
s=p.get("status", {})
print("Action: "+str(p.get("action", "install/update/migrate")))
print("Current: "+str(s.get("current") or p.get("installed_distribution_id") or "none"))
print("Target: "+str(p.get("target") or p.get("candidate_distribution_id") or "none"))
print("Affected instances: "+(", ".join(s.get("instances",{})) or "none"))
print("Preserved: "+", ".join(p.get("preserved", ["existing instance declarations, runtime paths, and services"])))
if p.get("action")=="uninstall":
 print("Existing deployments will be stopped and removed before deleting program files.")
 print("Original declarations, removed profiles, and logs will be saved in a protected backup.")
'
}

setup_menu() {
  local backend status choice confirmation preview target operation selection result inspection_error available="Not checked"
  local self
  self=$(realpath -- "${BASH_SOURCE[0]}")
  local remote_args=(--non-interactive --account-home "$ACCOUNT_HOME" --prefix "$PREFIX")
  if $QUALIFICATION; then
    remote_args+=(--qualification --repository "$REPOSITORY" --ref "$RELEASE_REF")
    [[ -z "$QUALIFICATION_ARTIFACT_DIR" ]] || remote_args+=(--qualification-artifact-dir "$QUALIFICATION_ARTIFACT_DIR")
    [[ -z "$QUALIFICATION_FAILPOINT" ]] || remote_args+=(--qualification-failpoint "$QUALIFICATION_FAILPOINT")
  fi
  while true; do
    backend=$(setup_backend)
    inspection_error=""
    status='{"ok":true,"ownership":"absent","instances":{},"releases":[]}'
    printf '\nWorkspace MCP Manager — Setup\n\n'
    if [[ -n "$backend" ]]; then
      local diagnostic
      diagnostic=$(mktemp)
      if ! status=$(bash "$backend" --maintenance-status --account-home "$ACCOUNT_HOME" --prefix "$PREFIX" 2>"$diagnostic"); then
        inspection_error=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("error", ""))' <<<"$status" 2>/dev/null) || inspection_error=""
        [[ -n "$inspection_error" ]] || inspection_error=$(cat "$diagnostic")
        # Historical installers expose only --check. Retain both failure reasons.
        if ! status=$(bash "$backend" --check --account-home "$ACCOUNT_HOME" --prefix "$PREFIX" 2>"$diagnostic"); then
          inspection_error+=" ${inspection_error:+Fallback check: }$(cat "$diagnostic")"
          status='{"ok":false,"ownership":"inspection failed","instances":{},"releases":[]}'
        fi
      fi
      rm -f -- "$diagnostic"
    elif [[ -e "$PREFIX/lib/workspace-mcp-manager" || -L "$PREFIX/lib/workspace-mcp-manager" ]]; then
      status='{"ok":false,"ownership":"unrecognized installation","instances":{},"releases":[]}'
    fi
    if command -v python3 >/dev/null 2>&1; then setup_summary <<<"$status" || true; else printf 'Status: Python >=3.10 is required for inspection\n'; fi
    if [[ -d "$PREFIX/lib/workspace-mcp" || -L "$PREFIX/bin/workspace-mcp-setup" ]]; then
      printf 'Legacy workspace-mcp toolkit: detected (separate from the manager)\n'
    fi
    [[ -z "$inspection_error" ]] || printf 'Inspection: %s\n' "$inspection_error"
    printf 'Target: %s\nAvailable release: %s\n' "$PREFIX" "$available"
    local os_id=unknown os_version=unknown arch kernel host_ready=false
    if [[ -r /etc/os-release ]]; then
      os_id=$( . /etc/os-release; printf '%s' "${ID:-unknown}")
      os_version=$( . /etc/os-release; printf '%s' "${VERSION_ID:-unknown}")
    fi
    arch=$(uname -m)
    kernel=$(uname -r)
    if [[ "$os_id:$os_version:$arch" == ubuntu:22.04:aarch64 && "${kernel,,}" != *microsoft* && "${kernel,,}" != *wsl* ]] || [[ "$os_id:$os_version:$arch" == ubuntu:24.04:x86_64 && ( "${kernel,,}" == *microsoft* || "${kernel,,}" == *wsl* ) ]]; then
      host_ready=true
      printf 'Host: Ubuntu %s %s\n' "$os_version" "$arch"
    else
      printf 'Host: Needs attention — installation requires native Ubuntu 22.04 aarch64 or WSL Ubuntu 24.04 x86_64; observed %s %s %s\n' "$os_id" "$os_version" "$arch"
    fi
    local compatible_python=false candidate
    for candidate in python3 python3.12 python3.11; do
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then compatible_python=true; break; fi
    done
    if ! $compatible_python; then
      host_ready=false
      printf 'Prerequisite: Python >=3.11 is required for installation; checked python3, python3.12, python3.11.\n'
    fi
    local required
    for required in git curl sha256sum realpath mktemp flock tar unzip systemctl; do
      if ! command -v "$required" >/dev/null 2>&1; then
        host_ready=false
        printf 'Prerequisite: Missing %s.\n' "$required"
      fi
    done
    local primary='Install'
    case "$(json_value ownership <<<"$status" 2>/dev/null)" in
      standalone_distribution) primary='Update' ;;
      recognized_existing) primary='Migrate' ;;
    esac
    local ownership current recovery_label rollback_label uninstall_label open_label
    ownership=$(json_value ownership <<<"$status" 2>/dev/null)
    current=$(json_value current <<<"$status" 2>/dev/null)
    [[ -z "$current" || "$current" != "$available" ]] || primary='Already current'
    recovery_label='Recover interrupted operation'
    [[ "$(json_value recovery_required <<<"$status" 2>/dev/null)" == True ]] || recovery_label+=' (none pending)'
    rollback_label='Roll back to a retained release'
    uninstall_label='Uninstall'
    open_label='Open manager'
    if [[ "$ownership" != standalone_distribution ]]; then
      rollback_label+=' (requires standalone installation)'
      uninstall_label+=' (requires standalone installation)'
    fi
    if [[ "$ownership" != standalone_distribution && "$ownership" != recognized_existing ]]; then open_label+=' (not available)'; fi
    printf '\n1. %s\n2. Check for updates\n3. %s\n4. %s\n5. %s\n6. %s\n7. Import and replace legacy toolkit\n0. Exit\n\nChoice: ' "$primary" "$recovery_label" "$rollback_label" "$uninstall_label" "$open_label"
    IFS= read -r choice || return 0
    case "$choice" in
      0) return 0 ;;
      1|2)
        if [[ "$(json_value recovery_required <<<"$status" 2>/dev/null)" == True ]]; then
          printf 'Recover the interrupted operation first.\n'; continue
        fi
        if [[ "$choice" == 2 ]]; then
          printf 'Checking the production release…\n'
          if result=$(bash "$self" "${remote_args[@]}" --check); then
            available=$(json_value candidate_distribution_id <<<"$result")
            printf 'Available release: %s\n' "$available"
            if [[ "$(json_value install_required <<<"$result")" == False ]]; then primary='Already current'; printf 'Already current.\n'; fi
          else
            available='Check failed; local maintenance is available'
            printf '%s\n' "$available"
          fi
        else
          $host_ready || { printf 'Installation/update is unavailable on this host. See the readiness requirements above.\n'; continue; }
          local args=("${remote_args[@]}" --review)
          $CONFIGURE && args+=(--configure)
          bash "$self" "${args[@]}" || printf 'Installation/update did not complete. Review the result above.\n'
        fi
        ;;
      7)
        printf 'Discovering legacy instances (read-only)…\n'
        python3 - "$ACCOUNT_HOME" <<'PYDISCOVER'
import pathlib,sys
root=pathlib.Path(sys.argv[1])/'.config/workspace-mcp'
for path in sorted(root.glob('*.conf')):
 print('  '+path.stem)
PYDISCOVER
        local manager="$PREFIX/bin/workspace-mcp-manager"
        if [[ "$ownership" != standalone_distribution || ! -x "$manager" ]] || ! "$manager" instance import --help >/dev/null 2>&1; then
          $host_ready || { printf 'Install prerequisites must be satisfied before importing.\n'; continue; }
          printf 'A manager release with import support must be installed first. Existing legacy services will keep running.\n'
          bash "$self" "${remote_args[@]}" --review || { printf 'Manager installation did not complete.\n'; continue; }
        fi
        local candidates ids resolved_file
        if [[ ! -x "$manager" ]] || ! "$manager" instance import --help >/dev/null 2>&1; then
          printf 'Update the installed manager to a release with legacy import support.\n'; continue
        fi
        if ! candidates=$("$manager" instance import list); then printf '%s\n' "$candidates"; continue; fi
        if ! python3 -c 'import json,pathlib,sys; p=json.load(sys.stdin); expected=[pathlib.Path(x).resolve() for x in sys.argv[1:]]; observed=[pathlib.Path(p.get(k,"/")).resolve() for k in ("account_home","manager_prefix")]; raise SystemExit(0 if expected==observed else 1)' "$ACCOUNT_HOME" "$PREFIX" <<<"$candidates"; then
          printf 'The installed manager account or prefix differs from the selected target; migration is blocked.\n'; continue
        fi
        printf '%s\n' "$candidates" | python3 -c '
import json,sys
p=json.load(sys.stdin)
for row in p.get("candidates",[]): print(row["instance_id"]+": "+row["state"]+(" — "+row["reason"] if row.get("reason") else ""))
for row in p.get("imported",[]): print(row["instance_id"]+": imported")
'
        printf 'Instance IDs separated by spaces, or all (blank cancels): '
        IFS= read -r ids || return 0
        [[ -n "$ids" ]] || continue
        local selected_ids=()
        if [[ "$ids" == all ]]; then
          mapfile -t selected_ids < <(python3 -c 'import json,sys; p=json.load(sys.stdin); sys.stdout.write("".join(r["instance_id"]+"\n" for r in p.get("candidates",[])))' <<<"$candidates")
        else
          read -r -a selected_ids <<<"$ids"
        fi
        preview=$(mktemp)
        if ! "$manager" instance import preview "${selected_ids[@]}" >"$preview"; then
          cat "$preview"
          printf 'To resolve supported service-intent ambiguities, enter a reviewed v2 declaration-map JSON file (blank cancels): '
          IFS= read -r resolved_file || { rm -f -- "$preview"; return 0; }
          if [[ -z "$resolved_file" ]] || ! "$manager" instance import preview "${selected_ids[@]}" --candidate "$resolved_file" >"$preview"; then
            cat "$preview"; rm -f -- "$preview"; continue
          fi
        fi
        python3 - "$preview" <<'PYIMPORT'
import json,sys
p=json.load(open(sys.argv[1]))
for row in p['instances']:
 d=row['desired']
 print(row['instance_id']+': '+row['action']+' — '+d['workspace_path'])
 print('  Permission: '+d['mcp']['permission_mode']+'; runtime: '+d['lifecycle']['runtime'])
 print('  MCP: '+d['mcp']['binary'])
 print('  Tunnel: '+d['tunnel']['binary']+'; identity: '+d['tunnel']['id'])
 print('  Ports: '+str(d['mcp']['port'])+', '+str(d['tunnel']['health_port']))
 if row['action']=='import': print('  Upgrade: coding-tools-mcp 0.5.0; tunnel-client 0.0.15')
 print('  Shell environment: '+d['mcp'].get('shell_env_inherit','preserved'))
 if d['mcp'].get('exec_path'): print('  PATH: '+d['mcp']['exec_path'])
 if d['mcp'].get('external_roots'): print('  Execution roots: '+', '.join(d['mcp']['external_roots']))
 if d['tunnel'].get('env_file'): print('  Credential file (contents hidden): '+d['tunnel']['env_file'])
 if d.get('github',{}).get('config_dir'): print('  GitHub profile: '+d['github']['config_dir'])
 for mode,folders in d.get('access',{}).items():
  for folder in folders: print('  '+mode+': '+folder['alias']+' -> '+folder['path'])
 if row.get('unit_enablement'): print('  Boot enablement: '+', '.join(unit+(' enabled' if enabled else ' disabled') for unit,enabled in row['unit_enablement'].items()))
print(p['interruption'])
print('Settings and credential references are preserved. Credentials stay in place.')
print('The old toolkit is archived and retired only after all legacy instances have migrated.')
print('Toolkit retirement: '+p['retirement']['state'])
if p['retirement'].get('reason'): print(p['retirement']['reason'])
PYIMPORT
        printf 'Type MIGRATE to confirm this preview (anything else cancels): '
        IFS= read -r confirmation || confirmation=''
        if [[ "$confirmation" == MIGRATE ]]; then
          local fingerprint
          fingerprint=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$preview")
          printf 'Migrating selected instances sequentially…\n'
          if result=$("$manager" instance import apply --preview "$preview" --expected-preview-fingerprint "$fingerprint"); then
            printf '%s\n' "$result"
          else
            printf '%s\nMigration stopped. Use Recover for any pending transaction.\n' "$result"
          fi
        else
          printf 'Migration cancelled.\n'
        fi
        rm -f -- "$preview"
        ;;
      3)
        [[ -n "$backend" ]] || { printf 'No local recovery backend is available.\n'; continue; }
        printf 'Recover the interrupted local transaction. Completed migrations and removals remain committed. Continue? [y/N]: '
        IFS= read -r confirmation || return 0
        [[ "$confirmation" == y || "$confirmation" == Y ]] || continue
        if result=$(bash "$backend" --recover-only --account-home "$ACCOUNT_HOME" --prefix "$PREFIX"); then
          setup_result <<<"$result"
        else
          printf '%s\nRecovery failed. Review the result before retrying.\n' "$result"
        fi
        ;;
      4|5)
        [[ -n "$backend" ]] || { printf 'Install the manager before using maintenance.\n'; continue; }
        target=''
        operation=uninstall
        if [[ "$choice" == 4 ]]; then
          if [[ "$ownership" != standalone_distribution ]]; then printf 'No validated standalone rollback targets are available.\n'; continue; fi
          operation=rollback
          printf '%s\n' "$status" | python3 -c '
import json,sys
s=json.load(sys.stdin)
for i,r in enumerate(s.get("releases",[]),1):
 print(str(i)+". "+r["id"]+" — manager "+r["version"]+" "+str(r.get("components",{}))+(" (current)" if r["id"]==s.get("current") else ""))
'
          printf 'Release number (blank cancels): '
          IFS= read -r selection || return 0
          [[ -n "$selection" ]] || continue
          target=$(python3 -c 'import json,sys; s=json.load(sys.stdin); i=int(sys.argv[1]); assert i>0; print(s["releases"][i-1]["id"])' "$selection" <<<"$status" 2>/dev/null) || { printf 'Invalid selection.\n'; continue; }
        fi
        preview=$(mktemp)
        local args=(--maintenance-preview "$operation" --account-home "$ACCOUNT_HOME" --prefix "$PREFIX")
        [[ -z "$target" ]] || args+=(--maintenance-target "$target")
        if ! bash "$backend" "${args[@]}" >"$preview"; then
          cat -- "$preview"; rm -f -- "$preview"; printf 'Action unavailable; resolve the reported blocker first.\n'; continue
        fi
        setup_preview_summary <"$preview"
        if [[ "$operation" == uninstall ]]; then printf 'Type UNINSTALL to confirm (blank cancels): '; else printf 'Apply rollback? [y/N]: '; fi
        if ! IFS= read -r confirmation; then rm -f -- "$preview"; return 0; fi
        if [[ "$operation" == rollback ]]; then
          if [[ "$confirmation" == y || "$confirmation" == Y ]]; then confirmation=yes; else rm -f -- "$preview"; continue; fi
        elif [[ "$confirmation" != UNINSTALL ]]; then
          rm -f -- "$preview"; continue
        fi
        printf 'Executing %s…\n' "$operation"
        if result=$(bash "$backend" --maintenance-apply --confirmation "$confirmation" --account-home "$ACCOUNT_HOME" --prefix "$PREFIX" <"$preview"); then
          setup_result <<<"$result"
        else
          setup_result <<<"$result" || printf '%s\n' "$result"
          printf 'Operation interrupted or failed. Use Recover before retrying.\n'
        fi
        rm -f -- "$preview"
        ;;
      6)
        if [[ "$ownership" == standalone_distribution || "$ownership" == recognized_existing ]]; then
          "$PREFIX/bin/workspace-mcp-manager-tui" || printf 'Manager exited with an error.\n'
        else printf 'A validated manager installation is required.\n'; fi
        ;;
      *) printf 'Choose one of the numbered actions.\n' ;;
    esac
  done
}

[[ -n "$PREFIX" ]] || PREFIX="$ACCOUNT_HOME/.local"
if [[ "$INTERACTIVE" == auto ]]; then
  if [[ -t 0 && -t 1 ]]; then INTERACTIVE=true
  else die "no interactive terminal; use --non-interactive for automation or --interactive for a scripted menu"; fi
fi
if $INTERACTIVE; then
  ! $CHECK_REMOTE && ! $REVIEW || die "--check/--review require --non-interactive"
  setup_menu
  exit $?
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

if $CHECK_REMOTE || $REVIEW; then
  check_args=()
  for arg in "${args[@]}"; do [[ "$arg" == --configure ]] || check_args+=("$arg"); done
  preview_fingerprint=$(installation_fingerprint)
  preview=$(bash "$CHECKOUT/scripts/install.sh" --check "${check_args[@]}") || die "candidate inspection failed; no update was applied"
  if $CHECK_REMOTE; then printf '%s\n' "$preview"; exit 0; fi
  setup_preview_summary <<<"$preview"
  local_lock="$CHECKOUT/install-versions.lock"
  if [[ "$(uname -m)" == aarch64 && -f "$CHECKOUT/install-versions.aarch64.lock" ]]; then local_lock="$CHECKOUT/install-versions.aarch64.lock"; fi
  python3 - "$local_lock" <<'PYVERSIONS'
import json,sys
for name,component in json.load(open(sys.argv[1]))["components"].items():
 print(name+": "+component["version"])
PYVERSIONS
  if [[ "$(json_value recovery_required <<<"$preview")" == True ]]; then die "recover the interrupted operation before continuing"; fi
  if [[ "$(json_value install_required <<<"$preview")" == False ]]; then printf 'Already current; no update needed.\n'; exit 0; fi
  printf 'Apply this installation/update? [y/N]: '
  IFS= read -r confirmation || exit 0
  [[ "$confirmation" == y || "$confirmation" == Y ]] || exit 0
  [[ "$(installation_fingerprint)" == "$preview_fingerprint" ]] || die "installation state changed after preview; inspect again"
  refreshed=$(bash "$CHECKOUT/scripts/install.sh" --check "${check_args[@]}") || die "candidate reinspection failed"
  [[ "$refreshed" == "$preview" ]] || die "installation state changed after preview; inspect again"
  args+=(--expected-setup-fingerprint "$preview_fingerprint")
fi
printf 'Validating and staging the selected distribution…\n' >&2
bash "$CHECKOUT/scripts/install.sh" "${args[@]}"
