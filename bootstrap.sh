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
  base=$(dirname -- "${BASH_SOURCE[0]}")
  if [[ -d "$base" ]]; then
    base=$(cd -- "$base" && pwd -P)
    if [[ -f "$base/install.sh" ]]; then printf '%s' "$base/install.sh"; return; fi
    if [[ -f "$base/scripts/install.sh" ]]; then printf '%s' "$base/scripts/install.sh"; return; fi
  fi
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
try:
 p=json.load(sys.stdin)
 if not isinstance(p,dict): raise ValueError("object required")
except (ValueError,TypeError):
 print("Blocked: the operation returned an unreadable result. Review Installation details.")
 raise SystemExit(1)
if not p.get("ok",True):
 error=p.get("error", "operation failed")
 if isinstance(error,dict): error=error.get("message", str(error))
 print("Blocked or interrupted: "+str(error))
else:
 action=p.get("action") or ("legacy instances imported" if "completed" in p else "operation")
 print("Completed: "+{"install":"manager installed", "upgrade":"manager updated", "migration":"manager installation upgraded", "recover":"recovery completed", "rollback":"previous manager version restored", "uninstall":"manager uninstalled"}.get(action, str(action).replace("_", " "))+".")
if p.get("backup"): print("Preserved data: "+p["backup"])
if p.get("product_version"): print("Manager version: "+p["product_version"])
if p.get("completed_instances"): print("Removed deployments: "+", ".join(p["completed_instances"]))
if p.get("remaining_instances"): print("Deployments still requiring removal: "+", ".join(p["remaining_instances"]))
if p.get("completed"): print("Imported instances: "+", ".join(p["completed"]))
if p.get("failed"): print("Needs attention: "+p["failed"])
if p.get("unattempted"): print("Instances not attempted: "+", ".join(p["unattempted"]))
'
}

setup_summary() {
  python3 -c '
import json,sys
s=json.load(sys.stdin)
ownership=s.get("ownership")
recovery=sys.argv[1]=="true" or s.get("recovery_required")
if not s.get("ok",True) or ownership not in {"absent","recognized_existing","standalone_distribution"}:
 print("Installation status could not be verified.")
 print("Recommended: recover the interrupted operation in Maintenance." if recovery else "Recommended: open Installation details to review the problem.")
else:
 if ownership=="absent": print("No manager installation was found.")
 elif ownership=="recognized_existing": print("An older manager installation was found.")
 else:
  version=next((r.get("version") for r in s.get("releases",[]) if r.get("id")==s.get("current")),None) or s.get("installed_product_version")
  print("Manager"+(" "+version if version else "")+" is installed.")
 count=len(s["instances"]) if "instances" in s else s.get("instance_count")
 print(str(count)+" configured instance"+("" if count==1 else "s") if count is not None else "Configured instance count is unknown.")
 if recovery: print("Recommended: recover the interrupted operation in Maintenance.")
 elif ownership=="absent": print("Recommended: install the manager.")
 elif ownership=="recognized_existing": print("Recommended: upgrade the manager installation.")
 else: print("Recommended: open the instance manager to manage your workspaces.")
' "${pending:-false}"
}

setup_preview_summary() {
  python3 -c '
import json,sys
p=json.load(sys.stdin)
s=p.get("status", {})
action=p.get("action") or {"absent":"install", "recognized_existing":"migration", "standalone_distribution":"upgrade"}.get(p.get("ownership"), "change")
print("Review: "+{"install":"install manager", "migration":"upgrade older manager installation", "upgrade":"update manager", "rollback":"restore a previous manager version", "uninstall":"uninstall manager"}.get(action,action))
installed=p.get("installed_product_version")
candidate=p.get("candidate_product_version")
if not installed: installed=next((r.get("version") for r in s.get("releases",[]) if r.get("id")==s.get("current")),None)
if not candidate: candidate=next((r.get("version") for r in s.get("releases",[]) if r.get("id")==p.get("target")),None)
if installed: print("Installed manager version: "+installed)
if candidate: print("Candidate manager version: "+candidate)
if "instances" in s: print("Affected instances: "+(", ".join(s["instances"]) or "none"))
elif p.get("instance_count") is not None: print("Configured instances preserved: "+str(p["instance_count"]))
print("Preserved: "+", ".join(p.get("preserved", ["existing instance settings, runtime paths, and services"])))
if action=="uninstall":
 print("Existing deployments will be stopped and removed before deleting program files.")
 print("Original declarations, removed profiles, and logs will be saved in a protected backup.")
if p.get("recovery_command"): print("Recovery command: "+p["recovery_command"])
'
}

