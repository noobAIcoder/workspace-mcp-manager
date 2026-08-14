from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_MANAGER_OUTPUT_BYTES = 1024 * 1024


class TuiError(RuntimeError):
    def __init__(self, message: str, *, payload: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = dict(payload) if payload is not None else None


@dataclass(frozen=True, slots=True)
class ManagerInvocation:
    argv: tuple[str, ...]
    returncode: int
    payload: dict[str, Any]


def default_manager_executable() -> str:
    configured = os.environ.get("WORKSPACE_MCP_MANAGER_BIN")
    if configured:
        return configured
    found = shutil.which("workspace-mcp-manager")
    if found:
        return found
    return str(Path.home() / ".local" / "bin" / "workspace-mcp-manager")


def _decode_payload(stdout: str, stderr: str) -> dict[str, Any]:
    candidates = [stdout.strip(), stderr.strip()]
    for text in candidates:
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    detail = (stderr or stdout).strip()
    if len(detail) > 2000:
        detail = detail[-2000:]
    raise TuiError(f"manager returned non-JSON output: {detail or '<empty>'}")


class ManagerClient:
    """Thin process client over the public workspace-mcp-manager CLI."""

    def __init__(self, manager: str, *, registry_dir: Path | None = None) -> None:
        self.manager = manager
        self.registry_dir = registry_dir

    def argv(self, *args: str) -> tuple[str, ...]:
        values = [self.manager]
        if self.registry_dir is not None:
            values.extend(["--registry-dir", str(self.registry_dir)])
        values.extend(args)
        return tuple(values)

    def invoke(
        self,
        *args: str,
        stdin_text: str | None = None,
        require_ok: bool = False,
    ) -> ManagerInvocation:
        argv = self.argv(*args)
        try:
            run_kwargs: dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "errors": "replace",
                "check": False,
                "timeout": 180.0,
            }
            if stdin_text is None:
                run_kwargs["stdin"] = subprocess.DEVNULL
            else:
                run_kwargs["input"] = stdin_text
            completed = subprocess.run(list(argv), **run_kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TuiError(f"cannot execute manager: {exc}") from exc
        stdout = completed.stdout[-MAX_MANAGER_OUTPUT_BYTES:]
        stderr = completed.stderr[-MAX_MANAGER_OUTPUT_BYTES:]
        payload = _decode_payload(stdout, stderr)
        if require_ok and (completed.returncode != 0 or payload.get("ok") is False):
            error = payload.get("error")
            if isinstance(error, Mapping):
                message = str(error.get("message") or "manager operation failed")
            else:
                message = "manager operation failed"
            raise TuiError(message, payload=payload)
        return ManagerInvocation(argv=argv, returncode=completed.returncode, payload=payload)

    def list_instances(self) -> list[dict[str, Any]]:
        payload = self.invoke("instance", "list", require_ok=True).payload
        instances = payload.get("instances", [])
        if not isinstance(instances, list):
            raise TuiError("manager instance list returned malformed instances", payload=payload)
        return [dict(item) for item in instances if isinstance(item, Mapping)]

    def show(self, instance_id: str) -> dict[str, Any]:
        return self.invoke("instance", "show", instance_id, require_ok=True).payload

    def view(self, command: str, instance_id: str) -> dict[str, Any]:
        if command == "logs":
            return self.invoke("instance", "logs", instance_id, "--lines", "100").payload
        if command == "diagnose":
            return self.invoke("instance", "diagnose", instance_id).payload
        if command not in {"plan", "status", "git"}:
            raise TuiError(f"unsupported TUI view command: {command}")
        return self.invoke("instance", command, instance_id).payload

    def lifecycle(self, action: str, instance_id: str) -> dict[str, Any]:
        if action not in {"apply", "start", "stop", "restart", "remove"}:
            raise TuiError(f"unsupported lifecycle action: {action}")
        return self.invoke("instance", action, instance_id, require_ok=True).payload

    def access_list(self, instance_id: str) -> dict[str, Any]:
        return self.invoke("access", "list", instance_id, require_ok=True).payload

    def access_add(self, instance_id: str, *, mode: str, alias: str, path: str) -> dict[str, Any]:
        action = "add-ro" if mode == "ro" else "add-rw"
        return self.invoke("access", action, instance_id, alias, path, require_ok=True).payload

    def access_remove(self, instance_id: str, *, alias: str) -> dict[str, Any]:
        return self.invoke("access", "remove", instance_id, alias, require_ok=True).payload

    def host_view(self, command: str) -> dict[str, Any]:
        if command == "tools":
            return self.invoke("host", "tools", "audit").payload
        if command not in {"inspect", "doctor", "components"}:
            raise TuiError(f"unsupported host view: {command}")
        return self.invoke("host", command).payload

    def save_declaration(self, action: str, desired: Mapping[str, Any]) -> dict[str, Any]:
        if action not in {"create", "update"}:
            raise TuiError(f"unsupported declaration action: {action}")
        path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="workspace-mcp-manager-tui-",
                delete=False,
            ) as handle:
                path = handle.name
                json.dump(desired, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
            self.invoke("instance", "validate", path, require_ok=True)
            return self.invoke("instance", action, path, require_ok=True).payload
        finally:
            if path is not None:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass


def project_v1_to_v2(desired: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(desired))
    if result.get("config_version") != 1:
        return result
    github_v1 = result.get("github")
    config_dir = github_v1.get("config_dir") if isinstance(github_v1, Mapping) else None
    if config_dir:
        result["github"] = {"mode": "external", "config_dir": config_dir, "binary": None}
    else:
        result["github"] = {"mode": "disabled", "config_dir": None, "binary": None}
    result["git"] = {"identity": None, "remote": None}
    result["agent"] = {"mode": "none", "ssh_auth_sock": None}
    result["config_version"] = 2
    return result


@dataclass(frozen=True, slots=True)
class EditorField:
    label: str
    path: tuple[str, ...]
    kind: str = "str"
    choices: tuple[str, ...] = ()
    min_version: int = 1
    max_version: int | None = None


EDITOR_FIELDS = (
    EditorField("Workspace path", ("workspace_path",)),
    EditorField("MCP binary", ("mcp", "binary")),
    EditorField("MCP host", ("mcp", "host")),
    EditorField("MCP port", ("mcp", "port"), "int"),
    EditorField(
        "Permission mode",
        ("mcp", "permission_mode"),
        "choice",
        ("safe", "trusted", "dangerous"),
    ),
    EditorField(
        "Shell environment",
        ("mcp", "shell_env_inherit"),
        "choice",
        ("core", "all", "none"),
    ),
    EditorField("Execution PATH", ("mcp", "exec_path")),
    EditorField("External roots (JSON array)", ("mcp", "external_roots"), "json"),
    EditorField("Tunnel binary", ("tunnel", "binary")),
    EditorField("Tunnel ID", ("tunnel", "id")),
    EditorField("Tunnel profile", ("tunnel", "profile")),
    EditorField("Tunnel health host", ("tunnel", "health_host")),
    EditorField("Tunnel health port", ("tunnel", "health_port"), "int"),
    EditorField("Tunnel env file", ("tunnel", "env_file")),
    EditorField("Admission guard", ("recovery", "admission_guard_enabled"), "bool"),
    EditorField("Guard interval seconds", ("recovery", "guard_interval_seconds"), "int"),
    EditorField("Recovery cooldown seconds", ("recovery", "recovery_cooldown_seconds"), "int"),
    EditorField("GitHub config dir", ("github", "config_dir"), "optional", max_version=1),
    EditorField(
        "GitHub mode",
        ("github", "mode"),
        "choice",
        ("disabled", "external", "managed"),
        min_version=2,
    ),
    EditorField("GitHub config dir", ("github", "config_dir"), "optional", min_version=2),
    EditorField("GitHub binary", ("github", "binary"), "optional", min_version=2),
    EditorField("Git identity (JSON object/null)", ("git", "identity"), "json", min_version=2),
    EditorField("Git remote (JSON object/null)", ("git", "remote"), "json", min_version=2),
    EditorField("Agent (JSON object)", ("agent",), "json", min_version=2),
)


def available_editor_fields(desired: Mapping[str, Any]) -> list[EditorField]:
    version = int(desired.get("config_version", 1))
    return [
        field
        for field in EDITOR_FIELDS
        if version >= field.min_version and (field.max_version is None or version <= field.max_version)
    ]


def get_nested(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def set_nested(value: dict[str, Any], path: Sequence[str], replacement: Any) -> None:
    if not path:
        raise ValueError("path must not be empty")
    current = value
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = replacement


def parse_editor_value(field: EditorField, text: str) -> Any:
    if field.kind == "int":
        return int(text)
    if field.kind == "bool":
        normalized = text.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
        raise ValueError("expected true/false")
    if field.kind == "choice":
        if text not in field.choices:
            raise ValueError(f"expected one of: {', '.join(field.choices)}")
        return text
    if field.kind == "json":
        return json.loads(text)
    if field.kind == "optional":
        return None if text.strip().lower() in {"", "null", "none"} else text
    return text


def format_editor_value(value: Any) -> str:
    if isinstance(value, (dict, list)) or value is None or isinstance(value, bool):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def clip_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def json_lines(payload: Mapping[str, Any], *, max_lines: int = 400) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return lines
    return [*lines[: max_lines - 1], f"… {len(lines) - max_lines + 1} more lines"]


def dashboard_snapshot(client: ManagerClient, *, instance_id: str | None = None) -> str:
    instances = client.list_instances()
    lines = ["Workspace MCP Manager TUI", f"instances={len(instances)}"]
    for item in instances:
        lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), Mapping) else {}
        lines.append(
            "- "
            + str(item.get("instance_id", "?"))
            + " deployment="
            + str(lifecycle.get("deployment", "?"))
            + " runtime="
            + str(lifecycle.get("runtime", "?"))
        )
    if instance_id is not None:
        show = client.show(instance_id)
        desired = show.get("desired") if isinstance(show.get("desired"), Mapping) else {}
        lines.extend(
            [
                "",
                f"instance={instance_id}",
                f"config_version={desired.get('config_version', '?')}",
                f"workspace={desired.get('workspace_path', '?')}",
            ]
        )
        mcp = desired.get("mcp") if isinstance(desired.get("mcp"), Mapping) else {}
        tunnel = desired.get("tunnel") if isinstance(desired.get("tunnel"), Mapping) else {}
        lines.append(f"mcp={mcp.get('host', '?')}:{mcp.get('port', '?')} permission={mcp.get('permission_mode', '?')}")
        lines.append(f"tunnel_health={tunnel.get('health_host', '?')}:{tunnel.get('health_port', '?')}")
        plan = client.view("plan", instance_id)
        operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
        non_noop = [item for item in operations if isinstance(item, Mapping) and item.get("operation") != "NOOP"]
        lines.append(f"plan_valid={plan.get('valid')} non_noop={len(non_noop)}")
    return "\n".join(lines) + "\n"


