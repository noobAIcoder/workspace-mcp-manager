from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError, config_error
from .paths import ManagerPaths
from .redaction import redact_object, redact_text, sanitized_subprocess_env


DIAGNOSTIC_SCHEMA_VERSION = 1
DEFAULT_SINCE_SECONDS = 900
MAX_SINCE_SECONDS = 86400
MAX_RECENT_TUNNEL_EVENTS = 50
MAX_EVENT_TEXT = 1200
FUTURE_CLOCK_SKEW_SECONDS = 5

_HTTP_5XX_RE = re.compile(
    r"(?i)(?:http(?:\s+status)?|status(?:\s+code)?|response(?:\s+status)?|server\s+returned)"
    r"[^0-9]{0,24}(5\d\d)"
)
_RFC3339_LONG_FRACTION_RE = re.compile(r"(\.\d{6})\d+(?=(?:Z|[+-]\d{2}:\d{2})$)")


@dataclass(frozen=True, slots=True)
class HTTPResult:
    status: int | None
    body: bytes
    headers: Mapping[str, str]
    error: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _atomic_write(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            errors="strict",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
        raise ManagerError(ErrorCode.IO_ERROR, f"cannot persist diagnostic snapshot: {path}") from exc


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_rfc3339(value: str) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = _RFC3339_LONG_FRACTION_RE.sub(r"\1", value)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_key_ints(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw = line.partition(" ")
        if not separator:
            continue
        value = _parse_int(raw.strip())
        if value is not None:
            result[key] = value
    return result


def _parse_meminfo(text: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        parts = raw.strip().split()
        if not parts:
            continue
        value = _parse_int(parts[0])
        if value is None:
            continue
        result[key] = {"value": value, "unit": parts[1] if len(parts) > 1 else None}
    return result


def _parse_psi(text: str) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        category = parts[0]
        values: dict[str, float | int] = {}
        for token in parts[1:]:
            key, separator, raw = token.partition("=")
            if not separator:
                continue
            try:
                values[key] = int(raw) if key == "total" else float(raw)
            except ValueError:
                continue
        if values:
            result[category] = values
    return result


def _extract_control_plane_5xx(entry: Mapping[str, Any]) -> int | None:
    for key in ("status", "status_code", "http_status", "response_status"):
        value = _parse_int(entry.get(key))
        if value is not None and 500 <= value <= 599:
            return value
    text = " ".join(str(entry.get(key, "")) for key in ("msg", "error", "reason"))
    match = _HTTP_5XX_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def _is_poll_timeout(entry: Mapping[str, Any]) -> bool:
    if str(entry.get("component", "")).lower() != "controlplane":
        return False
    msg = str(entry.get("msg", "")).lower()
    error = str(entry.get("error", "")).lower()
    if "poll timed out" in msg:
        return True
    if "poll" not in msg:
        return False
    return any(
        token in error
        for token in (
            "timeout",
            "timed out",
            "context deadline exceeded",
            "client.timeout exceeded",
            "i/o timeout",
        )
    )


def _memory_pressure(cgroups: Mapping[str, Mapping[str, Any]]) -> bool:
    for item in cgroups.values():
        if not isinstance(item, Mapping):
            continue
        events = item.get("memory_events")
        if isinstance(events, Mapping):
            if any((_parse_int(events.get(key)) or 0) > 0 for key in ("oom", "oom_kill", "oom_group_kill")):
                return True
        current = _parse_int(item.get("memory_current"))
        maximum = item.get("memory_max")
        max_int = _parse_int(maximum)
        if current is not None and max_int is not None and max_int > 0 and current * 100 >= max_int * 95:
            return True
    return False


def _classify(
    *,
    probes: Mapping[str, Any],
    services: Mapping[str, Mapping[str, Any]],
    cgroups: Mapping[str, Mapping[str, Any]],
    tunnel_events: Sequence[Mapping[str, Any]],
    since_seconds: int,
    host_uptime_seconds: float | None,
) -> tuple[list[str], dict[str, list[str]]]:
    labels: set[str] = set()

    mcp = probes.get("mcp") if isinstance(probes, Mapping) else None
    if isinstance(mcp, Mapping):
        for name in ("discovery", "initialize"):
            item = mcp.get(name)
            if isinstance(item, Mapping):
                status = _parse_int(item.get("status"))
                if status is not None and 500 <= status <= 599:
                    labels.add("local_mcp_5xx")

    for event in tunnel_events:
        if str(event.get("component", "")).lower() != "controlplane":
            continue
        if event.get("control_plane_5xx") is True:
            labels.add("tunnel_control_plane_5xx")
        if event.get("poll_timeout") is True:
            labels.add("tunnel_poll_timeout")

    mcp_service = services.get("mcp", {})
    if isinstance(mcp_service, Mapping):
        active = str(mcp_service.get("ActiveState", ""))
        sub = str(mcp_service.get("SubState", ""))
        main_pid = _parse_int(mcp_service.get("MainPID")) or 0
        if active != "active" or sub != "running" or main_pid <= 0:
            labels.add("mcp_process_down_or_restart")
        elif host_uptime_seconds is not None:
            active_us = _parse_int(mcp_service.get("ActiveEnterTimestampMonotonic"))
            if active_us is not None and active_us > 0:
                age = host_uptime_seconds - (active_us / 1_000_000.0)
                if 0 <= age <= since_seconds:
                    labels.add("mcp_process_down_or_restart")

    if _memory_pressure(cgroups):
        labels.add("memory_pressure")

    ordered = sorted(labels)
    domains = {
        "local_mcp": [label for label in ordered if label in {"local_mcp_5xx", "mcp_process_down_or_restart"}],
        "tunnel_control_plane": [
            label for label in ordered if label in {"tunnel_control_plane_5xx", "tunnel_poll_timeout"}
        ],
        "resource_pressure": [label for label in ordered if label == "memory_pressure"],
    }
    return ordered, domains


class ResilienceDiagnosticService:
    def __init__(
        self,
        paths: ManagerPaths,
        *,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.paths = paths
        self.proc_root = proc_root
        self.cgroup_root = cgroup_root
        self.now = now

    @staticmethod
    def _run(argv: Sequence[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                env=sanitized_subprocess_env(os.environ),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(list(argv), 127, "", redact_text(str(exc)))

    def _systemd_show(self, unit: str) -> dict[str, Any]:
        properties = (
            "Id",
            "LoadState",
            "ActiveState",
            "SubState",
            "Result",
            "ExecMainStatus",
            "MainPID",
            "NRestarts",
            "ControlGroup",
            "ActiveEnterTimestampMonotonic",
            "InactiveEnterTimestampMonotonic",
        )
        argv = ["systemctl", "--user", "show", unit, "--no-pager"]
        for name in properties:
            argv.append(f"--property={name}")
        completed = self._run(argv)
        result: dict[str, Any] = {
            "unit": unit,
            "query_exit_code": completed.returncode,
            "query_error": redact_text(completed.stderr.strip())[-1000:] or None,
        }
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                result[key] = value
        for key in ("MainPID", "NRestarts", "ActiveEnterTimestampMonotonic", "InactiveEnterTimestampMonotonic", "ExecMainStatus"):
            parsed = _parse_int(result.get(key))
            if parsed is not None:
                result[key] = parsed
        return result

    def _read_text(self, path: Path, *, limit: int = 2_000_000) -> tuple[str | None, str | None]:
        try:
            data = path.read_bytes()
        except OSError as exc:
            return None, redact_text(str(exc))[-1000:]
        if len(data) > limit:
            data = data[:limit]
        return data.decode("utf-8", errors="replace"), None

    def _host_uptime(self) -> float | None:
        text, _error = self._read_text(self.proc_root / "uptime", limit=4096)
        if not text:
            return None
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            return None

    def _process_snapshot(self, pid: int, *, host_uptime: float | None) -> dict[str, Any]:
        root = self.proc_root / str(pid)
        result: dict[str, Any] = {
            "pid": pid,
            "rss_kb": None,
            "pss_kb": None,
            "age_seconds": None,
            "cgroup": None,
            "errors": [],
        }
        status, error = self._read_text(root / "status", limit=256_000)
        if status is not None:
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        result["rss_kb"] = _parse_int(parts[1])
                    break
        elif error:
            result["errors"].append("status_unavailable")

        smaps, error = self._read_text(root / "smaps_rollup", limit=512_000)
        if smaps is not None:
            for line in smaps.splitlines():
                if line.startswith("Pss:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        result["pss_kb"] = _parse_int(parts[1])
                    break
        elif error:
            result["errors"].append("pss_unavailable")

        cgroup, error = self._read_text(root / "cgroup", limit=64_000)
        if cgroup is not None:
            result["cgroup"] = cgroup.strip()
        elif error:
            result["errors"].append("cgroup_unavailable")

        stat_text, error = self._read_text(root / "stat", limit=64_000)
        if stat_text is not None and host_uptime is not None:
            close = stat_text.rfind(")")
            if close >= 0:
                tail = stat_text[close + 1 :].strip().split()
                if len(tail) > 19:
                    start_ticks = _parse_int(tail[19])
                    if start_ticks is not None:
                        try:
                            clock_ticks = float(os.sysconf("SC_CLK_TCK"))
                        except (ValueError, OSError):
                            clock_ticks = 100.0
                        age = host_uptime - (start_ticks / clock_ticks)
                        if age >= 0:
                            result["age_seconds"] = round(age, 3)
        elif error:
            result["errors"].append("stat_unavailable")
        return result

    def _cgroup_snapshot(self, control_group: str, *, host_uptime: float | None) -> dict[str, Any]:
        if not control_group or not control_group.startswith("/"):
            return {
                "path": control_group or None,
                "available": False,
                "processes": [],
                "memory_events": {},
                "memory_events_local": {},
            }
        root = self.cgroup_root / control_group.lstrip("/")
        result: dict[str, Any] = {"path": control_group, "available": root.is_dir()}
        procs_text, _error = self._read_text(root / "cgroup.procs", limit=256_000)
        pids: list[int] = []
        if procs_text:
            for line in procs_text.splitlines():
                pid = _parse_int(line.strip())
                if pid is not None and pid > 0:
                    pids.append(pid)
        result["pids"] = pids
        result["processes"] = [self._process_snapshot(pid, host_uptime=host_uptime) for pid in pids]

        for filename, field in (
            ("memory.current", "memory_current"),
            ("memory.peak", "memory_peak"),
            ("memory.max", "memory_max"),
        ):
            text, _error = self._read_text(root / filename, limit=4096)
            if text is None:
                result[field] = None
            else:
                raw = text.strip()
                result[field] = raw if raw == "max" else _parse_int(raw)

        events, _error = self._read_text(root / "memory.events", limit=64_000)
        result["memory_events"] = _parse_key_ints(events or "")
        events_local, _error = self._read_text(root / "memory.events.local", limit=64_000)
        result["memory_events_local"] = _parse_key_ints(events_local or "")
        return result

    def _host_evidence(self) -> dict[str, Any]:
        meminfo, mem_error = self._read_text(self.proc_root / "meminfo", limit=1_000_000)
        pressure: dict[str, Any] = {}
        for name in ("cpu", "memory", "io"):
            text, error = self._read_text(self.proc_root / "pressure" / name, limit=64_000)
            pressure[name] = {
                "available": text is not None,
                "values": _parse_psi(text or ""),
                "error": error,
            }
        return {
            "uptime_seconds": self._host_uptime(),
            "meminfo": {
                "available": meminfo is not None,
                "values": _parse_meminfo(meminfo or ""),
                "error": mem_error,
            },
            "psi": pressure,
        }

    @staticmethod
    def _http(
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 3.0,
    ) -> HTTPResult:
        request = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(262_144)
                return HTTPResult(response.status, data, dict(response.headers.items()), None)
        except urllib.error.HTTPError as exc:
            try:
                data = exc.read(262_144)
            except OSError:
                data = b""
            return HTTPResult(exc.code, data, dict(exc.headers.items()) if exc.headers else {}, None)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return HTTPResult(None, b"", {}, redact_text(str(exc))[-1000:])

    def _mcp_probes(self, desired: DesiredInstance) -> dict[str, Any]:
        base = f"http://{desired.mcp.host}:{desired.mcp.port}"
        discovery_response = self._http("GET", f"{base}/.well-known/mcp.json")
        discovery: dict[str, Any] = {
            "status": discovery_response.status,
            "error": discovery_response.error,
            "protocol_version": None,
        }
        if discovery_response.body:
            try:
                payload = json.loads(discovery_response.body.decode("utf-8", errors="strict"))
            except (UnicodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, Mapping):
                version = payload.get("protocolVersion")
                if isinstance(version, str):
                    discovery["protocol_version"] = version

        initialize_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "workspace-mcp-manager-diagnostics", "version": "1"},
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        initialize_response = self._http(
            "POST",
            f"{base}/mcp",
            body=initialize_body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        session_id = None
        for key, value in initialize_response.headers.items():
            if key.lower() == "mcp-session-id":
                session_id = value
                break
        initialize: dict[str, Any] = {
            "status": initialize_response.status,
            "error": initialize_response.error,
            "protocol_version": None,
            "session_created": bool(session_id),
            "session_cleanup": {"attempted": False, "status": None, "ok": None},
        }
        if initialize_response.body:
            try:
                payload = json.loads(initialize_response.body.decode("utf-8", errors="strict"))
            except (UnicodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, Mapping):
                result = payload.get("result")
                if isinstance(result, Mapping) and isinstance(result.get("protocolVersion"), str):
                    initialize["protocol_version"] = result.get("protocolVersion")
        if session_id:
            cleanup_response = self._http(
                "DELETE",
                f"{base}/mcp",
                headers={"Mcp-Session-Id": session_id, "Accept": "application/json"},
            )
            initialize["session_cleanup"] = {
                "attempted": True,
                "status": cleanup_response.status,
                "ok": cleanup_response.status is not None and 200 <= cleanup_response.status < 300,
                "error": cleanup_response.error,
            }
        return {"discovery": discovery, "initialize": initialize}

    def _tunnel_probes(self, desired: DesiredInstance) -> dict[str, Any]:
        base = f"http://{desired.tunnel.health_host}:{desired.tunnel.health_port}"
        result: dict[str, Any] = {}
        for name, path in (("health", "/healthz"), ("readiness", "/readyz")):
            response = self._http("GET", f"{base}{path}")
            text = response.body[:4096].decode("utf-8", errors="replace").strip() if response.body else ""
            result[name] = {
                "status": response.status,
                "text": redact_text(text),
                "error": response.error,
            }
        return result

    def _recent_tunnel_events(
        self,
        *,
        instance_id: str,
        captured_at: datetime,
        since_seconds: int,
    ) -> list[dict[str, Any]]:
        path = self.paths.state_root / "instances" / instance_id / "tunnel.log"
        cutoff = captured_at - timedelta(seconds=since_seconds)
        future_limit = captured_at + timedelta(seconds=FUTURE_CLOCK_SKEW_SECONDS)
        events: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_TUNNEL_EVENTS)
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            return []
        with handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, Mapping):
                    continue
                timestamp = _parse_rfc3339(str(entry.get("time", "")))
                if timestamp is None or timestamp < cutoff or timestamp > future_limit:
                    continue
                component = str(entry.get("component", ""))
                level = str(entry.get("level", ""))
                status = _extract_control_plane_5xx(entry) if component.lower() == "controlplane" else None
                timeout = _is_poll_timeout(entry)
                if level.upper() not in {"WARN", "ERROR", "FATAL"} and status is None and not timeout:
                    continue
                event = {
                    "time": timestamp.isoformat(),
                    "level": level or None,
                    "component": component or None,
                    "msg": redact_text(str(entry.get("msg", "")))[:MAX_EVENT_TEXT],
                    "error": redact_text(str(entry.get("error", "")))[:MAX_EVENT_TEXT] or None,
                    "control_plane_5xx": status is not None,
                    "http_status": status,
                    "poll_timeout": timeout,
                }
                events.append(event)
        return list(events)

    def run(self, desired: DesiredInstance, *, since_seconds: int = DEFAULT_SINCE_SECONDS) -> dict[str, Any]:
        if isinstance(since_seconds, bool) or not isinstance(since_seconds, int):
            raise config_error("diagnostic since_seconds must be an integer")
        if not 1 <= since_seconds <= MAX_SINCE_SECONDS:
            raise config_error(
                f"diagnostic since_seconds must be between 1 and {MAX_SINCE_SECONDS}",
                since_seconds=since_seconds,
            )

        captured_at = self.now().astimezone(timezone.utc)
        snapshot_id = _snapshot_id(captured_at)
        iid = desired.instance_id.value
        host = self._host_evidence()
        host_uptime = host.get("uptime_seconds")
        host_uptime_float = float(host_uptime) if isinstance(host_uptime, (int, float)) else None

        services = {
            "mcp": self._systemd_show(f"coding-tools-mcp-{iid}.service"),
            "tunnel": self._systemd_show(f"tunnel-client-{iid}.service"),
        }
        cgroups = {
            "mcp": self._cgroup_snapshot(str(services["mcp"].get("ControlGroup", "")), host_uptime=host_uptime_float),
            "tunnel": self._cgroup_snapshot(
                str(services["tunnel"].get("ControlGroup", "")), host_uptime=host_uptime_float
            ),
        }
        cgroups["separate"] = bool(
            cgroups["mcp"].get("path")
            and cgroups["tunnel"].get("path")
            and cgroups["mcp"].get("path") != cgroups["tunnel"].get("path")
        )

        probes = {"mcp": self._mcp_probes(desired), "tunnel": self._tunnel_probes(desired)}
        tunnel_events = self._recent_tunnel_events(
            instance_id=iid,
            captured_at=captured_at,
            since_seconds=since_seconds,
        )
        classifications, domains = _classify(
            probes=probes,
            services=services,
            cgroups={"mcp": cgroups["mcp"], "tunnel": cgroups["tunnel"]},
            tunnel_events=tunnel_events,
            since_seconds=since_seconds,
            host_uptime_seconds=host_uptime_float,
        )
        snapshot = {
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "captured_at": captured_at.isoformat(),
            "instance_id": iid,
            "desired_fingerprint": desired.fingerprint(),
            "since_seconds": since_seconds,
            "services": services,
            "cgroups": cgroups,
            "host": host,
            "probes": probes,
            "recent_tunnel_events": tunnel_events,
            "classifications": classifications,
            "incident_domains": domains,
        }
        sanitized = redact_object(snapshot)
        snapshot_path = self.paths.state_root / "diagnostics" / iid / f"{snapshot_id}.json"
        _atomic_write(snapshot_path, _json_text(sanitized), mode=0o600)
        return {
            "ok": True,
            "instance_id": iid,
            "snapshot_path": str(snapshot_path),
            "snapshot": sanitized,
        }