# Task helpers share the explicitly scoped menu context in setup_menu.
# Keep reads, presentation, and dispatch separate from installer mutations.
setup_read() {
  if IFS= read -r "$1"; then return 0; fi
  SETUP_EXIT=true
  return 1
}

setup_inspect() {
  printf 'Inspecting local installation (no network)…\n'
  if [[ ! -f "$self" && -f "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/setup-recovery/setup.sh" ]]; then
    self="$ACCOUNT_HOME/.local/state/workspace-mcp-manager/setup-recovery/setup.sh"
  fi
  backend=$(setup_backend)
  inspection_error=""
  status='{"ok":true,"ownership":"absent"}'
  local diagnostic
  if [[ -n "$backend" ]]; then
    diagnostic=$(mktemp)
    if ! status=$(bash "$backend" --maintenance-status --account-home "$ACCOUNT_HOME" --prefix "$PREFIX" 2>"$diagnostic"); then
      inspection_error=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("error", ""))' <<<"$status" 2>/dev/null) || inspection_error=""
      [[ -n "$inspection_error" ]] || inspection_error=$(cat "$diagnostic")
      # Compatibility with historical installers that only expose --check.
      if ! status=$(bash "$backend" --check --account-home "$ACCOUNT_HOME" --prefix "$PREFIX" 2>"$diagnostic"); then
        inspection_error+=" Fallback check: $(cat "$diagnostic")"
        status='{"ok":false,"ownership":"unknown"}'
      fi
    fi
    rm -f -- "$diagnostic"
  else
    local marker
    for marker in "$PREFIX/lib/workspace-mcp-manager" "$PREFIX/bin/workspace-mcp-manager" "$PREFIX/bin/workspace-mcp-manager-tui" "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/maintenance-transaction.json" "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/distribution-transaction.json"; do
      if [[ -e "$marker" || -L "$marker" ]]; then
        status='{"ok":false,"ownership":"unknown"}'
        inspection_error='Installation files were found, but no local inspection backend is available. Use setup from a complete checkout or the installed recovery helper.'
        break
      fi
    done
  fi
  if ! python3 -c 'import json,sys; s=json.load(sys.stdin); assert isinstance(s,dict); assert isinstance(s.get("ok",True),bool); assert s.get("ownership") in {"absent","recognized_existing","standalone_distribution"}; assert s.get("ok",True); assert "instances" not in s or isinstance(s["instances"],dict)' <<<"$status" 2>/dev/null; then
    [[ -n "$inspection_error" ]] || inspection_error='The local inspector returned an invalid or blocked result.'
    status='{"ok":false,"ownership":"unknown"}'
  fi
  ownership=$(json_value ownership <<<"$status" 2>/dev/null) || ownership=unknown
  pending=false
  [[ "$(json_value recovery_required <<<"$status" 2>/dev/null)" != True ]] || pending=true
  local journal
  for journal in "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/maintenance-transaction.json" "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/distribution-transaction.json"; do
    [[ ! -e "$journal" && ! -L "$journal" ]] || pending=true
  done
  if compgen -G "$ACCOUNT_HOME/.local/state/workspace-mcp-manager/imports/*.json" >/dev/null; then pending=true; fi
  legacy=false
  if [[ -d "$PREFIX/lib/workspace-mcp" || -L "$PREFIX/bin/workspace-mcp-setup" || -d "$ACCOUNT_HOME/.config/workspace-mcp" ]]; then legacy=true; fi
}

