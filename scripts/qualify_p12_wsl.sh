#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:-manager-qual}"
MANAGER="${MANAGER:-$HOME/.local/bin/workspace-mcp-manager}"
MCP_PORT="${MCP_PORT:-7657}"
TUNNEL_HEALTH_PORT="${TUNNEL_HEALTH_PORT:-7373}"
SHORT_WINDOW="${SHORT_WINDOW:-60}"
LONG_WINDOW="${LONG_WINDOW:-40000}"
MCP_UNIT="coding-tools-mcp-$INSTANCE_ID.service"
TUNNEL_UNIT="tunnel-client-$INSTANCE_ID.service"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

[ -x "$MANAGER" ] || fail "manager executable is unavailable: $MANAGER"

TMP_ROOT="$(mktemp -d -t workspace-mcp-p12-XXXXXX)"
SHORT_JSON="$TMP_ROOT/short.json"
LONG_JSON="$TMP_ROOT/long.json"
SAT_JSON="$TMP_ROOT/saturated.json"
SESSIONS="$TMP_ROOT/sessions.txt"

delete_sessions() {
  local file=$1
  [ -f "$file" ] || return 0
  python3 - "$MCP_PORT" "$file" <<'PY' >/dev/null 2>&1 || true
import pathlib, sys, urllib.request
port=int(sys.argv[1]); path=pathlib.Path(sys.argv[2])
for sid in path.read_text(encoding="utf-8", errors="replace").splitlines():
    if not sid: continue
    req=urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        method="DELETE",
        headers={"Mcp-Session-Id":sid,"Accept":"application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass
PY
}

cleanup() {
  local rc=$?
  delete_sessions "$SESSIONS"
  rm -rf "$TMP_ROOT"
  return "$rc"
}
trap cleanup EXIT

SHOW_JSON="$TMP_ROOT/show.json"
"$MANAGER" instance show "$INSTANCE_ID" >"$SHOW_JSON"
python3 - "$SHOW_JSON" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
d=p["desired"]
if d["recovery"]["admission_guard_enabled"]:
    raise SystemExit("P12_FAIL: admission guard must be disabled during P12 saturation qualification")
if d["access"]["read_only"] or d["access"]["read_write"]:
    raise SystemExit("P12_FAIL: qualification must start from no-access baseline")
PY

MCP_PID_BASE="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
TUNNEL_PID_BASE="$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)"
[ "$MCP_PID_BASE" -gt 0 ] || fail "MCP is not running"
[ "$TUNNEL_PID_BASE" -gt 0 ] || fail "tunnel is not running"

"$MANAGER" instance diagnose "$INSTANCE_ID" --since-seconds "$SHORT_WINDOW" >"$SHORT_JSON"
python3 - "$SHORT_JSON" "$MCP_PID_BASE" "$TUNNEL_PID_BASE" <<'PY'
import json,os,re,sys
p=json.load(open(sys.argv[1])); s=p["snapshot"]
mpid=int(sys.argv[2]); tpid=int(sys.argv[3])
def require(cond,msg):
    if not cond: raise SystemExit(f"P12_FAIL: {msg}")
require(p.get("ok") is True,"healthy diagnostic returned ok=false")
path=p.get("snapshot_path")
require(isinstance(path,str) and os.path.isfile(path),"snapshot was not persisted")
require(os.stat(path).st_mode & 0o777 == 0o600,"snapshot mode is not 0600")
require(s["cgroups"].get("separate") is True,"MCP/tunnel cgroups are not distinct")
for name,pid in (("mcp",mpid),("tunnel",tpid)):
    svc=s["services"][name]
    require(svc.get("ActiveState")=="active" and svc.get("SubState")=="running",f"{name} service not active/running")
    require(int(svc.get("MainPID",0))==pid,f"{name} main PID changed during healthy diagnostic")
    processes=s["cgroups"][name].get("processes",[])
    main=next((x for x in processes if int(x.get("pid",0))==pid),None)
    require(main is not None,f"{name} main process missing from cgroup")
    require(isinstance(main.get("rss_kb"),int) and main["rss_kb"]>0,f"{name} RSS missing")
    require(isinstance(main.get("pss_kb"),int) and main["pss_kb"]>0,f"{name} PSS missing")
    require(isinstance(main.get("age_seconds"),(int,float)),f"{name} process age missing")
    require(isinstance(main.get("cgroup"),str) and main["cgroup"],f"{name} process cgroup membership missing")
require(s["host"]["meminfo"].get("available") is True,"/proc/meminfo unavailable")
require("MemTotal" in s["host"]["meminfo"].get("values",{}),"MemTotal missing")
for name in ("cpu","memory","io"):
    require(s["host"]["psi"][name].get("available") is True,f"{name} PSI unavailable")
disc=s["probes"]["mcp"]["discovery"]
init=s["probes"]["mcp"]["initialize"]
require(disc.get("status")==200 and disc.get("protocol_version")=="2025-11-25","MCP discovery probe failed")
require(init.get("status")==200 and init.get("protocol_version")=="2025-11-25","MCP initialize probe failed")
require(init.get("session_cleanup",{}).get("ok") is True,"diagnostic MCP session cleanup failed")
require(s["probes"]["tunnel"]["health"].get("text")=="live","tunnel health probe failed")
require(s["probes"]["tunnel"]["readiness"].get("text")=="ready","tunnel readiness probe failed")
text=open(path,encoding="utf-8").read()
patterns=(
    r"(?i)CONTROL_PLANE_API_KEY\s*=\s*(?!<REDACTED>)[^\s\"']+",
    r"(?i)OPENAI_API_KEY\s*=\s*(?!<REDACTED>)[^\s\"']+",
    r"(?i)GITHUB_TOKEN\s*=\s*(?!<REDACTED>)[^\s\"']+",
    r"(?i)GH_TOKEN\s*=\s*(?!<REDACTED>)[^\s\"']+",
    r"(?i)Bearer\s+(?!<REDACTED>)[A-Za-z0-9._~+/=-]+",
)
require(not any(re.search(pattern,text) for pattern in patterns),"snapshot contains credential material")
PY

"$MANAGER" instance diagnose "$INSTANCE_ID" --since-seconds "$LONG_WINDOW" >"$LONG_JSON"
python3 - "$SHORT_JSON" "$LONG_JSON" "$SHORT_WINDOW" <<'PY'
from datetime import datetime
import json,sys
short=json.load(open(sys.argv[1]))["snapshot"]
long=json.load(open(sys.argv[2]))["snapshot"]
window=int(sys.argv[3])
def require(cond,msg):
    if not cond: raise SystemExit(f"P12_FAIL: {msg}")
require("tunnel_poll_timeout" in long.get("classifications",[]),"retained poll-timeout history was not classified")
captured=datetime.fromisoformat(long["captured_at"])
older=[]
for event in long.get("recent_tunnel_events",[]):
    if not event.get("poll_timeout"): continue
    ts=datetime.fromisoformat(event["time"])
    if (captured-ts).total_seconds()>window:
        older.append(event["time"])
require(older,"long window contains no poll timeout older than short window")
short_times={event.get("time") for event in short.get("recent_tunnel_events",[])}
require(any(ts not in short_times for ts in older),"stale poll timeout leaked into short-window events")
require("tunnel_poll_timeout" in long["incident_domains"]["tunnel_control_plane"],"poll timeout domain classification missing")
PY

python3 - "$MCP_PORT" "$SESSIONS" <<'PY'
import json,pathlib,sys,urllib.error,urllib.request
port=int(sys.argv[1]); out=pathlib.Path(sys.argv[2])
payload=json.dumps({
    "jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"p12-qualification","version":"1"}},
},separators=(",",":")).encode()
sessions=[]
for _ in range(256):
    req=urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",data=payload,method="POST",
        headers={"Content-Type":"application/json","Accept":"application/json"},
    )
    try:
        with urllib.request.urlopen(req,timeout=3) as response:
            sid=response.headers.get("Mcp-Session-Id")
            if response.status!=200 or not sid:
                raise SystemExit(f"P12_FAIL: unexpected initialize response status={response.status} sid={sid!r}")
            sessions.append(sid)
    except urllib.error.HTTPError as exc:
        body=exc.read().decode("utf-8",errors="replace")
        try: data=json.loads(body)
        except Exception: data={}
        err=data.get("error") if isinstance(data,dict) else None
        if exc.code==503 and isinstance(err,dict) and err.get("code")==-32000 and err.get("message")=="maximum HTTP session count reached":
            out.write_text("\n".join(sessions)+"\n",encoding="utf-8")
            print(f"P12_LOCAL_SATURATION=PASS sessions={len(sessions)}")
            raise SystemExit(0)
        raise SystemExit(f"P12_FAIL: unexpected admission failure status={exc.code} body={body!r}")
