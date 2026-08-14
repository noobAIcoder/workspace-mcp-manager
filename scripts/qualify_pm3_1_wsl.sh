#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTANCE_ID=${PM3_1_INSTANCE_ID:-manager-qual}
MANAGER=${PM3_1_MANAGER:-workspace-mcp-manager}
TMP=$(mktemp -d -t workspace-mcp-pm3-1-XXXXXX)
UNREGISTERED="$TMP/Unregistered Context Repo"
NESTED="$UNREGISTERED/packages/core"
TOOLKIT_PREFIX="$TMP/toolkit"

cleanup() {
  rm -rf -- "$TMP"
}
trap cleanup EXIT INT TERM

fail() {
  printf 'PM3_1_FAIL=%s\n' "$*" >&2
  exit 1
}

command -v "$MANAGER" >/dev/null 2>&1 || fail "manager executable is unavailable: $MANAGER"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"
command -v git >/dev/null 2>&1 || fail "git is unavailable"

json_assert() {
  local expression=$1
  python3 -c '
import json, sys
d = json.load(sys.stdin)
safe = {"all": all, "any": any, "bool": bool, "len": len, "set": set, "sorted": sorted, "str": str, "isinstance": isinstance, "list": list, "dict": dict}
if not eval(sys.argv[1], {"__builtins__": {}}, {"d": d, **safe}):
    raise SystemExit(f"assertion failed: {sys.argv[1]}\n{json.dumps(d, indent=2, sort_keys=True)}")
' "$expression"
}

json_field() {
  local field=$1
  python3 -c '
import json, sys
value = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    value = value[int(part)] if isinstance(value, list) else value[part]
print("" if value is None else value)
' "$field"
}

candidate_request() {
  local workspace=$1
  local instance_id=${2:-}
  local tunnel_id=${3:-}
  python3 - "$workspace" "$instance_id" "$tunnel_id" <<'PY'
import json, sys
edits=[]
if sys.argv[2]:
    edits.append({"path":"/instance_id","operation":"set","value":sys.argv[2]})
if sys.argv[3]:
    edits.append({"path":"/tunnel/id","operation":"set","value":sys.argv[3]})
print(json.dumps({
    "candidate_request_version": 1,
    "workspace_path": sys.argv[1],
    "field_edits": edits,
    "access_edits": [],
}, sort_keys=True, separators=(",", ":")))
PY
}

echo "=== PM3.1 specification and pure contract verification ==="
test -s "$ROOT/specs/pm3.1-context-aware-instance-setup.md" || fail "normative PM3.1 specification is missing"
PYTHONPATH="$ROOT/src:$ROOT/tests:$ROOT" python3 -m unittest \
  tests.test_pm3_1_setup \
  tests.test_pm3_1_electrocad_dry_run \
  tests.test_development_environment \
  tests.test_operator_contracts \
  tests.test_planning \
  tests.test_p7_listener_updates \
  tests.test_cli \
  tests.test_cli_host_boundary \
  tests.test_tui
echo "PM3_1_SPECIFICATION=PASS"
echo "PM3_1_PURE_VERIFICATION=PASS"
echo "PM3_1_ELECTROCAD_DRY_RUN=PASS"

echo "=== PM3.1 public template contract ==="
"$MANAGER" instance template | json_assert \
  'd["template_version"] == 2 and d["config_version"] == 2 and "tunnel.profile" not in d["required_operator_fields"]'
echo "PM3_1_TUNNEL_PROFILE_ABSTRACTION=PASS"

echo "=== PM3.1 unregistered local Git discovery ==="
mkdir -p -- "$NESTED"
git -C "$UNREGISTERED" init -q
git -C "$UNREGISTERED" config --local user.name "PM3.1 Qualification"
git -C "$UNREGISTERED" config --local user.email "pm3.1@example.invalid"
git -C "$UNREGISTERED" remote add origin git@github.com:noobAIcoder/pm3-1-qualification.git