setup_readiness() {
  local os_id=unknown os_version=unknown arch kernel candidate compatible_python=false required
  host_ready=true
  readiness=""
  if [[ -r /etc/os-release ]]; then
    os_id=$( . /etc/os-release; printf '%s' "${ID:-unknown}")
    os_version=$( . /etc/os-release; printf '%s' "${VERSION_ID:-unknown}")
  fi
  arch=$(uname -m); kernel=$(uname -r)
  readiness="Host: $os_id $os_version $arch"
  if ! { [[ "$os_id:$os_version:$arch" == ubuntu:22.04:aarch64 && "${kernel,,}" != *microsoft* && "${kernel,,}" != *wsl* ]] || [[ "$os_id:$os_version:$arch" == ubuntu:24.04:x86_64 && ( "${kernel,,}" == *microsoft* || "${kernel,,}" == *wsl* ) ]]; }; then
    host_ready=false
    readiness+=$'\nInstallation requires native Ubuntu 22.04 aarch64 or WSL Ubuntu 24.04 x86_64.'
  fi
  for candidate in python3 python3.12 python3.11; do
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then compatible_python=true; break; fi
  done
  if ! $compatible_python; then host_ready=false; readiness+=$'\nPython >=3.11 is required; checked python3, python3.12, python3.11.'; fi
  for required in git curl sha256sum realpath mktemp flock tar unzip systemctl; do
    if ! command -v "$required" >/dev/null 2>&1; then host_ready=false; readiness+=$'\nMissing prerequisite: '"$required"; fi
  done
  if command -v systemctl >/dev/null 2>&1 && ! systemctl --user show-environment >/dev/null 2>&1; then
    host_ready=false
    readiness+=$'\nUser systemd is unavailable. Run setup in a login session with user systemd enabled.'
  fi
}

setup_render_menu() {
  printf '\nWorkspace MCP Manager — Setup\n\n'
  setup_summary <<<"$status" || printf 'Installation status could not be verified.\n'
  if [[ -n "$inspection_error" ]]; then
    local short_error="${inspection_error%%$'\n'*}"
    printf 'Inspection: %.160s\n' "$short_error"
  fi
  local primary='Install manager' open='Open instance manager'
  case "$ownership" in
    recognized_existing) primary='Upgrade manager installation' ;;
    standalone_distribution) primary='Check for manager updates' ;;
    absent) open+=' (unavailable: install the manager first)' ;;
    *) primary='Install or update manager (unavailable: verify installation status first)'; open+=' (unavailable: verify installation status first)' ;;
  esac
  if $pending; then primary+=' (unavailable: recover in Maintenance first)'; fi
  printf '\n1. %s\n2. %s\n3. Maintenance\n4. Installation details\n' "$primary" "$open"
  $legacy && printf '5. Import legacy toolkit instances\n'
  printf '0. Exit\n\nChoice [0]: '
}

setup_details() {
  local choice
  printf '\nInstallation details\n\nAccount home: %s\nInstall prefix: %s\n' "$ACCOUNT_HOME" "$PREFIX"
  printf '%s\n' "$status" | python3 -c '
import json,sys
s=json.load(sys.stdin)
print("Ownership: "+s.get("ownership", "unknown"))
print("Active release: "+str(s.get("current") or s.get("installed_distribution_id") or "unknown"))
for r in s.get("releases",[]):
 print("Retained release: "+r["id"]+"; manager "+r["version"])
 for k,v in r.get("components",{}).items(): print("  "+k+": "+v)
'
  if $pending; then printf 'Recovery: required\n'
  elif [[ "$ownership" == unknown ]]; then printf 'Recovery: unknown\n'
  else printf 'Recovery: not required\n'; fi
  [[ -z "$inspection_error" ]] || printf 'Diagnostic: %s\n' "$inspection_error"
  setup_readiness
  printf '%s\n' "$readiness"
  printf '\n0. Back\nChoice [0]: '
  setup_read choice || return 0
}

