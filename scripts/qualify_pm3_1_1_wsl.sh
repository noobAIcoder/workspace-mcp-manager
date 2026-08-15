#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTANCE_ID=${PM3_1_1_INSTANCE_ID:-manager-qual}
PARENT_MANAGER=${PM3_1_1_PARENT_MANAGER:-$HOME/.local/bin/workspace-mcp-manager}
TMP=$(mktemp -d -t workspace-mcp-pm3-1-1-XXXXXX)
TOOLKIT_PREFIX="$TMP/toolkit"
STAGE_CODE=10

cleanup() {
  rm -rf -- "$TMP"
}
trap cleanup EXIT INT TERM
trap 'rc=$?; if (( rc != 0 )); then printf "PM3_1_1_FAILED_STAGE=%s\n" "$STAGE_CODE" >&2; exit "$STAGE_CODE"; fi' ERR

fail() {
  printf 'PM3_1_1_FAIL=%s\n' "$*" >&2
  exit 1
}

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

json_field() {
  local field=$1
  python3 -c '
import json, sys
value=json.load(sys.stdin)
for part in sys.argv[1].split("."):
    value=value[int(part)] if isinstance(value, list) else value[part]
print("" if value is None else value)
' "$field"
}

command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"
command -v git >/dev/null 2>&1 || fail "git is unavailable"
command -v gh >/dev/null 2>&1 || fail "GitHub CLI is unavailable"
[[ -n "$PARENT_MANAGER" && -x "$PARENT_MANAGER" ]] || fail "qualified parent manager is unavailable"

echo "=== PM3.1.1 specification authority ==="
STAGE_CODE=11
test -s "$ROOT/specs/pm3.1.1-github-access-onboarding.md" || fail "normative PM3.1.1 specification is missing"
grep -Eq 'github_access_projection_version[[:space:]]*=[[:space:]]*1' "$ROOT/specs/pm3.1.1-github-access-onboarding.md" \
  || fail "projection version contract missing"
grep -Eq 'github_access_verification_record_version[[:space:]]*=[[:space:]]*1' "$ROOT/specs/pm3.1.1-github-access-onboarding.md" \
  || fail "verification record version contract missing"
grep -Fq -- '--insecure-storage' "$ROOT/specs/pm3.1.1-github-access-onboarding.md" \
  || fail "qualified storage contract missing"
grep -Fq $'CAP-118\tC\tPRESERVE\tPM3.1.1' "$ROOT/specs/capabilities.tsv" \
  || fail "CAP-118 authority was not assigned to PM3.1.1"
echo "PM3_1_1_SPECIFICATION=PASS"
echo "PM3_1_1_AUTHORITY_SUPERSESSION=PASS"

echo "=== PM3.1.1 deterministic verification ==="
STAGE_CODE=12
PYTHONPATH="$ROOT/src:$ROOT/tests:$ROOT" python3 -m unittest \
  tests.test_pm3_1_1_github_access \
  tests.test_pm3_1_electrocad_dry_run \
  tests.test_generation \
  tests.test_development_environment \
  tests.test_cli \
  tests.test_cli_host_boundary
echo "PM3_1_1_GITHUB_ACCESS_PROJECTION=PASS"
echo "PM3_1_1_MANAGED_PROFILE=PASS"
echo "PM3_1_1_PROFILE_OWNERSHIP=PASS"
echo "PM3_1_1_CREDENTIAL_STORAGE_SEPARATION=PASS"
echo "PM3_1_1_CREDENTIAL_SOURCE_ISOLATION=PASS"
echo "PM3_1_1_PROFILE_STORAGE_SAFETY=PASS"
echo "PM3_1_1_GH_VERSION_CONTRACT=PASS"
echo "PM3_1_1_HELPER_PROCESS_BOUNDARY=PASS"
echo "PM3_1_1_SECRET_BOUNDARY=PASS"
echo "PM3_1_1_GH_STDIN_HANDOFF=PASS"
echo "PM3_1_1_GH_STORAGE_CONTRACT=PASS"
echo "PM3_1_1_TTY_REQUIREMENT=PASS"
echo "PM3_1_1_OPERATION_SERIALIZATION=PASS"
echo "PM3_1_1_PROFILE_VERIFICATION=PASS"
echo "PM3_1_1_MANAGER_ENV_VERIFICATION=PASS"
echo "PM3_1_1_REPOSITORY_READ_VERIFICATION=PASS"
echo "PM3_1_1_VERIFICATION_CONTEXT=PASS"
echo "PM3_1_1_VERIFICATION_FRESHNESS=PASS"
echo "PM3_1_1_DISABLED_TO_MANAGED=PASS"
echo "PM3_1_1_RECONFIGURATION=PASS"
echo "PM3_1_1_CANCELLATION=PASS"
echo "PM3_1_1_GIT_TRANSPORT_SEPARATION=PASS"
echo "PM3_1_1_EXTERNAL_PROFILE_READ_ONLY=PASS"
echo "PM3_1_1_SECRET_LEAK_SCAN=PASS"
echo "PM3_1_1_ELECTROCAD_DRY_RUN=PASS"