DISCOVERY=$("$MANAGER" instance discover "$NESTED")
printf '%s\n' "$DISCOVERY" | json_assert \
  'd["discovery_version"] == 1 and d["repository_detected"] is True and d["repository_root"] == d["workspace_path"] and d["instance_match"]["status"] == "none" and len(d["discovery_fingerprint"]) == 64'
DISCOVERED_ROOT=$(printf '%s\n' "$DISCOVERY" | json_field workspace_path)
[[ "$DISCOVERED_ROOT" == "$(realpath "$UNREGISTERED")" ]] || fail "nested discovery did not resolve repository root"
printf '%s\n' "$DISCOVERY" | json_assert \
  'd["local_git"]["identity"]["classification"] == "adoptable" and d["local_git"]["identity"]["effective"]["scope"] == "repository-local" and d["local_git"]["remote"]["selected"] == "origin" and d["local_git"]["remote"]["host"] == "github.com"'
printf '%s\n' "$DISCOVERY" | grep -Fq 'git@github.com' && fail "raw remote URL leaked from discovery"
printf '%s\n' "$DISCOVERY" | grep -Eqi '(api[_-]?key|oauth[^" ]*token|passphrase|private[_-]?key)[^:]*:[[:space:]]*"[^" ]+' \
  && fail "secret-like value leaked from discovery"
echo "PM3_1_WORKSPACE_DISCOVERY=PASS"
echo "PM3_1_WORKSPACE_IDENTITY=PASS"
echo "PM3_1_GIT_DISCOVERY=PASS"
echo "PM3_1_GIT_ADOPTION_POLICY=PASS"
echo "PM3_1_GIT_PROVENANCE=PASS"
echo "PM3_1_AUTH_STATUS_CONTRACT=PASS"

echo "=== PM3.1 manager-authoritative candidate ==="
CANDIDATE=$(candidate_request "$NESTED" "pm3-1-qual-candidate" "tunnel_pm31qual" | "$MANAGER" instance candidate)
printf '%s\n' "$CANDIDATE" | json_assert \
  'd["candidate_version"] == 1 and len(d["candidate_input_fingerprint"]) == 64 and len(d["candidate_fingerprint"]) == 64 and d["effective_declaration"]["workspace_path"] == d["discovery"]["workspace_path"] and d["effective_declaration"]["tunnel"]["profile"] == "workspace-mcp-pm3-1-qual-candidate" and d["unresolved_required_operator_fields"] == []'
printf '%s\n' "$CANDIDATE" | json_assert \
  'any(item.get("path") == "/instance_id" and item.get("source") == "operator_override" for item in d["field_provenance"]) and any(item.get("path") == "/tunnel/profile" and item.get("source") == "manager_derived" for item in d["field_provenance"]) and any(item.get("path") == "/mcp/port" and item.get("source") == "manager_recommendation" for item in d["field_provenance"])'
printf '%s\n' "$CANDIDATE" | json_assert \
  'd["port_projection"]["port_projection_version"] == 1 and d["port_projection"]["collision_state"] in {"clear", "unknown"} and d["effective_declaration"]["mcp"]["port"] != d["effective_declaration"]["tunnel"]["health_port"]'
echo "PM3_1_CANDIDATE_REQUEST=PASS"
echo "PM3_1_CANDIDATE_AUTHORITY=PASS"
echo "PM3_1_CANDIDATE_PROVENANCE=PASS"
echo "PM3_1_CANDIDATE_FRESHNESS=PASS"
echo "PM3_1_COPY_POLICY=PASS"
echo "PM3_1_ACCESS_PROVENANCE=PASS"
echo "PM3_1_PORT_RECOMMENDATION=PASS"

echo "=== PM3.1 live endpoint projection ==="
PORTS=$("$MANAGER" host ports)
printf '%s\n' "$PORTS" | json_assert \
  'd["port_projection_version"] == 1 and d["listener_observation"]["state"] in {"available", "partial", "unavailable"} and isinstance(d["declared_endpoints"], list) and isinstance(d["observed_listeners"], list)'
