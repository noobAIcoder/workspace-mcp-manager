#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
args=()
while (($#)); do
  case "$1" in
    --account-home|--prefix)
      (($# >= 2)) || { printf '%s requires a path\n' "$1" >&2; exit 2; }
      args+=("$1" "$2"); shift 2 ;;
    -h|--help)
      printf 'Usage: bash scripts/setup-local.sh [--account-home PATH] [--prefix PATH]\nInstall/update the clean local commit using a numbered menu.\n'
      exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
exec bash "$SCRIPT_DIR/../bootstrap.sh" --interactive --local-repo "$SCRIPT_DIR/.." "${args[@]}"