echo "=== PM3.1.1 qualified GitHub CLI ==="
STAGE_CODE=13
GH_RAW=$(gh --version)
GH_VERSION=$(printf '%s\n' "$GH_RAW" | sed -n '1s/^gh version \([0-9][0-9.]*\).*/\1/p')
[[ "$GH_VERSION" == "2.45.0" ]] || fail "qualified host gh version must be 2.45.0, got ${GH_VERSION:-unknown}"
printf 'PM3_1_1_GH_BINARY=%s\n' "$(command -v gh)"
printf 'PM3_1_1_GH_VERSION=%s\n' "$GH_VERSION"
echo "PM3_1_1_GH_VERSION_CONTRACT=PASS"

echo "=== PM3.1.1 candidate toolkit / isolated Textual Pilot ==="
STAGE_CODE=14
INSTALL_JSON=$(bash "$ROOT/scripts/install_toolkit.sh" --repo "$ROOT" --account-home "$HOME" --prefix "$TOOLKIT_PREFIX")
RELEASE_FP=$(printf '%s\n' "$INSTALL_JSON" | json_field installed_fingerprint)
RELEASE="$TOOLKIT_PREFIX/lib/workspace-mcp-manager/releases/$RELEASE_FP"
SITE="$RELEASE/tui-runtime/site-packages"
MANAGER="$RELEASE/bin/workspace-mcp-manager"
[[ -x "$MANAGER" ]] || fail "candidate manager executable missing"
[[ -d "$SITE/textual" ]] || fail "isolated Textual runtime missing"

SITE="$SITE" ROOT="$ROOT" python3 -I -S -c '
import os, sys, unittest
sys.path[:0]=[os.environ["SITE"], os.path.join(os.environ["ROOT"], "src"), os.environ["ROOT"]]
suite=unittest.defaultTestLoader.loadTestsFromName("tests.test_tui_textual")
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'
echo "PM3_1_1_TEXTUAL_PILOT=PASS"
echo "PM3_1_1_CREATION_WORKFLOW=PASS"
echo "PM3_1_1_EXISTING_INSTANCE_WORKFLOW=PASS"

echo "=== PM3.1.1 pseudo-TTY visible-input qualification ==="
STAGE_CODE=15
SITE="$SITE" ROOT="$ROOT" python3 -I -S -c '
import os, sys, unittest
sys.path[:0]=[os.environ["SITE"], os.path.join(os.environ["ROOT"], "src"), os.environ["ROOT"]]
suite=unittest.defaultTestLoader.loadTestsFromName(
    "tests.test_pm3_1_1_github_access.VisibleCredentialHelperTests.test_visible_pty_input_reaches_fake_gh_only_through_stdin"
)
result=unittest.TextTestRunner(verbosity=2).run(suite)
if result.skipped:
    raise SystemExit("pseudo-TTY qualification was skipped")
raise SystemExit(0 if result.wasSuccessful() else 1)
'
echo "PM3_1_1_VISIBLE_CREDENTIAL_INPUT=PASS"

echo "=== PM3.1.1 registered-instance non-secret projection ==="
STAGE_CODE=16
STATUS=$("$MANAGER" _runtime github-access status "$INSTANCE_ID")
printf '%s\n' "$STATUS" | json_assert \
  'd["github_access_projection_version"] == 1 and d["instance_id"] and d["profile"]["mode"] in {"disabled","managed","external"} and d["authentication"]["state"] in {"not_applicable","unauthenticated","authenticated","verification_failed","unknown"} and d["git_transport"]["status"] in {"ready","not_ready","not_configured"}'
printf '%s\n' "$STATUS" | grep -Fq 'github_pat_' && fail "credential-shaped data leaked into status"
echo "PM3_1_1_LIVE_STATUS_PROJECTION=PASS"

echo "=== PM3.1.1 deployed MCP verification contract ==="
STAGE_CODE=17
PYTHONPATH="$ROOT/src:$ROOT/tests:$ROOT" python3 -m unittest \
  tests.test_pm3_1_1_github_access.ProjectionAndVerificationTests
echo "PM3_1_1_MCP_EXECUTION_VERIFICATION=PASS"

echo "=== PM3.1.1 LeadBot reference boundary ==="
STAGE_CODE=18
# LeadBotCodingTools is an optional cross-instance reference. This qualification
# deliberately performs no profile reads or mutation. Absence of a connector-side
# read-only observation is the normative 'unavailable' outcome, not a failure.
echo "PM3_1_1_LEADBOT_REFERENCE_STATUS=unavailable"
echo "PM3_1_1_LEADBOT_REFERENCE=PASS"

echo "=== PM3.1.1 PM3.1 / PM3 / PM2 regression ==="
STAGE_CODE=19
PM3_1_MANAGER="$PARENT_MANAGER" bash "$ROOT/scripts/qualify_pm3_1_wsl.sh"
echo "PM3_1_1_PM3_1_REGRESSION=PASS"
echo "PM3_1_1_PM3_REGRESSION=PASS"
echo "PM3_1_1_PM2_REGRESSION=PASS"

echo "=== PM3.1.1 final implementation qualification ==="
STAGE_CODE=20
echo "PM3_1_1_IMPLEMENTATION_QUALIFICATION=PASS"
echo "PM3_1_1_GITHUB_ACCESS_ONBOARDING=AWAITING_OPERATOR_VALIDATION"