raise SystemExit("P12_FAIL: MCP session limit was not reached")
PY

MCP_PID_SAT_BEFORE="$(systemctl --user show "$MCP_UNIT" -p MainPID --value)"
TUNNEL_PID_SAT_BEFORE="$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)"
"$MANAGER" instance diagnose "$INSTANCE_ID" --since-seconds "$SHORT_WINDOW" >"$SAT_JSON"
python3 - "$SAT_JSON" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))["snapshot"]
def require(cond,msg):
    if not cond: raise SystemExit(f"P12_FAIL: {msg}")
require("local_mcp_5xx" in s.get("classifications",[]),"local MCP 5xx was not classified")
require("local_mcp_5xx" in s["incident_domains"]["local_mcp"],"local MCP domain does not contain local_mcp_5xx")
require(s["probes"]["mcp"]["initialize"].get("status")==503,"saturated initialize probe did not record HTTP 503")
require("tunnel_control_plane_5xx" not in s["incident_domains"]["local_mcp"],"tunnel label leaked into local MCP domain")
PY

[ "$(systemctl --user show "$MCP_UNIT" -p MainPID --value)" = "$MCP_PID_SAT_BEFORE" ] || fail "diagnostic restarted MCP"
[ "$(systemctl --user show "$TUNNEL_UNIT" -p MainPID --value)" = "$TUNNEL_PID_SAT_BEFORE" ] || fail "diagnostic restarted tunnel"

delete_sessions "$SESSIONS"
: >"$SESSIONS"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$MCP_PORT/.well-known/mcp.json" >/dev/null \
     && [ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/healthz")" = live ] \
     && [ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/readyz")" = ready ]; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$MCP_PORT/.well-known/mcp.json" >/dev/null || fail "MCP discovery did not recover after session cleanup"
[ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/healthz")" = live ] || fail "tunnel health not live after cleanup"
[ "$(curl -fsS "http://127.0.0.1:$TUNNEL_HEALTH_PORT/readyz")" = ready ] || fail "tunnel readiness not ready after cleanup"

PLAN="$TMP_ROOT/plan.json"
"$MANAGER" instance plan "$INSTANCE_ID" >"$PLAN"
python3 - "$PLAN" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
if not p.get("valid") or any(item.get("operation")!="NOOP" for item in p.get("operations",[])):
    raise SystemExit("P12_FAIL: final manager plan is not all NOOP")
PY
"$MANAGER" instance apply "$INSTANCE_ID" >/dev/null

cleanup
trap - EXIT
printf 'P12_RESILIENCE_DIAGNOSTICS_QUALIFICATION=PASS\n'
