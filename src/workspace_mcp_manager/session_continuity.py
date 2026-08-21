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


SESSION_CONTINUITY_PROJECTION_VERSION = 1
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_RESPONSE_LIMIT = 64 * 1024
MCP_TIMEOUT_SECONDS = 10.0


class SessionContinuityService:
    """Qualify session-scoped coding-tools behavior through the local MCP endpoint."""

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
    def _tool(
        cls,
        url: str,
        session_id: str,
        request_id: int,
        name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
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
        if not isinstance(result, Mapping) or result.get("isError") is True:
            raise ValueError(f"MCP tool failed: {name}")
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            raise ValueError(f"MCP tool returned no structuredContent: {name}")
        if structured.get("ok") is False:
            raise ValueError(f"MCP tool returned structured failure: {name}")
        return structured

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
            "session_continuity_projection_version": SESSION_CONTINUITY_PROJECTION_VERSION,
            "instance_id": instance_id,
            "scope": scope,
            "server": {"version": None, "protocol": None},
            "mcp_session": "unknown",
            "default_cwd": "unknown",
            "command_session": "unknown",
            "cleanup": "unknown",
            "session_fingerprint": None,
            "result": "unavailable",
            "reason_code": None,
            "tunnel_backed": {
                "state": "external_validation_required",
                "reason": "ChatGPT connector/tunnel continuity cannot be inferred from local MCP continuity",
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
            if not isinstance(result, Mapping) or not response_session:
                raise ValueError("initialize did not establish an MCP session")
            session_id = response_session
            base["session_fingerprint"] = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
            base["mcp_session"] = "passed"
            server_info = result.get("serverInfo") if isinstance(result.get("serverInfo"), Mapping) else {}
            base["server"] = {
                "version": server_info.get("version"),
                "protocol": result.get("protocolVersion"),
            }
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
            self._tool(
                url,
                session_id,
                7,
                "kill_session",
                {"session_id": process_session, "signal": "TERM", "wait_ms": 2000},
            )
            process_session = None
            base["command_session"] = "passed"
            base["ok"] = True
            base["result"] = "passed"
            base["reason_code"] = None
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            base["result"] = "failed"
            base["reason_code"] = type(exc).__name__.upper()
        finally:
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


__all__ = ["SESSION_CONTINUITY_PROJECTION_VERSION", "SessionContinuityService"]