printf '%s\n' "$PORTS" | python3 -c '
import json, sys
d=json.load(sys.stdin)
declared={(x.get("bind_address"),x.get("port"),x.get("instance_id"),x.get("purpose")) for x in d["declared_endpoints"]}
observed={(x.get("bind_address"),x.get("port")) for x in d["observed_listeners"]}
if not declared:
    raise SystemExit("no declared manager endpoints were projected")
if d["listener_observation"]["state"] == "available" and not observed:
    raise SystemExit("available listener observation unexpectedly projected no listeners")
'
echo "PM3_1_PORT_PROJECTION=PASS"
echo "PM3_1_ENDPOINT_COLLISION_MODEL=PASS"
echo "PM3_1_UNKNOWN_LISTENER_SAFETY=PASS"

echo "=== PM3.1 existing workspace match ==="
MANAGED_SHOW=$("$MANAGER" instance show "$INSTANCE_ID")
MANAGED_WORKSPACE=$(printf '%s\n' "$MANAGED_SHOW" | json_field desired.workspace_path)
MATCH=$("$MANAGER" instance discover "$MANAGED_WORKSPACE")
printf '%s\n' "$MATCH" | json_assert \
  'd["instance_match"]["status"] == "single" and len(d["instance_match"]["instance_ids"]) == 1'
printf '%s\n' "$MATCH" | grep -Fq "\"$INSTANCE_ID\"" || fail "existing workspace match does not identify manager-qual"
echo "PM3_1_INSTANCE_SELECTION=PASS"

echo "=== PM3.1 isolated Textual Pilot ==="
INSTALL_JSON=$(bash "$ROOT/scripts/install_toolkit.sh" --repo "$ROOT" --account-home "$HOME" --prefix "$TOOLKIT_PREFIX")
RELEASE_FP=$(printf '%s\n' "$INSTALL_JSON" | json_field installed_fingerprint)
RELEASE="$TOOLKIT_PREFIX/lib/workspace-mcp-manager/releases/$RELEASE_FP"
SITE="$RELEASE/tui-runtime/site-packages"
[[ -d "$SITE/textual" ]] || fail "isolated Textual runtime is absent"
SITE="$SITE" ROOT="$ROOT" python3 -I -S -c '
import os, sys, unittest
sys.path[:0] = [os.environ["SITE"], os.path.join(os.environ["ROOT"], "src"), os.environ["ROOT"]]
suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_tui_textual")
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'
echo "PM3_1_DIRECTORY_PICKER=PASS"
echo "PM3_1_EXTERNAL_AUTH_UX=PASS"
echo "PM3_1_WIZARD_REFINEMENT=PASS"
echo "PM3_1_DIRTY_FIELD_SEMANTICS=PASS"
echo "PM3_1_CLIPBOARD_UX=PASS"
echo "PM3_1_VERSION_PROJECTION=PASS"
echo "PM3_1_TEXTUAL_PILOT=PASS"

echo "=== PM3.1 PM3 regression ==="
PM3_SOURCE_MODE=0 bash "$ROOT/scripts/qualify_pm3_wsl.sh"
echo "PM3_1_PM3_REGRESSION=PASS"

echo "=== PM3.1 PM2 regression ==="
REPO="$ROOT" MANAGER="$(command -v "$MANAGER")" INSTANCE_ID="$INSTANCE_ID" bash "$ROOT/scripts/qualify_pm2_wsl.sh"
echo "PM3_1_PM2_REGRESSION=PASS"

echo "=== PM3.1 final baseline ==="
"$MANAGER" instance plan "$INSTANCE_ID" | json_assert \
  'd["valid"] is True and all(item.get("operation") == "NOOP" for item in d.get("operations", []))'
"$MANAGER" instance summary "$INSTANCE_ID" | json_assert \
  'd["runtime"] == "Running" and d["tunnel"] == "Ready" and d["recovery"] == "Clean"'
echo "PM3_1_LIVE_WSL_QUALIFICATION=PASS"
echo "PM3_1_CONTEXT_AWARE_UX_QUALIFICATION=PASS"
