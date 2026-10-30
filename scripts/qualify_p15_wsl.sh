#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
NODE_ROOT="${NODE_ROOT:-$HOME/.nvm/versions/node/v24.15.0}"

fail() {
  printf 'P15_FAIL=%s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable"

TMP_ROOT="$(mktemp -d -t workspace-mcp-p15-XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT

SHOW="$TMP_ROOT/show.json"
ACCESS="$TMP_ROOT/access.json"
"$MANAGER" instance show "$INSTANCE_ID" >"$SHOW"
"$MANAGER" access list "$INSTANCE_ID" >"$ACCESS"
BASELINE_FINGERPRINT="$(python3 - "$SHOW" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["fingerprint"])
PY
)"
python3 - "$ACCESS" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["entries"] == [], p
PY
[ ! -e "$HOME/repos/workspace-mcp-manager-qual/.workspace-mcp-access" ] \
  || fail "manager-qual must begin P15 with no external access root"

# Manager distribution installation/upgrade is intentionally absent here.
# `specs/standalone-distribution.md` supersedes P15 shared-toolkit authority.
printf 'P15_DISTRIBUTION_AUTHORITY=SUPERSEDED_BY_DIST\n'

"$MANAGER" host tools audit >"$TMP_ROOT/tools-audit.json"
python3 - "$TMP_ROOT/tools-audit.json" "$NODE_ROOT" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
root=sys.argv[2]
assert p["ok"] is True
for name in ("uv", "coding-tools-mcp", "tunnel-client", "gh-system"):
    assert p["tools"][name]["present"] is True, (name, p["tools"][name])
matches=[item for item in p["node_roots"] if item["root"] == root]
assert len(matches) == 1
selected=matches[0]
assert selected["node"]["version"] == "v24.15.0", selected
assert selected["npm"]["version"] is not None, selected
assert selected["codex"]["version"] == "codex-cli 0.147.0", selected
agents=p["agents"]
assert agents["ssh_agent_binary"] is True
assert agents["gpg_agent_binary"] is True
assert agents["gpgconf_binary"] is True
PY
printf 'P15_TOOL_AUDIT_COMMAND=PASS\n'

for state in "$HOME/.local/state/workspace-mcp-manager/tooling/codex.json" \
             "$HOME/.local/state/workspace-mcp-manager/tooling/gh.json"; do
  [ -f "$state" ] || fail "qualified tooling state missing: $state"
  [ "$(stat -c '%a' "$state")" = 600 ] || fail "tooling state mode is not 0600: $state"
  ! grep -E '(CONTROL_PLANE_API_KEY|OPENAI_API_KEY|GH_TOKEN|GITHUB_TOKEN|-----BEGIN .*PRIVATE KEY-----)' "$state" >/dev/null \
    || fail "tooling state contains credential material: $state"
done
printf 'P15_TOOLING_STATE=PASS\n'

"$MANAGER" instance plan "$INSTANCE_ID" >"$TMP_ROOT/final-plan.json"
python3 - "$TMP_ROOT/final-plan.json" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["valid"] is True
assert {item["operation"] for item in p["operations"]} == {"NOOP"}
PY
FINAL_FINGERPRINT="$("$MANAGER" instance show "$INSTANCE_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"
[ "$FINAL_FINGERPRINT" = "$BASELINE_FINGERPRINT" ] || fail "P15 changed manager-qual desired fingerprint"

"$MANAGER" access list "$INSTANCE_ID" >"$TMP_ROOT/final-access.json"
python3 - "$TMP_ROOT/final-access.json" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["entries"] == [], p
PY
[ ! -e "$HOME/repos/workspace-mcp-manager-qual/.workspace-mcp-access" ] \
  || fail "P15 left manager-qual external access root"

curl -fsS http://127.0.0.1:7657/.well-known/mcp.json >/dev/null || fail "MCP discovery regression"
[ "$(curl -fsS http://127.0.0.1:7373/healthz)" = live ] || fail "tunnel health regression"
[ "$(curl -fsS http://127.0.0.1:7373/readyz)" = ready ] || fail "tunnel readiness regression"
"$MANAGER" instance diagnose "$INSTANCE_ID" --since-seconds 60 >/dev/null

printf 'P15_FINAL_CONVERGENCE=PASS\n'
printf 'P15_WSL_QUALIFICATION=PASS\n'
