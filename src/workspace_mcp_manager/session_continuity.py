from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from .domain import DeploymentTarget, DesiredInstance
from .paths import ManagerPaths
from .registry import InstanceRegistry


CLIENT_COMPATIBILITY_PROJECTION_VERSION = 2
SESSION_CONTINUITY_PROJECTION_VERSION = 3
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_RESPONSE_LIMIT = 64 * 1024
MCP_TIMEOUT_SECONDS = 10.0


class ClientCompatibilityService:
    """Qualify coding-tools behavior for clients that may use a fresh MCP session per call.

    Protocol-session persistence is not an ordinary coding-readiness requirement.
    The important distinction is whether explicit server-minted command/output
    handles remain usable from a different MCP session.
    """

    def __init__(self, paths: ManagerPaths, registry: InstanceRegistry) -> None:
        self.paths = paths
        self.registry = registry

    @staticmethod
    def _url(desired: DesiredInstance) -> str:
        host = desired.mcp.host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{desired.mcp.port}/mcp"

    @staticmethod
    def _test_cwd(desired: DesiredInstance) -> str | None:
        workspace = Path(desired.workspace_path)
        for name in ("docs", "src", "tests", "packages", "apps"):
            path = workspace / name
            try:
                if path.is_dir() and not path.is_symlink():
                    return name
            except OSError:
                continue
        return None

    @staticmethod
    def _decode_sse(text: str) -> Mapping[str, Any]:
        stripped = text.strip()
        if stripped.startswith("data:"):
            values = [line[5:].strip() for line in stripped.splitlines() if line.startswith("data:")]
            stripped = values[-1] if values else ""
        if not stripped:
            return {}
        value = json.loads(stripped)
        if not isinstance(value, Mapping):
            raise ValueError("MCP response is not an object")
        return value

    @classmethod
    def _request(
        cls,
        url: str,
        payload: Mapping[str, Any] | None,
        *,
        session_id: str | None = None,
        method: str = "POST",
    ) -> tuple[Mapping[str, Any], str | None, int]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Connection": "close",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=MCP_TIMEOUT_SECONDS) as response:
            raw = response.read(MCP_RESPONSE_LIMIT + 1)
            if len(raw) > MCP_RESPONSE_LIMIT:
                raise ValueError("MCP response exceeded bound")
            decoded = cls._decode_sse(raw.decode("utf-8", errors="strict"))
            return decoded, response.headers.get("Mcp-Session-Id"), int(response.status)

    @classmethod
    def _tool_result(
        cls,
        url: str,
        session_id: str | None,
        request_id: int,
        name: str,
        arguments: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], bool]:
        response, _, _ = cls._request(
            url,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            },
            session_id=session_id,
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise ValueError(f"MCP tool returned no result: {name}")
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            raise ValueError(f"MCP tool returned no structuredContent: {name}")
        ok = result.get("isError") is not True and structured.get("ok") is not False
        return structured, ok

    @classmethod
    def _tool(
        cls,
        url: str,
        session_id: str | None,
        request_id: int,
        name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        structured, ok = cls._tool_result(url, session_id, request_id, name, arguments)
        if not ok:
            raise ValueError(f"MCP tool returned structured failure: {name}")
        return structured

    @classmethod
    def _tools(cls, url: str, session_id: str | None, request_id: int) -> dict[str, Mapping[str, Any]]:
        response, _, _ = cls._request(
            url,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/list",
                "params": {},
            },
            session_id=session_id,
        )
        result = response.get("result")
        tools = result.get("tools") if isinstance(result, Mapping) else None
        if not isinstance(tools, list):
            raise ValueError("MCP tools/list returned no tool catalog")
        catalog: dict[str, Mapping[str, Any]] = {}
        for item in tools:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            if isinstance(name, str) and name:
                catalog[name] = item
        return catalog

    @staticmethod
    def _required_schema_field(tool: Mapping[str, Any], field: str) -> bool:
        schema = tool.get("inputSchema")
        if not isinstance(schema, Mapping):
            return False
        required = schema.get("required")
        properties = schema.get("properties")
        return (
            isinstance(required, list)
            and field in required
            and isinstance(properties, Mapping)
            and field in properties
        )

    @staticmethod
    def _handle_scope(structured: Mapping[str, Any], ok: bool) -> str:
        if ok:
            return "cross_session"
        error = structured.get("error") if isinstance(structured.get("error"), Mapping) else {}
        code = str(error.get("code") or "")
        if code in {"SESSION_NOT_FOUND", "SESSION_CLOSED"}:
            return "session_scoped"
        serialized = json.dumps(structured, sort_keys=True)
        if "SESSION_NOT_FOUND" in serialized or "Session not found" in serialized:
            return "session_scoped"
        return "unavailable"

    def run(
        self,
        instance_id: str,
        *,
        endpoint_url: str | None = None,
        scope: str = "local_mcp",
    ) -> dict[str, Any]:
        desired = self.registry.get(instance_id)
        base: dict[str, Any] = {
            "ok": False,
            "client_compatibility_projection_version": CLIENT_COMPATIBILITY_PROJECTION_VERSION,
            "session_continuity_projection_version": SESSION_CONTINUITY_PROJECTION_VERSION,
            "instance_id": instance_id,
            "scope": scope,
            "server": {"version": None, "protocol": None},
            "transport_session_model": "unknown",
            "process_control": {
                "handle_field": None,
                "terminate_tool": None,
            },
            "atomic_coding_ready": False,
            "protocol_session_persistence": "not_required_for_atomic_operations",
            "session_local_state": {
                "mcp_session": "unknown",
                "default_cwd": "unknown",
                "command_session": "unknown",
            },
            "cross_session_state": {
                "default_cwd": "not_required",
                "write_stdin": "unknown",
                "read_output": "unknown",
                "kill_session": "unknown",
                "kill_command": "unknown",
                "command_handles": "unknown",
            },
            # Backward-compatible summary fields retained for existing callers.
            "mcp_session": "unknown",
            "default_cwd": "unknown",
            "command_session": "unknown",
            "cleanup": "unknown",
            "session_fingerprint": None,
            "result": "unavailable",
            "reason_code": None,
            "limitations": [],
            "recommended_usage": {
                "explicit_cwd_or_workdir": True,
                "bounded_exec_command": True,
                "cross_call_command_interaction": False,
            },
            "tunnel_backed": {
                "state": "not_required_for_atomic_readiness",
                "reason": "protocol-session persistence is not required for ordinary atomic coding operations",
            },
        }
        if desired.lifecycle.deployment is DeploymentTarget.ABSENT:
            base["reason_code"] = "MCP_NOT_DEPLOYED"
            return base
        test_cwd = self._test_cwd(desired)
        if test_cwd is None:
            base["reason_code"] = "NO_TEST_SUBDIRECTORY"
            return base

        url = endpoint_url or self._url(desired)
        session_id: str | None = None
        fresh_session_id: str | None = None
        process_session: str | None = None
        cleanup = "unknown"
        try:
            initialized, response_session, _ = self._request(
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "workspace-mcp-manager-session-qualification", "version": "1"},
                    },
                },
            )
            result = initialized.get("result")
            if not isinstance(result, Mapping):
                raise ValueError("initialize returned no result")
            server_info = result.get("serverInfo") if isinstance(result.get("serverInfo"), Mapping) else {}
            base["server"] = {
                "version": server_info.get("version"),
                "protocol": result.get("protocolVersion"),
            }

            if not response_session:
                catalog = self._tools(url, None, 2)
                required_tools = {"exec_command", "write_stdin", "read_output", "kill_command"}
                missing_tools = sorted(required_tools - set(catalog))
                if missing_tools:
                    raise ValueError(f"stateless coding-tools catalog is missing: {', '.join(missing_tools)}")
                if not self._required_schema_field(catalog["write_stdin"], "command_id"):
                    raise ValueError("write_stdin does not require command_id")
                if not self._required_schema_field(catalog["kill_command"], "command_id"):
                    raise ValueError("kill_command does not require command_id")

                base["transport_session_model"] = "stateless"
                base["mcp_session"] = "not_applicable"
                base["session_local_state"]["mcp_session"] = "not_applicable"
                base["default_cwd"] = "not_applicable"
                base["session_local_state"]["default_cwd"] = "not_applicable"
                base["cross_session_state"]["default_cwd"] = "not_applicable_explicit_workdir"
                base["process_control"] = {
                    "handle_field": "command_id",
                    "terminate_tool": "kill_command",
                }

                started = self._tool(
                    url,
                    None,
                    3,
                    "exec_command",
                    {
                        "cmd": "cat",
                        "tty": True,
                        "workdir": test_cwd,
                        "yield_time_ms": 0,
                        "timeout_ms": 30000,
                        "max_output_bytes": 4096,
                        "preview_bytes": 1024,
                        "verbosity": "full",
                    },
                )
                command_id = str(started.get("command_id") or "")
                output_ref = started.get("output_ref")
                if not isinstance(output_ref, str):
                    output_refs = started.get("output_refs") if isinstance(started.get("output_refs"), Mapping) else {}
                    output_ref = output_refs.get("stdout")
                if started.get("status") != "running" or not command_id or not isinstance(output_ref, str):
                    raise ValueError("exec_command did not establish a persistent command handle")

                marker = "p71-stateless-command-handle"
                self._tool(
                    url,
                    None,
                    4,
                    "write_stdin",
                    {"command_id": command_id, "chars": marker + "\n", "yield_time_ms": 100},
                )
                output = self._tool(
                    url,
                    None,
                    5,
                    "read_output",
                    {"output_ref": output_ref, "stream": "stdout", "offset": 0, "limit": 4096},
                )
                if marker not in json.dumps(output, sort_keys=True):
                    raise ValueError("read_output did not observe stateless write_stdin marker")
                killed = self._tool(
                    url,
                    None,
                    6,
                    "kill_command",
                    {"command_id": command_id, "signal": "TERM", "wait_ms": 2000},
                )
                if killed.get("ok") is False:
                    raise ValueError("kill_command returned a structured failure")

                base["command_session"] = "passed"
                base["session_local_state"]["command_session"] = "passed"
                base["cross_session_state"].update(
                    {
                        "write_stdin": "cross_session",
                        "read_output": "cross_session",
                        "kill_session": "not_applicable",
                        "kill_command": "cross_session",
                        "command_handles": "cross_session",
                    }
                )
                base["recommended_usage"]["cross_call_command_interaction"] = True
                base["atomic_coding_ready"] = True
                base["cleanup"] = "not_applicable"
                base["ok"] = True
                base["result"] = "passed"
                base["reason_code"] = None
                return base

            session_id = response_session
            base["transport_session_model"] = "stateful_legacy"
            base["process_control"] = {
                "handle_field": "session_id",
                "terminate_tool": "kill_session",
            }
            base["session_fingerprint"] = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
            base["mcp_session"] = "passed"
            base["session_local_state"]["mcp_session"] = "passed"
            self._request(
                url,
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                session_id=session_id,
            )

            set_cwd = self._tool(url, session_id, 2, "set_default_cwd", {"path": test_cwd})
            get_cwd = self._tool(url, session_id, 3, "get_default_cwd", {})
            if set_cwd.get("default_cwd") != test_cwd or get_cwd.get("default_cwd") != test_cwd:
                raise ValueError("default_cwd did not persist within one MCP session")
            base["default_cwd"] = "passed"
            base["session_local_state"]["default_cwd"] = "passed"

            started = self._tool(
                url,
                session_id,
                4,
                "exec_command",
                {
                    "cmd": "cat",
                    "tty": True,
                    "yield_time_ms": 0,
                    "timeout_ms": 30000,
                    "max_output_bytes": 4096,
                    "preview_bytes": 1024,
                    "verbosity": "full",
                },
            )
            process_session = str(started.get("session_id") or "")
            output_ref = started.get("output_ref")
            if not isinstance(output_ref, str):
                output_refs = started.get("output_refs") if isinstance(started.get("output_refs"), Mapping) else {}
                output_ref = output_refs.get("stdout")
            if started.get("status") != "running" or not process_session or not isinstance(output_ref, str):
                raise ValueError("exec_command did not establish a persistent command session")

            marker = "p71-session-continuity"
            self._tool(
                url,
                session_id,
                5,
                "write_stdin",
                {"session_id": process_session, "chars": marker + "\n", "yield_time_ms": 100},
            )
            output = self._tool(
                url,
                session_id,
                6,
                "read_output",
                {"output_ref": output_ref, "stream": "stdout", "offset": 0, "limit": 4096},
            )
            serialized = json.dumps(output, sort_keys=True)
            if marker not in serialized:
                raise ValueError("read_output did not observe write_stdin marker")
            base["command_session"] = "passed"
            base["session_local_state"]["command_session"] = "passed"

            # A fresh MCP protocol session models clients that do not retain
            # protocol-session affinity between tool invocations. Explicit
            # command/output handles are the relevant cross-call capability.
            fresh_initialized, fresh_response_session, _ = self._request(
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "workspace-mcp-manager-stateless-compatibility", "version": "1"},
                    },
                },
            )
            fresh_result = fresh_initialized.get("result")
            if isinstance(fresh_result, Mapping) and fresh_response_session:
                fresh_session_id = fresh_response_session
                self._request(
                    url,
                    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    session_id=fresh_session_id,
                )
                fresh_cwd = self._tool(url, fresh_session_id, 9, "get_default_cwd", {})
                base["cross_session_state"]["default_cwd"] = (
                    "cross_session" if fresh_cwd.get("default_cwd") == test_cwd else "session_scoped_optional"
                )

                write_result, write_ok = self._tool_result(
                    url,
                    fresh_session_id,
                    10,
                    "write_stdin",
                    {"session_id": process_session, "chars": "p71-cross-session\n", "yield_time_ms": 100},
                )
                read_result, read_ok = self._tool_result(
                    url,
                    fresh_session_id,
                    11,
                    "read_output",
                    {"output_ref": output_ref, "stream": "stdout", "offset": 0, "limit": 4096},
                )
                kill_result, kill_ok = self._tool_result(
                    url,
                    fresh_session_id,
                    12,
                    "kill_session",
                    {"session_id": process_session, "signal": "TERM", "wait_ms": 2000},
                )
                write_scope = self._handle_scope(write_result, write_ok)
                read_scope = self._handle_scope(read_result, read_ok)
                kill_scope = self._handle_scope(kill_result, kill_ok)
                base["cross_session_state"].update(
                    {
                        "write_stdin": write_scope,
                        "read_output": read_scope,
                        "kill_session": kill_scope,
                        "kill_command": "not_applicable",
                    }
                )
                scopes = {write_scope, read_scope, kill_scope}
                if scopes == {"cross_session"}:
                    handle_scope = "cross_session"
                    process_session = None
                    base["recommended_usage"]["cross_call_command_interaction"] = True
                elif scopes == {"session_scoped"}:
                    handle_scope = "session_scoped"
                else:
                    handle_scope = "partial_or_unavailable"
                base["cross_session_state"]["command_handles"] = handle_scope
                if handle_scope != "cross_session":
                    base["limitations"].append(
                        "persistent command/output handles are not fully usable from a fresh MCP protocol session"
                    )
            else:
                base["limitations"].append("fresh MCP session compatibility could not be probed")

            if process_session:
                self._tool(
                    url,
                    session_id,
                    13,
                    "kill_session",
                    {"session_id": process_session, "signal": "TERM", "wait_ms": 2000},
                )
                process_session = None

            base["atomic_coding_ready"] = True
            base["ok"] = True
            base["result"] = "passed" if not base["limitations"] else "passed_with_limitations"
            base["reason_code"] = None
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            base["result"] = "failed"
            base["reason_code"] = type(exc).__name__.upper()
        finally:
            if fresh_session_id:
                try:
                    self._request(url, None, session_id=fresh_session_id, method="DELETE")
                except Exception:
                    pass
            if session_id:
                if process_session:
                    try:
                        self._tool(
                            url,
                            session_id,
                            98,
                            "kill_session",
                            {"session_id": process_session, "signal": "KILL", "wait_ms": 1000},
                        )
                    except Exception:
                        pass
                try:
                    _, _, status = self._request(url, None, session_id=session_id, method="DELETE")
                    cleanup = "passed" if status in {200, 202, 204} else "failed"
                except urllib.error.HTTPError as exc:
                    cleanup = "unavailable" if scope != "local_mcp" and exc.code == 501 else "failed"
                except Exception:
                    cleanup = "failed"
        base["cleanup"] = cleanup
        if base["result"] == "passed" and cleanup != "passed" and scope == "local_mcp":
            base["ok"] = False
            base["result"] = "failed"
            base["reason_code"] = "SESSION_CLEANUP_FAILED"
        return base


SessionContinuityService = ClientCompatibilityService


__all__ = [
    "CLIENT_COMPATIBILITY_PROJECTION_VERSION",
    "SESSION_CONTINUITY_PROJECTION_VERSION",
    "ClientCompatibilityService",
    "SessionContinuityService",
]