setup_open_manager() {
  if [[ "$ownership" != standalone_distribution && "$ownership" != recognized_existing ]]; then
    printf 'Blocked: a verified manager installation is required. Open Installation details.\n'; return 0
  fi
  if [[ ! -x "$PREFIX/bin/workspace-mcp-manager-tui" ]]; then
    printf 'Blocked: the instance manager command is missing. Review Installation details.\n'; return 0
  fi
  printf 'Opening instance manager. Close it to return to setup.\n'
  "$PREFIX/bin/workspace-mcp-manager-tui" || printf 'The instance manager exited with an error. Check Installation details or run the command from your terminal.\n'
}

setup_next_steps() {
  local choice
  while ! $SETUP_EXIT; do
    printf '\nNext steps\n\n1. Open instance manager\n2. Return to setup\n0. Exit\n\nChoice [0]: '
    setup_read choice || return 0
    case "$choice" in
      1) setup_open_manager; return 0 ;;
      2) return 0 ;;
      0|'') SETUP_EXIT=true; return 0 ;;
      *) printf 'Choose 1, 2, or 0.\n' ;;
    esac
  done
}

setup_install_task() {
  local choice before
  if $pending; then printf 'Blocked: recover the interrupted operation in Maintenance first.\n'; return 0; fi
  case "$ownership" in absent|recognized_existing|standalone_distribution) ;; *) printf 'Blocked: verify installation status in Installation details first.\n'; return 0 ;; esac
  printf '\nInstall or update manager\n\nThis task contacts the release server and reviews the candidate before any change.\n'
  if [[ "$ownership" == recognized_existing ]]; then printf 'Upgrading this older manager installation preserves instance settings and running services. This does not import legacy toolkit instances.\n'; fi
  printf '\n1. Check release and review change\n0. Back\n\nChoice [0]: '
  setup_read choice || return 0
  [[ "$choice" == 1 ]] || { printf 'Cancelled: no installation changes.\n'; return 0; }
  setup_readiness
  if ! $host_ready; then printf 'Blocked: installation prerequisites need attention.\n%s\n' "$readiness"; return 0; fi
  before=$(installation_fingerprint)
  local args=("${remote_args[@]}" --review)
  $CONFIGURE && args+=(--configure)
  printf 'Checking the release server…\n'
  if ! bash "$self" "${args[@]}"; then
    printf 'Blocked or interrupted: installation/update did not complete. Review the diagnostic above, then check Maintenance for recovery.\n'; return 0
  fi
  setup_inspect
  if [[ "$(installation_fingerprint)" != "$before" && "$ownership" == standalone_distribution ]] && ! $pending && ! $CONFIGURE; then setup_next_steps; fi
}

