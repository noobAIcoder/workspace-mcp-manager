#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
REPO="${REPO:-/home/cloudtoor/repos/workspace-mcp-manager}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
INSTALLER="$REPO/scripts/install_toolkit.py"
NODE_ROOT="${NODE_ROOT:-$HOME/.nvm/versions/node/v24.15.0}"

fail() {
  printf 'P15_FAIL=%s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable"
[ -f "$INSTALLER" ] || fail "standalone toolkit installer is unavailable"
[ -d "$REPO/src/workspace_mcp_manager" ] || fail "manager source package is unavailable"

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

python3 "$INSTALLER" --repo "$REPO" --account-home "$HOME" --check >"$TMP_ROOT/real-check.json"
python3 - "$TMP_ROOT/real-check.json" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert p["instances_valid"] is True, p
assert p["instance_count"] >= 1, p
assert isinstance(p["candidate_fingerprint"], str) and len(p["candidate_fingerprint"]) == 64
PY
printf 'P15_REAL_TOOLKIT_CHECK=PASS\n'

ISO_HOME="$TMP_ROOT/home"
ISO_PREFIX="$ISO_HOME/.local"
REPO1="$TMP_ROOT/repo1"
REPO2="$TMP_ROOT/repo2"
mkdir -p "$REPO1" "$REPO2"
cp "$REPO/pyproject.toml" "$REPO1/"
cp -a "$REPO/src" "$REPO1/"
cp "$REPO/pyproject.toml" "$REPO2/"
cp -a "$REPO/src" "$REPO2/"

python3 "$INSTALLER" --repo "$REPO1" --account-home "$ISO_HOME" --prefix "$ISO_PREFIX" \
  >"$TMP_ROOT/install-1.json"
"$ISO_PREFIX/bin/workspace-mcp-manager" --help >/dev/null
python3 "$INSTALLER" --repo "$REPO1" --account-home "$ISO_HOME" --prefix "$ISO_PREFIX" \
  >"$TMP_ROOT/install-noop.json"
OLD_FINGERPRINT="$(python3 - "$TMP_ROOT/install-noop.json" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["installed_fingerprint"])
PY
)"

printf '\n# p15-live-upgrade-candidate\n' >>"$REPO2/src/workspace_mcp_manager/domain.py"
set +e
python3 "$INSTALLER" --repo "$REPO2" --account-home "$ISO_HOME" --prefix "$ISO_PREFIX" \
  >"$TMP_ROOT/rejected.out" 2>"$TMP_ROOT/rejected.err"
REJECT_RC=$?
set -e
[ "$REJECT_RC" -eq 2 ] || fail "toolkit replacement did not require explicit upgrade"
grep -F 'requires explicit --upgrade' "$TMP_ROOT/rejected.err" >/dev/null \
  || fail "toolkit replacement rejection reason is wrong"

python3 "$INSTALLER" --repo "$REPO2" --account-home "$ISO_HOME" --prefix "$ISO_PREFIX" --upgrade \
  >"$TMP_ROOT/install-upgrade.json"
NEW_FINGERPRINT="$(python3 - "$TMP_ROOT/install-upgrade.json" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["installed_fingerprint"])
PY
)"
[ "$OLD_FINGERPRINT" != "$NEW_FINGERPRINT" ] || fail "explicit toolkit upgrade did not switch fingerprint"
"$ISO_PREFIX/bin/workspace-mcp-manager" --help >/dev/null
printf 'P15_TOOLKIT_INITIAL_INSTALL=PASS\n'
printf 'P15_TOOLKIT_NOOP=PASS\n'
printf 'P15_TOOLKIT_EXPLICIT_UPGRADE_GATE=PASS\n'
printf 'P15_TOOLKIT_ATOMIC_SWITCH=PASS\n'

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
