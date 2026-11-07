#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
args=()
dry=false
recover=false
confirmation=''
while (($#)); do
  case "$1" in
    --dry-run) dry=true; shift ;;
    --recover-only) recover=true; shift ;;
    --confirmation|--account-home|--prefix)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo "$1 requires a value" >&2; exit 2; }
      if [[ "$1" == --confirmation ]]; then confirmation=$2; else args+=("$1" "$2"); fi
      shift 2 ;;
    -h|--help)
      echo 'Usage: bash scripts/uninstall.sh [--dry-run | --recover-only | --confirmation UNINSTALL] [--account-home PATH] [--prefix PATH]'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
if { $dry && $recover; } || { [[ -n "$confirmation" ]] && { $dry || $recover; }; }; then
  echo 'Conflicting uninstall modes' >&2; exit 2
fi
[[ -z "$confirmation" || "$confirmation" == UNINSTALL ]] || { echo 'Confirmation must be UNINSTALL' >&2; exit 2; }
# Never select an interpreter inside the distribution being removed.
WORKSPACE_MCP_BOOTSTRAP_PYTHON=/usr/bin/python3
export WORKSPACE_MCP_BOOTSTRAP_PYTHON
backend="$SCRIPT_DIR/install.sh"
if $recover; then exec bash "$backend" --recover-only "${args[@]}"; fi
status=$(bash "$backend" --maintenance-status "${args[@]}")
if /usr/bin/python3 -c 'import json,sys; s=json.load(sys.stdin); sys.exit(not (s.get("ownership")=="absent" and not s.get("recovery_required") and not s.get("entrypoints")))' <<<"$status"; then
  echo 'Installation already absent. Configuration and recovery metadata are preserved.'
  exit 0
fi
preview=$(bash "$backend" --maintenance-preview uninstall "${args[@]}")
printf '%s\n' "$preview"
$dry && exit 0
if [[ -z "$confirmation" ]]; then
  [[ -t 0 ]] || { echo 'Noninteractive uninstall requires --confirmation UNINSTALL' >&2; exit 2; }
  read -r -p 'Type UNINSTALL to remove the listed installation: ' confirmation || exit 2
fi
[[ "$confirmation" == UNINSTALL ]] || { echo 'Cancelled; installation unchanged.'; exit 0; }
bash "$backend" --maintenance-apply --confirmation UNINSTALL "${args[@]}" <<<"$preview"
final_status=$(bash "$backend" --maintenance-status "${args[@]}")
/usr/bin/python3 -c 'import json,sys; s=json.load(sys.stdin); sys.exit(not (s.get("ownership")=="absent" and not s.get("recovery_required") and not s.get("entrypoints")))' <<<"$final_status" || { echo 'Removal verification failed' >&2; exit 2; }
printf '%s\n' "$final_status"
echo 'User configuration, backups, distribution lock and setup-recovery metadata are preserved under the selected account home.'