setup_maintenance_task() {
  local choice confirmation preview target operation selection result reason rollback_reason rollback_count
  while ! $SETUP_EXIT; do
    printf '\nMaintenance\n\nRecover unfinished work, restore a previous manager version, or uninstall.\n'
    reason=''
    $pending || reason=' (unavailable: no interrupted operation detected)'
    [[ -n "$backend" ]] || reason=' (unavailable: no local recovery backend)'
    printf '\n1. Recover interrupted operation%s\n' "$reason"
    reason=''
    if $pending; then reason=' (unavailable: recover first)'
    elif [[ "$ownership" != standalone_distribution ]]; then reason=' (unavailable: upgrade the manager installation first)'; fi
    rollback_reason="$reason"
    if [[ -z "$rollback_reason" ]]; then
      rollback_count=$(python3 -c 'import json,sys; s=json.load(sys.stdin); print(sum(r["id"]!=s.get("current") for r in s.get("releases",[])))' <<<"$status")
      [[ "$rollback_count" != 0 ]] || rollback_reason=' (unavailable: no previous version retained)'
    fi
    printf '2. Restore a previous manager version%s\n3. Uninstall manager%s\n0. Back\n\nChoice [0]: ' "$rollback_reason" "$reason"
    setup_read choice || return 0
    case "$choice" in
      0|'') return 0 ;;
      1)
        if ! $pending || [[ -z "$backend" ]]; then printf 'Recovery is unavailable: no pending operation or local backend. Review Installation details.\n'; continue; fi
        printf 'Review: recover unfinished local work. Completed instance removals and imports stay completed.\nContinue? [y/N]: '
        setup_read confirmation || return 0
        [[ "$confirmation" == y || "$confirmation" == Y ]] || { printf 'Cancelled: recovery was not started.\n'; continue; }
        if result=$(bash "$backend" --recover-only --account-home "$ACCOUNT_HOME" --prefix "$PREFIX"); then setup_result <<<"$result"
        else setup_result <<<"$result" || printf '%s\n' "$result"; printf 'Recovery did not complete. Review Installation details before retrying.\n'; fi
        return 0
        ;;
      2|3)
        if $pending; then printf 'Blocked: recover the interrupted operation first.\n'; continue; fi
        if [[ "$ownership" != standalone_distribution || -z "$backend" ]]; then printf 'Blocked: Upgrade the manager installation first. A verified current-format installation is required.\n'; continue; fi
        operation=uninstall; target=''
        if [[ "$choice" == 2 ]]; then
          operation=rollback
          rollback_count=$(python3 -c 'import json,sys; s=json.load(sys.stdin); print(sum(r["id"]!=s.get("current") for r in s.get("releases",[])))' <<<"$status")
          if [[ "$rollback_count" == 0 ]]; then printf 'No previous manager versions are available locally.\n'; continue; fi
          python3 -c 'import json,sys; s=json.load(sys.stdin); rows=[r for r in s.get("releases",[]) if r["id"]!=s.get("current")]; [print(str(i)+". Manager "+r["version"]+" ("+r["id"].removeprefix("dist-")[:8]+")") for i,r in enumerate(rows,1)]' <<<"$status"
          printf '0. Back\nPrevious version [0]: '
          setup_read selection || return 0
          [[ -n "$selection" && "$selection" != 0 ]] || { printf 'Cancelled: manager version unchanged.\n'; continue; }
          target=$(python3 -c 'import json,sys; s=json.load(sys.stdin); rows=[r for r in s.get("releases",[]) if r["id"]!=s.get("current")]; i=int(sys.argv[1]); assert i>0; print(rows[i-1]["id"])' "$selection" <<<"$status" 2>/dev/null) || { printf 'Invalid selection. Choose one of the listed versions.\n'; continue; }
        fi
        preview=$(mktemp)
        local args=(--maintenance-preview "$operation" --account-home "$ACCOUNT_HOME" --prefix "$PREFIX")
        [[ -z "$target" ]] || args+=(--maintenance-target "$target")
        if ! bash "$backend" "${args[@]}" >"$preview"; then
          setup_result <"$preview" || cat -- "$preview"
          rm -f -- "$preview"; printf 'Blocked: resolve the reported problem before retrying.\n'; return 0
        fi
        setup_preview_summary <"$preview"
        if [[ "$operation" == uninstall ]]; then printf 'Type UNINSTALL to confirm (blank cancels): '; else printf 'Restore this manager version? [y/N]: '; fi
        if ! setup_read confirmation; then rm -f -- "$preview"; return 0; fi
        if [[ "$operation" == rollback ]]; then
          if [[ "$confirmation" == y || "$confirmation" == Y ]]; then confirmation=yes; else rm -f -- "$preview"; printf 'Cancelled: manager version unchanged.\n'; continue; fi
        elif [[ "$confirmation" != UNINSTALL ]]; then
          rm -f -- "$preview"; printf 'Cancelled: manager remains installed.\n'; continue
        fi
        printf 'Executing %s…\n' "$operation"
        if result=$(bash "$backend" --maintenance-apply --confirmation "$confirmation" --account-home "$ACCOUNT_HOME" --prefix "$PREFIX" <"$preview"); then setup_result <<<"$result"
        else
          setup_result <<<"$result" || printf '%s\n' "$result"
          setup_inspect
          if $pending; then printf 'Operation interrupted. Use Maintenance > Recover interrupted operation before retrying.\n'
          else printf 'Operation blocked. Review Installation details and the diagnostic above before retrying.\n'; fi
          python3 -c 'import json,sys; p=json.load(sys.stdin); print("Recovery command: "+p["recovery_command"]) if p.get("recovery_command") else None' <"$preview"
        fi
        rm -f -- "$preview"
        return 0
        ;;
      *) printf 'Choose 1, 2, 3, or 0.\n' ;;
    esac
  done
}