def _load_curses():
    try:
        import curses
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise TuiError("Python curses support is unavailable") from exc
    return curses


class CursesTui:
    def __init__(self, stdscr: Any, client: ManagerClient, *, smoke: bool = False) -> None:
        self.stdscr = stdscr
        self.client = client
        self.smoke = smoke
        self.curses = _load_curses()
        self.selected = 0
        self.message = ""

    def _setup(self) -> None:
        self.curses.noecho()
        self.curses.cbreak()
        self.stdscr.keypad(True)
        try:
            self.curses.curs_set(0)
        except self.curses.error:
            pass

    def _put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        available = max(0, width - x - 1)
        if available <= 0:
            return
        try:
            self.stdscr.addnstr(y, x, text, available, attr)
        except self.curses.error:
            pass

    def _header(self, title: str) -> None:
        self._put(0, 0, title, self.curses.A_BOLD)

    def _footer(self, text: str) -> None:
        height, _ = self.stdscr.getmaxyx()
        if height >= 2:
            self._put(height - 2, 0, text, self.curses.A_DIM)
        if height >= 1 and self.message:
            self._put(height - 1, 0, self.message)

    def _prompt(self, label: str) -> str | None:
        height, width = self.stdscr.getmaxyx()
        if height < 2 or width < 8:
            self.message = "terminal is too small for input"
            return None
        y = height - 1
        try:
            self.stdscr.move(y, 0)
            self.stdscr.clrtoeol()
        except self.curses.error:
            pass
        prompt = clip_text(label, max(1, width - 2))
        self._put(y, 0, prompt)
        x = min(len(prompt), max(0, width - 2))
        try:
            self.curses.echo()
            self.curses.curs_set(1)
        except self.curses.error:
            pass
        try:
            raw = self.stdscr.getstr(y, x, 4096)
        except (self.curses.error, KeyboardInterrupt):
            return None
        finally:
            self.curses.noecho()
            try:
                self.curses.curs_set(0)
            except self.curses.error:
                pass
        return raw.decode("utf-8", errors="replace")

    def _confirm(self, text: str) -> bool:
        answer = self._prompt(f"{text} [y/N]: ")
        return bool(answer and answer.strip().lower() in {"y", "yes"})

    def _view_lines(self, title: str, lines: Sequence[str]) -> None:
        offset = 0
        while True:
            self.stdscr.erase()
            self._header(title)
            height, _ = self.stdscr.getmaxyx()
            visible = max(1, height - 4)
            chunk = lines[offset : offset + visible]
            for index, line in enumerate(chunk, start=1):
                self._put(index, 0, line)
            self._footer("↑/↓/PgUp/PgDn scroll  q back")
            self.stdscr.refresh()
            key = self.stdscr.get_wch()
            if key in ("q", "Q", "\x1b"):
                return
            if key == self.curses.KEY_DOWN:
                offset = min(max(0, len(lines) - visible), offset + 1)
            elif key == self.curses.KEY_UP:
                offset = max(0, offset - 1)
            elif key == self.curses.KEY_NPAGE:
                offset = min(max(0, len(lines) - visible), offset + visible)
            elif key == self.curses.KEY_PPAGE:
                offset = max(0, offset - visible)

    def _view_payload(self, title: str, payload: Mapping[str, Any]) -> None:
        self._view_lines(title, json_lines(payload))

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, TuiError) and exc.payload is not None:
            self._view_payload("Manager error", exc.payload)
        else:
            self.message = str(exc)

    def _host_screen(self) -> None:
        options = [
            ("i", "inspect", "Host inspect"),
            ("d", "doctor", "Host doctor"),
            ("c", "components", "Host components"),
            ("t", "tools", "Developer tools audit"),
        ]
        while True:
            self.stdscr.erase()
            self._header("Host overview")
            for row, (key, _, label) in enumerate(options, start=2):
                self._put(row, 2, f"{key}  {label}")
            self._footer("i inspect  d doctor  c components  t tools  q back")
            self.stdscr.refresh()
            key = self.stdscr.get_wch()
            if key in ("q", "Q", "\x1b"):
                return
            for hotkey, command, label in options:
                if key == hotkey:
                    try:
                        self._view_payload(label, self.client.host_view(command))
                    except Exception as exc:
                        self._error(exc)
                    break

    @staticmethod
    def _instance_summary(desired: Mapping[str, Any]) -> list[str]:
        lifecycle = desired.get("lifecycle") if isinstance(desired.get("lifecycle"), Mapping) else {}
        mcp = desired.get("mcp") if isinstance(desired.get("mcp"), Mapping) else {}
        tunnel = desired.get("tunnel") if isinstance(desired.get("tunnel"), Mapping) else {}
        lines = [
            f"workspace: {desired.get('workspace_path', '?')}",
            f"lifecycle: {lifecycle.get('deployment', '?')} / {lifecycle.get('runtime', '?')}",
            f"MCP: {mcp.get('host', '?')}:{mcp.get('port', '?')}  permission={mcp.get('permission_mode', '?')}",
            f"tunnel: {tunnel.get('profile', '?')}  health={tunnel.get('health_host', '?')}:{tunnel.get('health_port', '?')}",
        ]
        if desired.get("config_version") == 2:
            github = desired.get("github") if isinstance(desired.get("github"), Mapping) else {}
            git = desired.get("git") if isinstance(desired.get("git"), Mapping) else {}
            remote = git.get("remote") if isinstance(git.get("remote"), Mapping) else None
            agent = desired.get("agent") if isinstance(desired.get("agent"), Mapping) else {}
            lines.append(f"GitHub: {github.get('mode', '?')}")
            lines.append(f"Git remote: {remote.get('protocol', 'none') if remote else 'none'}")
            lines.append(f"agent: {agent.get('mode', '?')}")
        return lines

    def _access_screen(self, instance_id: str) -> None:
        while True:
            try:
                payload = self.client.access_list(instance_id)
            except Exception as exc:
                self._error(exc)
                return
            self.stdscr.erase()
            self._header(f"Access — {instance_id}")
            entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
            if not entries:
                self._put(2, 2, "No external access roots configured")
            else:
                for row, entry in enumerate(entries[: max(0, self.stdscr.getmaxyx()[0] - 6)], start=2):
                    if isinstance(entry, Mapping):
                        self._put(
                            row,
                            2,
                            f"{entry.get('mode', '?'):>2}  {entry.get('alias', '?')}  {entry.get('path', '?')}",
                        )
            self._footer("o add RO  w add RW  d remove alias  r refresh  q back")
            self.stdscr.refresh()
            key = self.stdscr.get_wch()
            if key in ("q", "Q", "\x1b"):
                return
            try:
                if key in ("o", "w"):
                    alias = self._prompt("alias: ")
                    if not alias:
                        continue
                    path = self._prompt("absolute path: ")
                    if not path:
                        continue
                    self.client.access_add(instance_id, mode="ro" if key == "o" else "rw", alias=alias, path=path)
                    self.message = "access declaration updated; apply instance to reconcile"
                elif key == "d":
                    alias = self._prompt("alias to remove: ")
                    if alias:
                        self.client.access_remove(instance_id, alias=alias)
                        self.message = "access declaration updated; apply instance to reconcile"
            except Exception as exc:
                self._error(exc)

    def _editor(self, desired: Mapping[str, Any], *, action: str) -> bool:
        working = copy.deepcopy(dict(desired))
        selected = 0
        while True:
            fields = available_editor_fields(working)
            selected = min(selected, max(0, len(fields) - 1))
            self.stdscr.erase()
            iid = working.get("instance_id", "?")
            self._header(f"Declaration editor — {iid} — {action}")
            height, width = self.stdscr.getmaxyx()
            visible = max(1, height - 5)
            start = max(0, min(selected - visible // 2, max(0, len(fields) - visible)))
            for row, field_index in enumerate(range(start, min(len(fields), start + visible)), start=1):
                field = fields[field_index]
                value = format_editor_value(get_nested(working, field.path))
                prefix = ">" if field_index == selected else " "
                label_width = min(30, max(10, width // 3))
                text = f"{prefix} {field.label:<{label_width}} {value}"
                attr = self.curses.A_REVERSE if field_index == selected else 0
                self._put(row, 0, text, attr)
            version = working.get("config_version", "?")
            suffix = "  u upgrade v1→v2" if version == 1 else ""
            self._footer(f"↑/↓ select  Enter edit  s save  q cancel{suffix}")
            self.stdscr.refresh()
            key = self.stdscr.get_wch()
            if key in ("q", "Q", "\x1b"):
                return False
            if key == self.curses.KEY_DOWN and fields:
                selected = min(len(fields) - 1, selected + 1)
                continue
            if key == self.curses.KEY_UP and fields:
                selected = max(0, selected - 1)
                continue
            if key == "u" and working.get("config_version") == 1:
                working = project_v1_to_v2(working)
                self.message = "projected declaration to config_version=2"
                continue
            if key == "s":
                try:
                    self.client.save_declaration(action, working)
                    self.message = f"declaration {action} succeeded"
                    return True
                except Exception as exc:
                    self._error(exc)
                continue
            if key in ("\n", "\r", self.curses.KEY_ENTER) and fields:
                field = fields[selected]
                current = format_editor_value(get_nested(working, field.path))
                hint = f"{field.label} [{current}]: "
                raw = self._prompt(hint)
                if raw is None or raw == "":
                    continue
                try:
                    parsed = parse_editor_value(field, raw)
                    set_nested(working, field.path, parsed)
                    if field.path == ("github", "mode") and parsed == "disabled":
                        set_nested(working, ("github", "config_dir"), None)
                        set_nested(working, ("github", "binary"), None)
                except (ValueError, json.JSONDecodeError) as exc:
                    self.message = f"invalid value: {exc}"

    def _clone_selected(self, template_id: str) -> None:
        try:
            show = self.client.show(template_id)
            desired = show.get("desired")
            if not isinstance(desired, Mapping):
                raise TuiError("template declaration is malformed", payload=show)
            clone = copy.deepcopy(dict(desired))
            instance_id = self._prompt("new instance ID: ")
            if not instance_id:
                return
            workspace = self._prompt("workspace path: ")
            mcp_port = self._prompt("MCP port: ")
            health_port = self._prompt("tunnel health port: ")
            tunnel_id = self._prompt("tunnel ID: ")
            profile = self._prompt("tunnel profile: ")
            if not all((workspace, mcp_port, health_port, tunnel_id, profile)):
                self.message = "create cancelled: all clone identity fields are required"
                return
            clone["instance_id"] = instance_id
            clone["workspace_path"] = workspace
            clone["mcp"]["port"] = int(mcp_port)
            clone["tunnel"]["health_port"] = int(health_port)
            clone["tunnel"]["id"] = tunnel_id
            clone["tunnel"]["profile"] = profile
            clone["lifecycle"] = {"deployment": "present", "runtime": "stopped"}
            if clone.get("config_version") == 2:
                github = clone.get("github")
                if isinstance(github, dict) and github.get("mode") == "managed":
                    github["config_dir"] = str(
                        Path.home() / ".config" / "workspace-mcp-manager" / "github" / instance_id
                    )
            self._editor(clone, action="create")
        except Exception as exc:
            self._error(exc)

    def _instance_screen(self, instance_id: str) -> None:
        while True:
            try:
                show = self.client.show(instance_id)
                desired = show.get("desired")
                if not isinstance(desired, Mapping):
                    raise TuiError("manager show returned malformed desired state", payload=show)
            except Exception as exc:
                self._error(exc)
                return
            self.stdscr.erase()
            self._header(f"Instance — {instance_id} — config v{desired.get('config_version', '?')}")
            for row, line in enumerate(self._instance_summary(desired), start=2):
                self._put(row, 2, line)
            self._footer(
                "p plan  s status  l logs  g git  d diagnose  v access  e edit  "
                "a apply  S start  X stop  R restart  D remove  q back"
            )
            self.stdscr.refresh()
            key = self.stdscr.get_wch()
            if key in ("q", "Q", "\x1b"):
                return
            view_map = {
                "p": ("plan", "Plan"),
                "s": ("status", "Status"),
                "l": ("logs", "Logs"),
                "g": ("git", "Git diagnostics"),
                "d": ("diagnose", "Resilience diagnostics"),
            }
            if key in view_map:
                command, title = view_map[key]
                try:
                    self._view_payload(f"{title} — {instance_id}", self.client.view(command, instance_id))
                except Exception as exc:
                    self._error(exc)
                continue
            if key == "v":
                self._access_screen(instance_id)
                continue
            if key == "e":
                self._editor(desired, action="update")
                continue
            lifecycle_map = {"a": "apply", "S": "start", "X": "stop", "R": "restart"}
            if key in lifecycle_map:
                try:
                    action = lifecycle_map[key]
                    result = self.client.lifecycle(action, instance_id)
                    self._view_payload(f"{action} — {instance_id}", result)
                except Exception as exc:
                    self._error(exc)
                continue
            if key == "D":
                if self._confirm(f"set {instance_id} deployment absent"):
                    try:
                        result = self.client.lifecycle("remove", instance_id)
                        self._view_payload(f"remove — {instance_id}", result)
                    except Exception as exc:
                        self._error(exc)

    def _dashboard_help(self) -> None:
        self._view_lines(
            "TUI help",
            [
                "Dashboard",
                "  ↑/↓ or j/k   select instance",
                "  Enter        instance detail",
                "  H            host overview",
                "  n            clone selected declaration into a new declaration",
                "  r            refresh",
                "  q            quit",
                "",
                "All lifecycle mutations are public workspace-mcp-manager CLI calls.",
                "The TUI never accepts API keys, GitHub tokens, private keys or passphrases.",
            ],
        )

    def run(self) -> None:
        self._setup()
        while True:
            try:
                instances = self.client.list_instances()
            except Exception as exc:
                self.stdscr.erase()
                self._header("Workspace MCP Manager TUI")
                self._put(2, 2, f"Cannot load instances: {exc}")
                self._footer("q quit  r retry")
                self.stdscr.refresh()
                if self.smoke:
                    return
                key = self.stdscr.get_wch()
                if key in ("q", "Q"):
                    return
                continue

            self.selected = min(self.selected, max(0, len(instances) - 1))
            self.stdscr.erase()
            self._header("Workspace MCP Manager TUI")
            height, _ = self.stdscr.getmaxyx()
            visible = max(1, height - 5)
            start = max(0, min(self.selected - visible // 2, max(0, len(instances) - visible)))
            if not instances:
                self._put(2, 2, "No declared instances")
            for row, item_index in enumerate(range(start, min(len(instances), start + visible)), start=2):
                item = instances[item_index]
                lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), Mapping) else {}
                prefix = ">" if item_index == self.selected else " "
                text = (
                    f"{prefix} {item.get('instance_id', '?'):<24} "
                    f"{lifecycle.get('deployment', '?')}/{lifecycle.get('runtime', '?')}"
                )
                attr = self.curses.A_REVERSE if item_index == self.selected else 0
                self._put(row, 0, text, attr)
            self._footer("↑/↓ j/k select  Enter detail  H host  n clone/create  ? help  r refresh  q quit")
            self.stdscr.refresh()
            if self.smoke:
                return
            key = self.stdscr.get_wch()
            if key in ("q", "Q"):
                return
            if key in (self.curses.KEY_DOWN, "j") and instances:
                self.selected = min(len(instances) - 1, self.selected + 1)
            elif key in (self.curses.KEY_UP, "k") and instances:
                self.selected = max(0, self.selected - 1)
            elif key in ("\n", "\r", self.curses.KEY_ENTER) and instances:
                self._instance_screen(str(instances[self.selected].get("instance_id")))
            elif key == "H":
                self._host_screen()
            elif key == "n":
                if instances:
                    self._clone_selected(str(instances[self.selected].get("instance_id")))
                else:
                    self.message = "create-from-template requires an existing declaration"
            elif key == "?":
                self._dashboard_help()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workspace-mcp-manager-tui")
    parser.add_argument("--manager", default=default_manager_executable())
    parser.add_argument("--registry-dir", type=Path)
    parser.add_argument("--snapshot", action="store_true", help="emit non-interactive dashboard text")
    parser.add_argument("--instance", help="include one instance detail in --snapshot output")
    parser.add_argument("--smoke", action="store_true", help="draw curses dashboard once and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = ManagerClient(args.manager, registry_dir=args.registry_dir)
    if args.snapshot:
        try:
            sys.stdout.write(dashboard_snapshot(client, instance_id=args.instance))
            return 0
        except TuiError as exc:
            print(f"workspace-mcp-manager-tui: {exc}", file=sys.stderr)
            return 2
    curses = None
    try:
        curses = _load_curses()
        curses.wrapper(lambda stdscr: CursesTui(stdscr, client, smoke=args.smoke).run())
        return 0
    except (TuiError, OSError) as exc:
        print(f"workspace-mcp-manager-tui: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # curses.error type is unavailable until import
        if curses is not None and isinstance(exc, curses.error):
            print(f"workspace-mcp-manager-tui: terminal initialization failed: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
