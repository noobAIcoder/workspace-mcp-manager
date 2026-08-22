#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTANCE_ID=${PM3_1_2_INSTANCE_ID:-electrocad}
WORKSPACE=${PM3_1_2_WORKSPACE:-$HOME/repos/ElectroCAD}
export PYTHONPATH="$ROOT/src:$ROOT/tests:$ROOT${PYTHONPATH:+:$PYTHONPATH}"

json_assert() {
  local expression=$1
  python3 -c '
import json, sys
d=json.load(sys.stdin)
safe={"all":all,"any":any,"bool":bool,"len":len,"set":set,"sorted":sorted,"str":str,"isinstance":isinstance,"list":list,"dict":dict}
if not eval(sys.argv[1], {"__builtins__": {}}, {"d": d, **safe}):
    raise SystemExit(f"assertion failed: {sys.argv[1]}\n{json.dumps(d, indent=2, sort_keys=True)}")
' "$expression"
}

echo "=== PM3.1.2 deterministic toolchain contracts ==="
python3 -m unittest \
  tests.test_pm3_1_2_toolchain \
  tests.test_pm3_1_setup \
  tests.test_tooling
echo "PM3_1_2_REPOSITORY_REQUIREMENT_DISCOVERY=PASS"
echo "PM3_1_2_NODE_RANGE_EVALUATION=PASS"
echo "PM3_1_2_NVM_SELECTION=PASS"
echo "PM3_1_2_PNPM_VERSION_CONTRACT=PASS"
echo "PM3_1_2_CANDIDATE_TOOLCHAIN_RECOMMENDATION=PASS"
echo "PM3_1_2_EXISTING_DECLARATION_NO_SILENT_REWRITE=PASS"

echo "=== PM3.1.2 live ElectroCAD discovery ==="
DISCOVERY=$(python3 -m workspace_mcp_manager.cli _runtime discover "$WORKSPACE")
printf '%s\n' "$DISCOVERY" | json_assert \
  'd["discovery_version"] == 2 and d["development_toolchain"]["development_toolchain_projection_version"] == 1 and d["development_toolchain"]["requirements"]["node"]["range"] == ">=24.18.0 <25" and d["development_toolchain"]["requirements"]["package_manager"]["name"] == "pnpm" and d["development_toolchain"]["requirements"]["package_manager"]["version"] == "11.17.0" and d["development_toolchain"]["selection"]["state"] == "ready" and d["development_toolchain"]["selection"]["node_version"] == "24.18.0" and d["development_toolchain"]["selection"]["package_manager_version"] == "11.17.0" and d["development_toolchain"]["declaration"]["state"] == "ready"'
echo "PM3_1_2_ELECTROCAD_DISCOVERY=PASS"
echo "PM3_1_2_ELECTROCAD_DECLARATION_TOOLCHAIN=PASS"
NODE_BIN=$(printf '%s\n' "$DISCOVERY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["development_toolchain"]["selection"]["node_bin"])')
[[ -x "$NODE_BIN/pnpm" ]] || { echo "PM3_1_2_FAIL=selected pnpm executable missing" >&2; exit 1; }

SHOW=$(python3 -m workspace_mcp_manager.cli _runtime show "$INSTANCE_ID")
printf '%s\n' "$SHOW" | json_assert \
  'd["desired"]["mcp"]["exec_path"].split(":")[0].endswith("/.nvm/versions/node/v24.18.0/bin") and any(item.endswith("/.nvm/versions/node/v24.18.0") for item in d["desired"]["mcp"]["external_roots"])'
echo "PM3_1_2_ELECTROCAD_DECLARATION=PASS"

echo "=== PM3.1.2 local MCP toolchain execution ==="
python3 - "$INSTANCE_ID" <<'PY'
import json, sys
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.session_continuity import ClientCompatibilityService

paths=ManagerPaths.for_current_user()
registry=InstanceRegistry(paths.registry_dir)
desired=registry.get(sys.argv[1])
service=ClientCompatibilityService(paths, registry)
url=service._url(desired)
init,sid,_=service._request(url,{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"pm3.1.2-toolchain","version":"1"}}})
assert sid
service._request(url,{"jsonrpc":"2.0","method":"notifications/initialized","params":{}},session_id=sid)
result=service._tool(url,sid,2,"exec_command",{"cmd":"printf 'node='; node --version; printf 'pnpm='; pnpm --version; printf 'corepack='; corepack --version","timeout_ms":10000,"max_output_bytes":4096,"preview_bytes":1024,"verbosity":"full"})
text=str(result.get("stdout") or "")
assert "node=v24.18.0" in text, text
assert "pnpm=11.17.0" in text, text
assert "corepack=" in text, text
service._request(url,None,session_id=sid,method="DELETE")
PY
echo "PM3_1_2_ELECTROCAD_MCP_NODE=PASS"
echo "PM3_1_2_ELECTROCAD_MCP_PNPM=PASS"
echo "PM3_1_2_ELECTROCAD_MCP_COREPACK=PASS"

echo "=== PM3.1.2 ElectroCAD repository gate ==="
# Do not update the target lockfile during manager qualification.  Frozen
# lockfile validation is deliberately used as the repository-owned boundary.
set +e
REPO_GATE=$(cd "$WORKSPACE" && CI=true PATH="$NODE_BIN:$PATH" "$NODE_BIN/pnpm" install --lockfile-only --frozen-lockfile 2>&1)
REPO_RC=$?
set -e
if (( REPO_RC == 0 )); then
  echo "PM3_1_2_ELECTROCAD_LOCKFILE=PASS"
  echo "PM3_1_2_ELECTROCAD_REPOSITORY_QUALIFICATION=READY"
else
  if printf '%s\n' "$REPO_GATE" | grep -Fq 'ERR_PNPM_OUTDATED_LOCKFILE'; then
    echo "PM3_1_2_ELECTROCAD_LOCKFILE=BLOCKED_OUTDATED_LOCKFILE"
    echo "PM3_1_2_ELECTROCAD_REPOSITORY_QUALIFICATION=BLOCKED_REPOSITORY_STATE"
  else
    printf '%s\n' "$REPO_GATE" >&2
    exit "$REPO_RC"
  fi
fi

echo "PM3_1_2_IMPLEMENTATION_QUALIFICATION=PASS"