setup_import_task() {
  if $pending; then printf 'Blocked: recover the interrupted operation in Maintenance first.\n'; return 0; fi
  if ! $legacy; then printf 'No legacy toolkit installation was detected.\n'; return 0; fi
  case "$ownership" in absent|recognized_existing|standalone_distribution) ;; *) printf 'Blocked: verify installation status in Installation details first.\n'; return 0 ;; esac
  printf '\nImport legacy toolkit instances\n\nThis transfers old toolkit instances into manager control. It is separate from upgrading the manager installation. Each import requires a reviewed preview and MIGRATE confirmation.\n'
  printf 'Discovering legacy instances (read-only)…\n'
  python3 - "$ACCOUNT_HOME" <<'PYDISCOVER'
import pathlib,sys
root=pathlib.Path(sys.argv[1])/'.config/workspace-mcp'
for path in sorted(root.glob('*.conf')):
 print('  '+path.stem)
PYDISCOVER
  local manager="$PREFIX/bin/workspace-mcp-manager" preview confirmation result
  if [[ "$ownership" != standalone_distribution || ! -x "$manager" ]] || ! "$manager" instance import --help >/dev/null 2>&1; then
    setup_readiness
    $host_ready || { printf 'Blocked: install prerequisites must be satisfied before importing.\n%s\n' "$readiness"; return 0; }
    printf 'A manager release with import support must be installed first. Existing legacy services will keep running.\n'
    bash "$self" "${remote_args[@]}" --review || { printf 'Manager installation did not complete.\n'; return 0; }
  fi
  local candidates ids resolved_file
  if [[ ! -x "$manager" ]] || ! "$manager" instance import --help >/dev/null 2>&1; then
    printf 'Update the installed manager to a release with legacy import support.\n'; return 0
  fi
  if ! candidates=$("$manager" instance import list); then printf '%s\n' "$candidates"; return 0; fi
  if ! python3 -c 'import json,pathlib,sys; p=json.load(sys.stdin); expected=[pathlib.Path(x).resolve() for x in sys.argv[1:]]; observed=[pathlib.Path(p.get(k,"/")).resolve() for k in ("account_home","manager_prefix")]; raise SystemExit(0 if expected==observed else 1)' "$ACCOUNT_HOME" "$PREFIX" <<<"$candidates"; then
    printf 'The installed manager account or prefix differs from the selected target; migration is blocked.\n'; return 0
  fi
  printf '%s\n' "$candidates" | python3 -c '
import json,sys
p=json.load(sys.stdin)
for row in p.get("candidates",[]): print(row["instance_id"]+": "+row["state"]+(" — "+row["reason"] if row.get("reason") else ""))
for row in p.get("imported",[]): print(row["instance_id"]+": imported")
'
  if [[ "$(python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("candidates",[])))' <<<"$candidates")" == 0 ]]; then
    printf 'No legacy instances remain to import. Return to setup to manage existing instances.\n'; return 0
  fi
  printf 'Instance IDs separated by spaces, or all (0 or blank goes back): '
  setup_read ids || return 0
  [[ -n "$ids" && "$ids" != 0 ]] || { printf 'Cancelled: no instances imported.\n'; return 0; }
  local selected_ids=()
  if [[ "$ids" == all ]]; then
    mapfile -t selected_ids < <(python3 -c 'import json,sys; p=json.load(sys.stdin); sys.stdout.write("".join(r["instance_id"]+"\n" for r in p.get("candidates",[])))' <<<"$candidates")
  else
    read -r -a selected_ids <<<"$ids"
  fi
  preview=$(mktemp)
  if ! "$manager" instance import preview "${selected_ids[@]}" >"$preview"; then
    cat "$preview"
    printf 'Some settings could not be resolved. For advanced use, enter a reviewed v2 declaration-map JSON file (0 or blank goes back): '
    setup_read resolved_file || { rm -f -- "$preview"; return 0; }
    if [[ -z "$resolved_file" || "$resolved_file" == 0 ]] || ! "$manager" instance import preview "${selected_ids[@]}" --candidate "$resolved_file" >"$preview"; then
      cat "$preview"; rm -f -- "$preview"; return 0
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
  setup_read confirmation || confirmation=''
  if [[ "$confirmation" == MIGRATE ]]; then
    local fingerprint
    fingerprint=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$preview")
    printf 'Migrating selected instances sequentially…\n'
    if result=$("$manager" instance import apply --preview "$preview" --expected-preview-fingerprint "$fingerprint"); then
      setup_result <<<"$result"
    else
      setup_result <<<"$result" || printf '%s\n' "$result"
      printf 'Import stopped. Review the reported problem. Use Maintenance > Recover interrupted operation if recovery is required.\n'
    fi
  else
    printf 'Migration cancelled.\n'
  fi
  rm -f -- "$preview"

}

setup_menu() {
  local backend status ownership pending legacy inspection_error readiness host_ready self
  local choice SETUP_EXIT=false
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
    printf 'Blocked: setup inspection requires Python >=3.10. Install Python, then reopen setup.\n'
    return 0
  fi
  self=$(realpath -- "${BASH_SOURCE[0]}")
  local remote_args=(--non-interactive --account-home "$ACCOUNT_HOME" --prefix "$PREFIX")
  if $QUALIFICATION; then
    remote_args+=(--qualification --repository "$REPOSITORY" --ref "$RELEASE_REF")
    [[ -z "$QUALIFICATION_ARTIFACT_DIR" ]] || remote_args+=(--qualification-artifact-dir "$QUALIFICATION_ARTIFACT_DIR")
    [[ -z "$QUALIFICATION_FAILPOINT" ]] || remote_args+=(--qualification-failpoint "$QUALIFICATION_FAILPOINT")
  fi
  while ! $SETUP_EXIT; do
    setup_inspect
    setup_render_menu
    setup_read choice || return 0
    case "$choice" in
      0|'') return 0 ;;
      1) setup_install_task ;;
      2) setup_open_manager ;;
      3) setup_maintenance_task ;;
      4) setup_details ;;
      5) setup_import_task ;;
      *) printf 'Choose one of the listed tasks, or 0 to exit.\n' ;;
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
if $CONFIGURE && ! $REVIEW; then args+=(--configure); fi
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
  if [[ "$(json_value install_required <<<"$preview")" == False ]]; then
    printf 'Already current; no update needed.\n'
    if $CONFIGURE; then "$PREFIX/bin/workspace-mcp-manager-tui"; fi
    exit 0
  fi
  printf 'Apply this installation/update? [y/N]: '
  IFS= read -r confirmation || { printf 'Cancelled: no installation changes.\n'; exit 0; }
  [[ "$confirmation" == y || "$confirmation" == Y ]] || { printf 'Cancelled: no installation changes.\n'; exit 0; }
  [[ "$(installation_fingerprint)" == "$preview_fingerprint" ]] || die "installation state changed after preview; inspect again"
  refreshed=$(bash "$CHECKOUT/scripts/install.sh" --check "${check_args[@]}") || die "candidate reinspection failed"
  [[ "$refreshed" == "$preview" ]] || die "installation state changed after preview; inspect again"
  args+=(--expected-setup-fingerprint "$preview_fingerprint")
fi
printf 'Validating and staging the selected distribution…\n' >&2
if $REVIEW; then
  if result=$(bash "$CHECKOUT/scripts/install.sh" "${args[@]}"); then
    setup_result <<<"$result"
    if $CONFIGURE; then "$PREFIX/bin/workspace-mcp-manager-tui"; fi
  else
    setup_result <<<"$result" || printf '%s\n' "$result"
    die "installation/update did not complete; inspect local status before retrying"
  fi
else
  bash "$CHECKOUT/scripts/install.sh" "${args[@]}"
fi
