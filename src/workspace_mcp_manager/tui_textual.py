from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import subprocess
import webbrowser
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.worker import get_current_worker
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input as TextualInput,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from . import __version__
from .tui import (
    SETTINGS_FIELDS,
    GenerationGate,
    ManagerClient,
    SettingsField,
    TuiError,
    TuiOutcomeUnknown,
    get_nested,
    parse_settings_value,
    project_v1_to_v2,
    semantic_plan_diff,
)


HOST_CLIPBOARD_MAX_BYTES = 256 * 1024


def _windows_powershell() -> str | None:
    """Resolve Windows PowerShell for explicit WSL clipboard interop."""

    discovered = shutil.which("powershell.exe")
    if discovered:
        return discovered
    fallback = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    return str(fallback) if fallback.is_file() else None


def _copy_to_host_clipboard(text: str) -> bool:
    """Best-effort Windows clipboard write in addition to Textual OSC52/local copy."""

    powershell = _windows_powershell()
    if powershell is None:
        return False
    command = (
        "$enc=[System.Text.UTF8Encoding]::new($false);"
        "[Console]::InputEncoding=$enc;"
        "$text=[Console]::In.ReadToEnd();"
        "Set-Clipboard -Value $text"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _read_host_clipboard() -> str | None:
    """Read the Windows clipboard only for an explicit paste action."""

    powershell = _windows_powershell()
    if powershell is None:
        return None
    command = (
        "$enc=[System.Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding=$enc;"
        "[Console]::Out.Write((Get-Clipboard -Raw))"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or len(completed.stdout) > HOST_CLIPBOARD_MAX_BYTES:
        return None
    try:
        return completed.stdout.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


class Input(TextualInput):
    """Textual Input with explicit WSL host-clipboard fallback for Ctrl+V."""

    BINDINGS = [
        *TextualInput.BINDINGS,
        Binding("ctrl+shift+c", "copy", "Copy selected text", show=False),
        Binding("ctrl+insert", "copy", "Copy selected text", show=False),
        Binding("ctrl+shift+v", "paste", "Paste text", show=False),
        Binding("shift+insert", "paste", "Paste text", show=False),
    ]

    def action_paste(self) -> None:
        host_text = _read_host_clipboard()
        if host_text is None:
            super().action_paste()
            return
        line = host_text.splitlines()[0] if host_text.splitlines() else host_text
        start, end = self.selection
        self.replace(line, start, end)


STATE_SYMBOLS = {
    "Healthy": "✓",
    "Ready": "✓",
    "Applied": "✓",
    "Clean": "✓",
    "Running": "✓",
    "Degraded": "!",
    "Pending": "!",
    "Not ready": "!",
    "Required": "!",
    "In progress": "!",
    "Failed": "✗",
    "Blocked": "✗",
    "Unknown": "?",
    "Not applicable": "—",
    "Stopped": "—",
}


def state_text(value: Any) -> str:
    text = str(value if value is not None else "Unknown")
    return f"{STATE_SYMBOLS.get(text, '•')} {text}"


def _error_code(exc: TuiError) -> str | None:
    payload = exc.payload
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    return str(error.get("code")) if isinstance(error, Mapping) and error.get("code") else None


def _json_text(value: Any, *, limit: int = 128_000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if len(text) > limit:
        return text[: limit - 32] + "\n… diagnostic output truncated\n"
    return text


def _semantic_lines(value: Any, prefix: str = "", *, depth: int = 0, max_depth: int = 3) -> list[str]:
    if depth > max_depth:
        return []
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            if key in {"ok", "snapshot", "raw", "desired"}:
                continue
            label = f"{prefix}{str(key).replace('_', ' ')}"
            if isinstance(item, Mapping):
                nested = _semantic_lines(item, label + " / ", depth=depth + 1, max_depth=max_depth)
                if nested:
                    lines.extend(nested)
                else:
                    lines.append(f"{label}: —")
            elif isinstance(item, list):
                if item:
                    lines.append(f"{label}: {len(item)} item(s)")
            elif item is not None:
                lines.append(f"{label}: {item}")
        return lines
    return []


def diagnostic_semantic_lines(payload: Mapping[str, Any]) -> list[str]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
    services = snapshot.get("services") if isinstance(snapshot.get("services"), Mapping) else {}
    probes = snapshot.get("probes") if isinstance(snapshot.get("probes"), Mapping) else {}
    mcp_probes = probes.get("mcp") if isinstance(probes.get("mcp"), Mapping) else {}
    tunnel_probes = probes.get("tunnel") if isinstance(probes.get("tunnel"), Mapping) else {}

    checks: list[tuple[str, bool | None]] = []
    for name, label in (("mcp", "MCP service active"), ("tunnel", "Tunnel service active")):
        unit = services.get(name)
        if isinstance(unit, Mapping):
            active = unit.get("ActiveState")
            checks.append((label, active == "active" if active is not None else None))
        else:
            checks.append((label, None))

    discovery = mcp_probes.get("discovery") if isinstance(mcp_probes.get("discovery"), Mapping) else {}
    initialize = mcp_probes.get("initialize") if isinstance(mcp_probes.get("initialize"), Mapping) else {}
    health = tunnel_probes.get("health") if isinstance(tunnel_probes.get("health"), Mapping) else {}
    readiness = tunnel_probes.get("readiness") if isinstance(tunnel_probes.get("readiness"), Mapping) else {}
    for label, item in (
        ("MCP discovery", discovery),
        ("MCP initialize", initialize),
        ("Tunnel health", health),
        ("Tunnel ready", readiness),
    ):
        status = item.get("status") if isinstance(item, Mapping) else None
        checks.append((label, 200 <= status < 300 if isinstance(status, int) else None))

    lines: list[str] = []
    for label, outcome in checks:
        symbol = "✓" if outcome is True else ("✗" if outcome is False else "?")
        word = "Healthy" if outcome is True else ("Failed" if outcome is False else "Unknown")
        lines.append(f"{symbol} {label}: {word}")

    classifications = snapshot.get("classifications")
    if isinstance(classifications, list) and classifications:
        lines.append(f"! Diagnostic classifications: {len(classifications)}")
        for item in classifications[:10]:
            if isinstance(item, Mapping):
                label = item.get("classification") or item.get("code") or item.get("reason")
                if label:
                    lines.append(f"  ! {label}")
    if checks and all(outcome is True for _, outcome in checks) and not classifications:
        lines.append("✓ No problems detected")
    return lines


class ConfirmModal(ModalScreen[bool]):
    CSS = """
    ConfirmModal { align: center middle; }
    #confirm-box { width: 64; max-width: 92%; height: auto; border: round $warning; padding: 1 2; background: $surface; }
    #confirm-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, title: str, message: str, *, destructive: bool = False) -> None:
        super().__init__()
        self.dialog_title = title
        self.message = message
        self.destructive = destructive

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"[b]{self.dialog_title}[/b]")
            yield Static(self.message)
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel", id="cancel")
                yield Button(
                    "Confirm",
                    id="confirm",
                    variant="error" if self.destructive else "primary",
                )

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class AccessModal(ModalScreen[dict[str, str] | None]):
    CSS = """
    AccessModal { align: center middle; }
    #access-box { width: 72; max-width: 94%; height: auto; max-height: 90%; border: round $accent; padding: 1 2; background: $surface; }
    #access-error { color: $error; height: auto; min-height: 1; }
    .field-label { margin-top: 1; color: $text-muted; }
    .modal-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, entry: Mapping[str, Any] | None = None, *, forced_mode: str | None = None) -> None:
        super().__init__()
        self.entry = dict(entry) if entry else {}
        self.forced_mode = forced_mode

    def compose(self) -> ComposeResult:
        mode = self.forced_mode or str(self.entry.get("mode", "ro"))
        with Vertical(id="access-box"):
            yield Static("[b]Access[/b]")
            yield Label("Mode", classes="field-label")
            yield Select(
                (("Read-only", "ro"), ("Read-write", "rw")),
                value=mode,
                allow_blank=False,
                id="access-mode",
                disabled=self.forced_mode is not None,
            )
            yield Label("Alias", classes="field-label")
            yield Input(value=str(self.entry.get("alias", "")), placeholder="models", id="access-alias")
            yield Label("Path", classes="field-label")
            yield Input(value=str(self.entry.get("path", "")), placeholder="/srv/models", id="access-path")
            yield Static("", id="access-error")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id != "save":
            return
        mode_value = self.query_one("#access-mode", Select).value
        mode = str(mode_value)
        alias = self.query_one("#access-alias", Input).value.strip()
        path = self.query_one("#access-path", Input).value.strip()
        if mode not in {"ro", "rw"} or not alias or not path.startswith("/"):
            self.query_one("#access-error", Static).update("Mode, alias, and an absolute path are required.")
            return
        self.dismiss({"mode": mode, "alias": alias, "path": path})


class BaseScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("?", "help", "Help"),
        Binding("ctrl+shift+c", "copy_text", "Copy selected text", show=False),
        Binding("ctrl+insert", "copy_text", "Copy selected text", show=False),
    ]

    @property
    def manager_app(self) -> "WorkspaceManagerApp":
        app = self.app
        assert isinstance(app, WorkspaceManagerApp)
        return app

    @property
    def client(self) -> ManagerClient:
        return self.manager_app.client

    def set_status(self, text: str) -> None:
        try:
            self.query_one("#screen-status", Static).update(text)
        except Exception:
            pass

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()
        elif not isinstance(self, DashboardScreen):
            self.app.switch_screen(DashboardScreen())
        else:
            self.app.exit()

    def action_refresh(self) -> None:
        refresh = getattr(self, "refresh_authoritative", None)
        if callable(refresh):
            refresh()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def set_mutation_busy(self, busy: bool, *, uncertain: bool = False) -> None:
        for button in self.query(".mutation-action"):
            if isinstance(button, Button):
                button.disabled = busy
        if uncertain:
            self.set_status("! Outcome unknown — waiting for manager completion and authoritative refresh")

    def handle_mutation_error(self, exc: TuiError, label: str) -> None:
        self.set_status(f"✗ {label}: {exc}")


class HelpScreen(BaseScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("[b]Workspace MCP Manager — Help[/b]", classes="title")
            yield Static(
                "↑ ↓ ← →  navigate\n"
                "Enter      open / primary action\n"
                "Esc        back / cancel\n"
                "Tab        next control / section\n"
                "Space      toggle/select\n"
                "Ctrl+P     command palette\n"
                "Ctrl+C     copy selected text to terminal/host clipboard\n"
                "Ctrl+Shift+C / Ctrl+Insert  copy fallback\n"
                "Ctrl+X     cut selected input text\n"
                "Ctrl+V     paste into input (host clipboard on WSL, local fallback)\n"
                "Ctrl+Shift+V / Shift+Insert paste fallback\n"
                "F6         toggle terminal-selection mode (mouse released)\n"
                "Ctrl+Q     quit\n"
                "?          this help\n\n"
                "State is manager-authoritative. Authentication is external. Raw JSON is diagnostic/read-only."
            )
            yield Static("", id="screen-status")
        yield Footer()


class RawJsonScreen(BaseScreen):
    def __init__(self, title: str, payload: Any) -> None:
        super().__init__()
        self.raw_title = title
        self.payload = payload

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static(f"[b]{self.raw_title}[/b]", classes="title")
            yield TextArea(
                _json_text(self.payload),
                language="json",
                read_only=True,
                show_line_numbers=True,
                id="raw-json",
            )
            yield Static("Read-only diagnostic evidence", id="screen-status")
        yield Footer()


class DashboardScreen(BaseScreen):
    def __init__(self) -> None:
        super().__init__()
        self.gate = GenerationGate()
        self.instance_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static("[b]Overview[/b]", classes="title")
            yield Static("Loading manager state…", id="host-summary", classes="card")
            yield Static("", id="attention-summary", classes="card")
            yield DataTable(id="instances", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="actions"):
                yield Button("Open", id="open", variant="primary")
                yield Button("New instance", id="new")
                yield Button("Host", id="host")
                yield Button("Refresh", id="refresh")
            yield Static("", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#instances", DataTable)
        table.add_columns("Instance", "Desired", "Runtime", "Health", "Tunnel", "Changes")
        if self.manager_app.initial_load:
            self.refresh_authoritative()

    def refresh_authoritative(self) -> None:
        generation = self.gate.next("dashboard")
        self.query_one("#instances", DataTable).loading = True
        self.set_status("Refreshing authoritative fleet state…")
        self._refresh_worker(generation)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _refresh_worker(self, generation: int) -> None:
        worker = get_current_worker()
        try:
            summaries = self.client.summaries()
            host = self.client.host_overview()
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._apply_error, generation, exc)
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_refresh, generation, summaries, host)

    def _apply_error(self, generation: int, exc: TuiError) -> None:
        if not self.gate.accepts("dashboard", generation):
            return
        self.query_one("#instances", DataTable).loading = False
        self.set_status(f"✗ Refresh failed: {exc}")

    def _apply_refresh(self, generation: int, summaries: Mapping[str, Any], host: Mapping[str, Any]) -> None:
        if not self.gate.accepts("dashboard", generation):
            return
        table = self.query_one("#instances", DataTable)
        table.clear()
        self.instance_ids = []
        instances = summaries.get("instances") if isinstance(summaries.get("instances"), list) else []
        for item in instances:
            if not isinstance(item, Mapping):
                continue
            instance_id = str(item.get("instance_id", "?"))
            self.instance_ids.append(instance_id)
            reconciliation = str(item.get("reconciliation", "Unknown"))
            changes = "—" if reconciliation == "Applied" else reconciliation
            table.add_row(
                instance_id,
                str(item.get("desired_lifecycle", "Unknown")),
                state_text(item.get("runtime")),
                state_text(item.get("health")),
                state_text(item.get("tunnel")),
                changes,
                key=instance_id,
            )
        counts = summaries.get("counts") if isinstance(summaries.get("counts"), Mapping) else {}
        self.query_one("#attention-summary", Static).update(
            "Instances: {instances}   Attention: {attention}   Pending: {pending}   Blocked: {blocked}".format(
                instances=counts.get("instances", len(self.instance_ids)),
                attention=counts.get("attention", "?"),
                pending=counts.get("pending", "?"),
                blocked=counts.get("blocked", "?"),
            )
        )
        inspect = host.get("inspect") if isinstance(host.get("inspect"), Mapping) else {}
        platform = inspect.get("platform") or inspect.get("host") or "manager host"
        self.query_one("#host-summary", Static).update(f"Host: {platform}   Toolkit state: manager-authoritative")
        table.loading = False
        self.set_status("✓ Refreshed")

    def selected_instance(self) -> str | None:
        if not self.instance_ids:
            return None
        row = self.query_one("#instances", DataTable).cursor_coordinate.row
        if 0 <= row < len(self.instance_ids):
            return self.instance_ids[row]
        return self.instance_ids[0]

    def open_selected(self) -> None:
        instance_id = self.selected_instance()
        if instance_id:
            self.app.push_screen(InstanceScreen(instance_id))

    @on(DataTable.RowSelected, "#instances")
    def row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if 0 <= row < len(self.instance_ids):
            self.app.push_screen(InstanceScreen(self.instance_ids[row]))

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "open":
                self.open_selected()
            case "new":
                self.app.push_screen(NewInstanceScreen())
            case "host":
                self.app.push_screen(HostScreen())
            case "refresh":
                self.refresh_authoritative()


class InstanceScreen(BaseScreen):
    def __init__(self, instance_id: str) -> None:
        super().__init__()
        self.instance_id = instance_id
        self.gate = GenerationGate()
        self.summary_payload: Mapping[str, Any] = {}
        self.show_payload: Mapping[str, Any] = {}
        self.access_payload: Mapping[str, Any] = {}
        self.git_payload: Mapping[str, Any] = {}
        self.github_access_payload: Mapping[str, Any] = {}
        self.access_entries: list[dict[str, Any]] = []
        self.pending_access: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static(f"[b]{self.instance_id}[/b]", classes="title")
            yield Static("Loading…", id="instance-header", classes="card")
            with Horizontal(classes="actions"):
                yield Button("Start", id="start", classes="mutation-action")
                yield Button("Stop", id="stop", classes="mutation-action")
                yield Button("Restart", id="restart", classes="mutation-action")
                yield Button("Review changes", id="review", variant="primary")
                yield Button("Remove deployment", id="remove", variant="error", classes="mutation-action")
            with TabbedContent(initial="overview"):
                with TabPane("Overview", id="overview"):
                    with VerticalScroll():
                        yield Static("", id="overview-content")
                with TabPane("Access", id="access"):
                    yield DataTable(id="access-table", cursor_type="row", zebra_stripes=True)
                    with Horizontal(classes="actions"):
                        yield Button("Add read-only", id="access-add-ro", classes="mutation-action")
                        yield Button("Add read-write", id="access-add-rw", classes="mutation-action")
                        yield Button("Edit", id="access-edit", classes="mutation-action")
                        yield Button("Remove", id="access-remove", classes="mutation-action")
                with TabPane("Git & GitHub", id="git"):
                    with VerticalScroll():
                        yield Static("", id="git-content")
                    with Horizontal(classes="actions"):
                        yield Button("Re-check GitHub access", id="github-access-verify")
                        yield Button("Configure GitHub access", id="github-access-configure", classes="mutation-action")
                        yield Button("Enable managed GitHub access", id="github-access-enable")
                        yield Button("Create/manage GitHub token", id="github-access-token")
                with TabPane("Diagnostics", id="diagnostics"):
                    with VerticalScroll():
                        yield Static("Run diagnostics to collect current manager evidence.", id="diagnostics-content")
                    with Horizontal(classes="actions"):
                        yield Button("Run diagnostics", id="diagnose")
                        yield Button("Open logs", id="logs")
                with TabPane("Settings", id="settings"):
                    with VerticalScroll():
                        yield Static("", id="settings-content")
                    with Horizontal(classes="actions"):
                        yield Button("Edit settings", id="settings-edit", classes="mutation-action")
                        yield Button("Raw declaration", id="raw-declaration")
            yield Static("", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#access-table", DataTable)
        table.add_columns("Mode", "Alias", "Path")
        self.refresh_authoritative()

    def refresh_authoritative(self) -> None:
        generation = self.gate.next("instance")
        self.set_status("Refreshing authoritative instance state…")
        self._refresh_worker(generation)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _refresh_worker(self, generation: int) -> None:
        worker = get_current_worker()
        try:
            summary = self.client.summary(self.instance_id)
            show = self.client.show(self.instance_id)
            access = self.client.access_list(self.instance_id)
            git = self.client.git(self.instance_id)
            github_method = getattr(self.client, "github_access_status", None)
            github_access = github_method(self.instance_id) if callable(github_method) else {}
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._apply_error, generation, exc)
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_refresh, generation, summary, show, access, git, github_access)

    def _apply_error(self, generation: int, exc: TuiError) -> None:
        if self.gate.accepts("instance", generation):
            self.set_status(f"✗ Refresh failed: {exc}")

    def _apply_refresh(
        self,
        generation: int,
        summary: Mapping[str, Any],
        show: Mapping[str, Any],
        access: Mapping[str, Any],
        git: Mapping[str, Any],
        github_access: Mapping[str, Any],
    ) -> None:
        if not self.gate.accepts("instance", generation):
            return
        self.summary_payload = summary
        self.show_payload = show
        self.access_payload = access
        self.git_payload = git
        self.github_access_payload = github_access
        self.pending_access = None
        self._render_header()
        self._render_overview()
        self._render_access()
        self._render_git()
        self._render_settings_summary()
        self.set_status("✓ Authoritative state refreshed")
        self.manager_app.mark_authority_verified(self.instance_id)

    def _render_header(self) -> None:
        summary = self.summary_payload
        access = summary.get("access") if isinstance(summary.get("access"), Mapping) else {}
        git = summary.get("git") if isinstance(summary.get("git"), Mapping) else {}
        text = (
            f"Workspace: {summary.get('workspace_path', '?')}\n"
            f"Desired: {summary.get('desired_lifecycle', 'Unknown')}   "
            f"Runtime: {state_text(summary.get('runtime'))}   "
            f"Health: {state_text(summary.get('health'))}\n"
            f"Tunnel: {state_text(summary.get('tunnel'))}   "
            f"Changes: {state_text(summary.get('reconciliation'))}   "
            f"Recovery: {state_text(summary.get('recovery'))}\n"
            f"Git: {git.get('remote_protocol') or 'not configured'}   "
            f"Access: {access.get('read_only_count', 0)} RO / {access.get('read_write_count', 0)} RW"
        )
        self.query_one("#instance-header", Static).update(text)

    def _render_overview(self) -> None:
        attention = self.summary_payload.get("attention_reasons")
        if isinstance(attention, list) and attention:
            attention_text = "\n".join(f"! {item}" for item in attention)
        else:
            attention_text = "✓ No operator attention requested by the current projection"
        self.query_one("#overview-content", Static).update(
            f"[b]State[/b]\n{attention_text}\n\n"
            f"Declaration fingerprint: {self.summary_payload.get('declaration_fingerprint', '?')}\n"
            f"Plan fingerprint: {self.summary_payload.get('plan_fingerprint') or 'unavailable'}"
        )

    def _render_access(self) -> None:
        table = self.query_one("#access-table", DataTable)
        table.clear()
        entries = self.access_payload.get("entries") if isinstance(self.access_payload.get("entries"), list) else []
        self.access_entries = [dict(item) for item in entries if isinstance(item, Mapping)]
        for item in self.access_entries:
            table.add_row(str(item.get("mode", "?")).upper(), str(item.get("alias", "?")), str(item.get("path", "?")))

    def _render_git(self) -> None:
        projection = self.summary_payload.get("git") if isinstance(self.summary_payload.get("git"), Mapping) else {}
        github = self.github_access_payload
        profile = github.get("profile") if isinstance(github.get("profile"), Mapping) else {}
        cli = github.get("github_cli") if isinstance(github.get("github_cli"), Mapping) else {}
        authentication = github.get("authentication") if isinstance(github.get("authentication"), Mapping) else {}
        verification = github.get("verification") if isinstance(github.get("verification"), Mapping) else {}
        transport = github.get("git_transport") if isinstance(github.get("git_transport"), Mapping) else {}
        lines = [
            "[b]Configuration managed by manager[/b]",
            f"Repository: {projection.get('workspace', '?')}",
            f"GitHub profile mode: {projection.get('github_mode', 'disabled')}",
            f"Git identity configured: {projection.get('identity_configured', False)}",
            f"Remote configured: {projection.get('remote_configured', False)}",
            f"Remote transport: {projection.get('remote_protocol') or 'not configured'}",
            f"Agent provider: {projection.get('agent_mode', 'none')}",
            "",
            "[b]GitHub CLI Access[/b]",
            f"Profile: {profile.get('mode', 'unknown')} / {profile.get('state', 'unknown')}",
            f"Path: {profile.get('path') or '—'}",
            f"GitHub CLI: {cli.get('version') or 'unavailable'} / {'supported' if cli.get('supported') else 'not qualified'}",
            f"Authentication: {authentication.get('state', 'unknown')}",
            f"Account: {authentication.get('account') or '—'}",
            f"Verification: profile={verification.get('profile', 'unknown')} manager={verification.get('manager_environment', 'unknown')} MCP={verification.get('mcp_execution', 'unknown')} repo={verification.get('repository_read', 'unknown')}",
            f"Checked: {verification.get('observed_at') or 'never'} / {verification.get('freshness', 'never')}",
            f"Reason: {verification.get('reason_code') or '—'}",
            "",
            "[b]Git Transport[/b]",
            f"Protocol: {transport.get('protocol') or projection.get('remote_protocol') or 'not configured'}",
            f"Status: {transport.get('status', 'unknown')}",
            "GitHub CLI/API authentication and repository Git transport are independent authorities.",
        ]
        lines.extend("• " + line for line in _semantic_lines(self.git_payload, max_depth=2)[:30])
        self.query_one("#git-content", Static).update("\n".join(lines))
        setup = github.get("setup") if isinstance(github.get("setup"), Mapping) else {}
        mode = profile.get("mode")
        configure = self.query_one("#github-access-configure", Button)
        enable = self.query_one("#github-access-enable", Button)
        verify_button = self.query_one("#github-access-verify", Button)
        configure.styles.display = "block" if mode == "managed" else "none"
        configure.disabled = not bool(setup.get("configure_allowed"))
        configure.label = "Reconfigure GitHub access" if authentication.get("state") == "authenticated" else "Configure GitHub access"
        enable.styles.display = "block" if mode == "disabled" else "none"
        verify_button.disabled = mode == "disabled"

    def _render_settings_summary(self) -> None:
        desired = self.show_payload.get("desired") if isinstance(self.show_payload.get("desired"), Mapping) else {}
        self.query_one("#settings-content", Static).update(
            f"Config version: {desired.get('config_version', '?')}\n"
            f"Workspace: {desired.get('workspace_path', '?')}\n"
            f"Common settings are edited through structured controls.\n"
            f"Advanced → Raw declaration is read-only."
        )

    def selected_access(self) -> dict[str, Any] | None:
        if not self.access_entries:
            return None
        row = self.query_one("#access-table", DataTable).cursor_coordinate.row
        if 0 <= row < len(self.access_entries):
            return self.access_entries[row]
        return self.access_entries[0]

    def _expected_fingerprint(self) -> str | None:
        value = self.summary_payload.get("declaration_fingerprint")
        return str(value) if value else None

    def _open_access_modal(self, entry: Mapping[str, Any] | None = None, *, mode: str | None = None) -> None:
        self.app.push_screen(AccessModal(entry, forced_mode=mode), partial(self._access_modal_result, entry))

    def _access_modal_result(self, original: Mapping[str, Any] | None, result: dict[str, str] | None) -> None:
        if result is None:
            return
        fingerprint = self._expected_fingerprint()
        if fingerprint is None:
            self.set_status("? Declaration fingerprint unavailable; refresh before editing access")
            return
        self.pending_access = {"original": dict(original) if original else None, "result": dict(result)}
        if original:
            operation = lambda: self.client.access_update(
                self.instance_id,
                existing_alias=str(original.get("alias")),
                mode=result["mode"],
                alias=result["alias"],
                path=result["path"],
                expected_current_fingerprint=fingerprint,
            )
            label = "Edit access"
        else:
            operation = lambda: self.client.access_add(
                self.instance_id,
                mode=result["mode"],
                alias=result["alias"],
                path=result["path"],
                expected_current_fingerprint=fingerprint,
            )
            label = "Add access"
        self.manager_app.start_mutation(label, self.instance_id, operation)

    def _confirm_access_remove(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        entry = self.selected_access()
        fingerprint = self._expected_fingerprint()
        if entry is None or fingerprint is None:
            return
        self.pending_access = {"original": dict(entry), "remove": True}
        self.manager_app.start_mutation(
            "Remove access",
            self.instance_id,
            lambda: self.client.access_remove(
                self.instance_id,
                alias=str(entry.get("alias")),
                expected_current_fingerprint=fingerprint,
            ),
        )

    def _confirm_remove_deployment(self, confirmed: bool | None) -> None:
        if confirmed:
            self.manager_app.start_mutation(
                "Remove deployment",
                self.instance_id,
                lambda: self.client.lifecycle("remove", self.instance_id),
            )

    def handle_mutation_error(self, exc: TuiError, label: str) -> None:
        super().handle_mutation_error(exc, label)
        if _error_code(exc) == "STALE_STATE" and self.pending_access:
            original = self.pending_access.get("original")
            result = self.pending_access.get("result")
            if isinstance(result, Mapping):
                self.set_status(f"! Stale access state; draft preserved. {exc}")
                self.app.push_screen(AccessModal(result), partial(self._access_modal_result, original if isinstance(original, Mapping) else None))

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id in {"start", "stop", "restart"}:
            self.manager_app.start_mutation(
                button_id.capitalize(),
                self.instance_id,
                lambda action=button_id: self.client.lifecycle(action, self.instance_id),
            )
        elif button_id == "review":
            self.app.push_screen(ReviewChangesScreen(self.instance_id))
        elif button_id == "remove":
            self.app.push_screen(
                ConfirmModal(
                    "Remove deployment",
                    f"Remove deployed resources for {self.instance_id}? The declaration remains manager-authoritative.",
                    destructive=True,
                ),
                self._confirm_remove_deployment,
            )
        elif button_id == "access-add-ro":
            self._open_access_modal(mode="ro")
        elif button_id == "access-add-rw":
            self._open_access_modal(mode="rw")
        elif button_id == "access-edit":
            entry = self.selected_access()
            if entry:
                self._open_access_modal(entry)
        elif button_id == "access-remove":
            entry = self.selected_access()
            if entry:
                self.app.push_screen(
                    ConfirmModal("Remove access", f"Remove access alias {entry.get('alias')}?"),
                    self._confirm_access_remove,
                )
        elif button_id == "diagnose":
            self.run_diagnostics()
        elif button_id == "logs":
            self.app.push_screen(LogsScreen(self.instance_id))
        elif button_id == "settings-edit":
            desired = self.show_payload.get("desired") if isinstance(self.show_payload.get("desired"), Mapping) else None
            fingerprint = self._expected_fingerprint()
            if desired and fingerprint:
                self.app.push_screen(SettingsScreen(self.instance_id, desired, fingerprint))
        elif button_id == "raw-declaration":
            desired = self.show_payload.get("desired") if isinstance(self.show_payload.get("desired"), Mapping) else {}
            self.app.push_screen(RawJsonScreen(f"{self.instance_id} — Raw declaration", desired))
        elif button_id == "github-access-verify":
            self._github_verify_worker()
        elif button_id == "github-access-configure":
            self.manager_app.configure_github_foreground(self.instance_id)
        elif button_id == "github-access-enable":
            desired = self.show_payload.get("desired") if isinstance(self.show_payload.get("desired"), Mapping) else None
            fingerprint = self._expected_fingerprint()
            if desired and fingerprint:
                self.app.push_screen(SettingsScreen(self.instance_id, desired, fingerprint))
                self.set_status("Select managed GitHub profile mode, then Preview & Save before configuring credentials")
        elif button_id == "github-access-token":
            setup = self.github_access_payload.get("setup") if isinstance(self.github_access_payload.get("setup"), Mapping) else {}
            url = setup.get("token_management_url")
            if isinstance(url, str) and url.startswith("https://github.com/"):
                webbrowser.open(url)
                self.set_status("Opened manager-projected GitHub token management")

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _github_verify_worker(self) -> None:
        worker = get_current_worker()
        verify = getattr(self.client, "github_access_verify", None)
        if not callable(verify):
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, "GitHub access verification is unavailable in this frontend")
            return
        try:
            payload = verify(self.instance_id)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"✗ GitHub access verification failed: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._github_verified, payload)

    def _github_verified(self, payload: Mapping[str, Any]) -> None:
        self.github_access_payload = payload
        self._render_git()
        reason = payload.get("verification", {}).get("reason_code") if isinstance(payload.get("verification"), Mapping) else None
        self.set_status("✓ GitHub access re-checked" if not reason else f"! GitHub access re-checked: {reason}")

    def run_diagnostics(self) -> None:
        self.set_status("Running manager diagnostics…")
        self.query_one("#diagnostics-content", Static).loading = True
        self._diagnostics_worker()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _diagnostics_worker(self) -> None:
        worker = get_current_worker()
        try:
            payload = self.client.diagnose(self.instance_id)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._diagnostics_error, exc)
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._diagnostics_result, payload)

    def _diagnostics_error(self, exc: TuiError) -> None:
        widget = self.query_one("#diagnostics-content", Static)
        widget.loading = False
        widget.update(f"✗ Diagnostics failed: {exc}")
        self.set_status(f"✗ Diagnostics failed: {exc}")

    def _diagnostics_result(self, payload: Mapping[str, Any]) -> None:
        widget = self.query_one("#diagnostics-content", Static)
        widget.loading = False
        widget.update("\n".join(diagnostic_semantic_lines(payload)))
        self.set_status("✓ Diagnostic evidence collected")


class ReviewChangesScreen(BaseScreen):
    def __init__(self, instance_id: str) -> None:
        super().__init__()
        self.instance_id = instance_id
        self.gate = GenerationGate()
        self.plan_payload: Mapping[str, Any] = {}
        self.previous_plan: Mapping[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static(f"[b]{self.instance_id} — Review changes[/b]", classes="title")
            yield RichLog(id="plan-log", wrap=True, markup=True)
            with Horizontal(classes="actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply changes", id="apply", variant="primary", classes="mutation-action")
                yield Button("Raw plan", id="raw")
            yield Static("", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_authoritative()

    def refresh_authoritative(self) -> None:
        generation = self.gate.next("plan")
        self.set_status("Refreshing authoritative plan…")
        self._refresh_worker(generation)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _refresh_worker(self, generation: int) -> None:
        worker = get_current_worker()
        try:
            plan = self.client.plan(self.instance_id)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._apply_error, generation, exc)
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_plan, generation, plan)

    def _apply_error(self, generation: int, exc: TuiError) -> None:
        if self.gate.accepts("plan", generation):
            self.set_status(f"✗ Plan unavailable: {exc}")

    def _apply_plan(self, generation: int, plan: Mapping[str, Any]) -> None:
        if not self.gate.accepts("plan", generation):
            return
        log = self.query_one("#plan-log", RichLog)
        log.clear()
        if self.previous_plan is not None:
            diff = semantic_plan_diff(self.previous_plan, plan)
            if diff != ["No semantic operation changes"]:
                log.write("[yellow]Reviewed plan changed; review is required again:[/yellow]")
                for line in diff:
                    log.write(line)
                log.write("")
        self.plan_payload = dict(plan)
        semantic = plan.get("semantic_operations") if isinstance(plan.get("semantic_operations"), list) else []
        actionable = [item for item in semantic if isinstance(item, Mapping) and item.get("operation") != "NOOP"]
        if not plan.get("valid"):
            log.write("[red]✗ Reconciliation is blocked.[/red]")
        elif not actionable:
            log.write("[green]✓ No pending changes.[/green]")
        else:
            for item in actionable:
                log.write(f"• {item.get('description', item.get('operation'))}")
        warnings = plan.get("warnings") if isinstance(plan.get("warnings"), list) else []
        for warning in warnings:
            log.write(f"[yellow]! {warning}[/yellow]")
        log.write("")
        log.write(f"Desired fingerprint: {plan.get('desired_fingerprint', '?')}")
        log.write(f"Plan fingerprint: {plan.get('plan_fingerprint', '?')}")
        apply_button = self.query_one("#apply", Button)
        apply_button.disabled = not bool(plan.get("valid")) or not actionable
        self.set_status("Review the semantic operations before Apply")
        self.previous_plan = None

    def handle_mutation_error(self, exc: TuiError, label: str) -> None:
        if _error_code(exc) == "STALE_STATE":
            self.previous_plan = dict(self.plan_payload)
            self.set_status("! Reviewed plan is stale; loading the current plan for re-review")
            self.refresh_authoritative()
        else:
            super().handle_mutation_error(exc, label)

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_back()
        elif event.button.id == "raw":
            self.app.push_screen(RawJsonScreen(f"{self.instance_id} — Raw plan", self.plan_payload))
        elif event.button.id == "apply":
            fingerprint = self.plan_payload.get("plan_fingerprint")
            if fingerprint:
                self.manager_app.start_mutation(
                    "Apply changes",
                    self.instance_id,
                    lambda: self.client.apply(self.instance_id, expected_plan_fingerprint=str(fingerprint)),
                )


class SettingsScreen(BaseScreen):
    def __init__(self, instance_id: str, desired: Mapping[str, Any], fingerprint: str) -> None:
        super().__init__()
        self.instance_id = instance_id
        self.original = dict(desired)
        self.draft = project_v1_to_v2(desired) if desired.get("config_version") == 1 else copy.deepcopy(dict(desired))
        self.expected_fingerprint = fingerprint
        self.preview_payload: Mapping[str, Any] | None = None

    @staticmethod
    def _control_id(index: int) -> str:
        return f"setting-{index}"

    @staticmethod
    def _wrap_id(index: int) -> str:
        return f"setting-wrap-{index}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static(f"[b]{self.instance_id} — Settings[/b]", classes="title")
            with VerticalScroll(id="settings-form"):
                current_group = None
                for index, field in enumerate(SETTINGS_FIELDS):
                    if field.group != current_group:
                        current_group = field.group
                        yield Static(f"[b]{current_group}[/b]", classes="section-title")
                    value = get_nested(self.draft, field.path)
                    with Vertical(id=self._wrap_id(index), classes="form-field"):
                        yield Label(field.label)
                        control_id = self._control_id(index)
                        if field.kind == "bool":
                            yield Checkbox("Enabled", value=bool(value), id=control_id)
                        elif field.kind == "choice":
                            options = tuple((choice, choice) for choice in field.choices)
                            kwargs: dict[str, Any] = {"allow_blank": field.optional, "id": control_id}
                            if value is not None:
                                kwargs["value"] = str(value)
                            yield Select(options, **kwargs)
                        else:
                            yield Input(value="" if value is None else str(value), id=control_id)
            with Horizontal(classes="actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Preview & Save", id="save", variant="primary", classes="mutation-action")
                yield Button("Raw declaration", id="raw")
            yield Static("", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_applicability()
        if self.original.get("config_version") == 1:
            self.set_status("v1 declaration projected to v2 for editing; downgrade is not offered")

    def _values(self) -> dict[tuple[str, ...], Any]:
        values: dict[tuple[str, ...], Any] = {}
        for index, field in enumerate(SETTINGS_FIELDS):
            control = self.query_one(f"#{self._control_id(index)}")
            if isinstance(control, Checkbox):
                value: Any = control.value
            elif isinstance(control, Select):
                value = control.value
                if value is Select.BLANK:
                    value = ""
            elif isinstance(control, Input):
                value = control.value
            else:
                continue
            values[field.path] = value
        return values

    @staticmethod
    def _pointer(path: Sequence[str]) -> str:
        return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in path)

    def _parsed_values(self) -> dict[tuple[str, ...], Any]:
        raw = self._values()
        result: dict[tuple[str, ...], Any] = {}
        for field in SETTINGS_FIELDS:
            result[field.path] = parse_settings_value(field, raw[field.path])
        return result

    def _field_edits(self) -> list[dict[str, Any]]:
        """Translate controls into explicit edit intent without composing desired state."""

        values = self._parsed_values()
        original = self.draft
        edits: list[dict[str, Any]] = []

        composite_paths = {
            ("git", "identity", "name"),
            ("git", "identity", "email"),
            ("git", "remote", "name"),
            ("git", "remote", "protocol"),
        }
        for field in SETTINGS_FIELDS:
            if field.path in composite_paths:
                continue
            value = values[field.path]
            if value == get_nested(original, field.path):
                continue
            pointer = self._pointer(field.path)
            if value is None:
                edits.append({"path": pointer, "operation": "clear"})
            else:
                edits.append({"path": pointer, "operation": "set", "value": value})

        identity_name = values[("git", "identity", "name")]
        identity_email = values[("git", "identity", "email")]
        original_identity = get_nested(original, ("git", "identity"))
        if not identity_name and not identity_email:
            if original_identity is not None:
                edits.append({"path": "/git/identity", "operation": "clear"})
        elif not identity_name or not identity_email:
            raise ValueError("Git identity requires both name and email")
        else:
            identity = {"name": identity_name, "email": identity_email}
            if identity != original_identity:
                edits.append({"path": "/git/identity", "operation": "set", "value": identity})

        remote_name = values[("git", "remote", "name")]
        remote_protocol = values[("git", "remote", "protocol")]
        original_remote = get_nested(original, ("git", "remote"))
        if not remote_name and not remote_protocol:
            if original_remote is not None:
                edits.append({"path": "/git/remote", "operation": "clear"})
        elif not remote_name or not remote_protocol:
            raise ValueError("Git remote requires both name and protocol")
        else:
            remote = {"name": remote_name, "protocol": remote_protocol}
            if remote != original_remote:
                edits.append({"path": "/git/remote", "operation": "set", "value": remote})
        return edits

    def _candidate_request(self) -> dict[str, Any]:
        return {
            "candidate_request_version": 1,
            "workspace_path": str(self.draft["workspace_path"]),
            "field_edits": self._field_edits(),
            "access_edits": [],
        }

    def _sync_applicability(self) -> None:
        modes: dict[tuple[str, ...], str] = {}
        for index, field in enumerate(SETTINGS_FIELDS):
            if field.path not in {("github", "mode"), ("agent", "mode")}:
                continue
            control = self.query_one(f"#{self._control_id(index)}", Select)
            if control.value is not Select.BLANK:
                modes[field.path] = str(control.value)
        for index, field in enumerate(SETTINGS_FIELDS):
            wrapper = self.query_one(f"#{self._wrap_id(index)}")
            applicable = True
            if field.path in {("github", "config_dir"), ("github", "binary")}:
                applicable = modes.get(("github", "mode"), "disabled") == "external"
            elif field.path == ("agent", "ssh_auth_sock"):
                applicable = modes.get(("agent", "mode"), "none") == "external"
            wrapper.styles.display = "block" if applicable else "none"

    @on(Select.Changed)
    def select_changed(self, event: Select.Changed) -> None:
        if event.select.id and event.select.id.startswith("setting-"):
            self._sync_applicability()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_back()
        elif event.button.id == "raw":
            self.app.push_screen(RawJsonScreen(f"{self.instance_id} — Raw declaration", self.original))
        elif event.button.id == "save":
            try:
                request = self._candidate_request()
            except ValueError as exc:
                self.set_status(f"✗ {exc}")
                return
            self.set_status("Reconstructing settings candidate with manager authority…")
            self._preview_worker(request)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _preview_worker(self, request: Mapping[str, Any]) -> None:
        worker = get_current_worker()
        try:
            candidate_payload = self.client.candidate(request)
            candidate = candidate_payload.get("effective_declaration")
            if not isinstance(candidate, Mapping):
                raise TuiError("manager candidate did not return an effective declaration")
            preview = self.client.preview_declaration(candidate)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"✗ Candidate preview rejected: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._preview_ready, candidate, preview)

    def _preview_ready(self, candidate: Mapping[str, Any], preview: Mapping[str, Any]) -> None:
        self.preview_payload = preview
        if preview.get("errors"):
            self.set_status("✗ Candidate preview contains manager errors; draft preserved")
            return
        warnings = preview.get("warnings") if isinstance(preview.get("warnings"), list) else []
        suffix = f" ({len(warnings)} warning(s))" if warnings else ""
        self.set_status(f"✓ Candidate valid{suffix}; saving conditionally…")
        self.manager_app.start_mutation(
            "Save settings",
            self.instance_id,
            lambda: self.client.save_declaration(
                "update",
                candidate,
                expected_current_fingerprint=self.expected_fingerprint,
            ),
        )

    def handle_mutation_error(self, exc: TuiError, label: str) -> None:
        if _error_code(exc) == "STALE_STATE":
            self.set_status(f"! Stale settings rejected; your draft is preserved. {exc}")
        else:
            super().handle_mutation_error(exc, label)

    def refresh_authoritative(self) -> None:
        # Deliberately do not overwrite an unsaved draft on a settings screen.
        self.set_status("Draft preserved. Cancel and reopen settings to load newer authoritative values.")


class LogsScreen(BaseScreen):
    def __init__(self, instance_id: str) -> None:
        super().__init__()
        self.instance_id = instance_id
        self.raw_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static(f"[b]{self.instance_id} — Logs[/b]", classes="title")
            with Horizontal(classes="actions"):
                yield Select(
                    (("All", "all"), ("MCP", "mcp"), ("Tunnel", "tunnel"), ("Recovery", "recovery")),
                    value="all",
                    allow_blank=False,
                    id="log-category",
                )
                yield Input(placeholder="Search loaded log text", id="log-search")
                yield Button("Load", id="log-load")
            yield RichLog(id="logs", wrap=False, markup=False)
            yield Static("", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_authoritative()

    def refresh_authoritative(self) -> None:
        value = self.query_one("#log-category", Select).value
        category = "all" if value is Select.BLANK else str(value)
        self.set_status(f"Loading {category} logs from manager…")
        self._load_worker(category)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _load_worker(self, category: str) -> None:
        worker = get_current_worker()
        try:
            payload = self.client.logs(self.instance_id, category=category, lines=200)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"✗ Log read failed: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_logs, payload)

    def _apply_logs(self, payload: Mapping[str, Any]) -> None:
        lines: list[str] = []
        journals = payload.get("journals") if isinstance(payload.get("journals"), Mapping) else {}
        files = payload.get("files") if isinstance(payload.get("files"), Mapping) else {}
        recovery = payload.get("recovery") if isinstance(payload.get("recovery"), Mapping) else {}
        for source, text in journals.items():
            lines.append(f"=== journal: {source} ===")
            lines.extend(str(text).splitlines())
        for source, text in files.items():
            lines.append(f"=== file: {source} ===")
            lines.extend(str(text).splitlines())
        for source, evidence in recovery.items():
            lines.append(f"=== recovery: {source} ===")
            lines.extend(_json_text(evidence, limit=20_000).splitlines())
        self.raw_lines = lines[-5000:]
        self._render_filter()
        self.set_status(f"✓ Loaded {len(self.raw_lines)} bounded log line(s)")

    def _render_filter(self) -> None:
        query = self.query_one("#log-search", Input).value.casefold().strip()
        log = self.query_one("#logs", RichLog)
        log.clear()
        visible = self.raw_lines if not query else [line for line in self.raw_lines if query in line.casefold()]
        for line in visible[-5000:]:
            log.write(line)

    @on(Input.Changed, "#log-search")
    def search_changed(self, event: Input.Changed) -> None:
        self._render_filter()

    @on(Button.Pressed, "#log-load")
    def load_pressed(self, event: Button.Pressed) -> None:
        self.refresh_authoritative()


class HostScreen(BaseScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static("[b]Host[/b]", classes="title")
            yield Static("Loading manager host projection…", id="host-content", classes="card")
            with Horizontal(classes="actions"):
                yield Button("Host doctor", id="doctor")
                yield Button("Refresh", id="refresh")
            yield Static("", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_authoritative()

    def refresh_authoritative(self) -> None:
        self.set_status("Refreshing manager host evidence…")
        self._host_worker(False)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _host_worker(self, doctor: bool) -> None:
        worker = get_current_worker()
        try:
            payload = self.client.host_doctor() if doctor else self.client.host_overview()
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"✗ Host operation failed: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_host, payload, doctor)

    def _apply_host(self, payload: Mapping[str, Any], doctor: bool) -> None:
        lines = _semantic_lines(payload, max_depth=3)
        self.query_one("#host-content", Static).update("\n".join(lines[:100]) or "? Host evidence unavailable")
        self.set_status("✓ Host doctor completed" if doctor else "✓ Host state refreshed")

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "doctor":
            self.set_status("Running host doctor…")
            self._host_worker(True)
        elif event.button.id == "refresh":
            self.refresh_authoritative()


class DirectoryPicker(ModalScreen[Path | None]):
    """Filesystem-only directory selector; semantic interpretation stays manager-side."""

    def __init__(self, selected: Path) -> None:
        super().__init__()
        self.selected = selected if selected.is_dir() else selected.parent

    def compose(self) -> ComposeResult:
        with Vertical(classes="page"):
            yield Static("[b]Choose directory[/b]", classes="title")
            yield Static(str(self.selected), id="directory-selection", classes="card")
            yield DirectoryTree("/", id="directory-tree")
            with Horizontal(classes="actions"):
                yield Button("Use selected", id="directory-use", variant="primary")
                yield Button("Cancel", id="directory-cancel")

    @on(DirectoryTree.DirectorySelected)
    def directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.selected = Path(event.path)
        self.query_one("#directory-selection", Static).update(str(self.selected))

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "directory-use":
            self.dismiss(self.selected)
        elif event.button.id == "directory-cancel":
            self.dismiss(None)


class NewInstanceScreen(BaseScreen):
    STEPS = ("Workspace", "Runtime", "Tunnel", "Git, GitHub & Folder Access", "Review")
    REUSABLE_EDIT_PATHS = {
        "/mcp/binary",
        "/mcp/host",
        "/mcp/port",
        "/mcp/permission_mode",
        "/mcp/shell_env_inherit",
        "/mcp/exec_path",
        "/mcp/external_roots",
        "/tunnel/binary",
        "/tunnel/health_host",
        "/tunnel/health_port",
        "/tunnel/env_file",
        "/recovery/admission_guard_enabled",
        "/recovery/guard_interval_seconds",
        "/recovery/recovery_cooldown_seconds",
    }

    def __init__(self) -> None:
        super().__init__()
        self.step = 0
        self.field_edits: list[dict[str, Any]] = []
        self.access_edits: list[dict[str, Any]] = []
        self.candidate_payload: Mapping[str, Any] | None = None
        self.preview_payload: Mapping[str, Any] | None = None
        self.workspace_identity: str | None = None
        self.existing_instance_id: str | None = None
        self.setup_action_url: str | None = None
        self._dirty_paths: set[str] = set()
        self._workspace_dirty = False
        self._populating = True
        self._candidate_gate = GenerationGate()
        self._active_request_identity: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static("[b]New Instance[/b]", classes="title")
            yield Static("", id="wizard-progress", classes="card")
            with VerticalScroll(id="wizard-body"):
                with Vertical(id="wizard-basics", classes="wizard-step"):
                    yield Label("Start from")
                    yield Select(
                        (("Defaults", "defaults"), ("Copy existing", "copy")),
                        value="defaults",
                        allow_blank=False,
                        id="new-source-mode",
                    )
                    with Vertical(id="copy-source-row"):
                        yield Label("Copy configuration from")
                        yield Select([], allow_blank=True, id="new-copy-source")
                    yield Label("Workspace")
                    with Horizontal(classes="actions"):
                        yield Input(placeholder="/home/user/repos/example", id="new-workspace")
                        yield Button("Browse…", id="new-workspace-browse")
                    yield Label("Instance ID")
                    yield Input(placeholder="manager-suggested-id", id="new-instance-id")
                    with Vertical(id="existing-workspace-row"):
                        yield Static("", id="new-existing-workspace")
                        yield Button("Open existing instance", id="new-open-existing", variant="primary")
                with Vertical(id="wizard-runtime", classes="wizard-step"):
                    yield Label("Permission mode")
                    yield Select(
                        (("safe", "safe"), ("trusted", "trusted"), ("dangerous", "dangerous")),
                        value="trusted",
                        allow_blank=False,
                        id="new-permission",
                    )
                    yield Label("MCP port")
                    yield Input(placeholder="7654", id="new-mcp-port")
                    yield Static("", id="new-mcp-port-state", classes="card")
                    yield Label("Known ports")
                    yield Static("Loading manager port projection…", id="new-runtime-known-ports", classes="card")
                with Vertical(id="wizard-tunnel", classes="wizard-step"):
                    yield Label("Tunnel ID")
                    yield Input(placeholder="tunnel_…", id="new-tunnel-id")
                    yield Label("Tunnel health port")
                    yield Input(placeholder="7070", id="new-health-port")
                    yield Static("", id="new-health-port-state", classes="card")
                    yield Label("Known ports")
                    yield Static("Loading manager port projection…", id="new-tunnel-known-ports", classes="card")
                    yield Label("Authentication")
                    yield Static("Loading external authentication status…", id="new-auth-status", classes="card")
                    yield Button("Open credential setup", id="new-auth-setup")
                with Vertical(id="wizard-git", classes="wizard-step"):
                    yield Label("Git Repository")
                    yield Static("Loading manager Git discovery…", id="new-git-summary", classes="card")
                    yield Label("GitHub CLI Access")
                    yield Select(
                        (("Managed per-instance GitHub access", "managed"), ("Not enabled", "disabled")),
                        value="managed",
                        allow_blank=False,
                        id="new-github-mode",
                    )
                    yield Select(
                        (("Configure during creation", "now"), ("Configure later", "later")),
                        value="later",
                        allow_blank=False,
                        id="new-github-configure-timing",
                    )
                    yield Static("Setup occurs only after declaration registration.", id="new-github-summary", classes="card")
                    yield Button("Create/manage GitHub token", id="new-github-token")
                    yield Label("Git Transport")
                    yield Static("Loading repository transport…", id="new-git-transport", classes="card")
                    yield Label("Folder Access")
                    yield Static("No additional folders configured", id="new-access-summary", classes="card")
                    yield Select([], allow_blank=True, id="new-access-selection")
                    with Horizontal(classes="actions"):
                        yield Button("+ Read-only folder", id="new-access-add-ro")
                        yield Button("+ Read-write folder", id="new-access-add-rw")
                        yield Button("Remove selected", id="new-access-remove")
                with Vertical(id="wizard-review", classes="wizard-step"):
                    yield RichLog(id="wizard-review-log", wrap=True, markup=True)
            with Horizontal(classes="actions"):
                yield Button("Back", id="wizard-back")
                yield Button("Next", id="wizard-next", variant="primary")
                yield Button("Create declaration", id="wizard-create", classes="mutation-action")
                yield Button("Create and deploy", id="wizard-deploy", variant="success", classes="mutation-action")
            yield Static("Discovering launch context…", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        self._show_step()
        self.query_one("#copy-source-row").styles.display = "none"
        self.query_one("#existing-workspace-row").styles.display = "none"
        self.query_one("#new-auth-setup", Button).styles.display = "none"
        self._populating = True
        self.query_one("#new-workspace", Input).value = os.getcwd()
        self._populating = False
        self._load_summaries_worker()
        self._request_candidate("launch context")

    @staticmethod
    def _select_value(select: Select) -> str:
        return "" if select.value is Select.BLANK else str(select.value)

    def _build_request(self) -> dict[str, Any]:
        workspace = self.query_one("#new-workspace", Input).value.strip() or os.getcwd()
        request: dict[str, Any] = {
            "candidate_request_version": 1,
            "workspace_path": workspace,
            "field_edits": copy.deepcopy(self.field_edits),
            "access_edits": copy.deepcopy(self.access_edits),
        }
        if self._select_value(self.query_one("#new-source-mode", Select)) == "copy":
            source = self._select_value(self.query_one("#new-copy-source", Select))
            if source:
                request["copy_source_instance_id"] = source
        return request

    @staticmethod
    def _request_identity(request: Mapping[str, Any]) -> str:
        return json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _set_field_edit(self, path: str, value: Any) -> None:
        self.field_edits = [item for item in self.field_edits if item.get("path") != path]
        self.field_edits.append({"path": path, "operation": "set", "value": value})

    def _remove_field_edit(self, path: str) -> None:
        self.field_edits = [item for item in self.field_edits if item.get("path") != path]

    def _request_candidate(self, reason: str) -> None:
        request = self._build_request()
        identity = self._request_identity(request)
        generation = self._candidate_gate.next("new-instance")
        self._active_request_identity = identity
        self.preview_payload = None
        self.query_one("#wizard-create", Button).disabled = True
        self.query_one("#wizard-deploy", Button).disabled = True
        self.set_status(f"Refreshing manager candidate: {reason}…")
        self._candidate_worker(request, identity, generation)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _candidate_worker(self, request: Mapping[str, Any], identity: str, generation: int) -> None:
        worker = get_current_worker()
        try:
            payload = self.client.candidate(request)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._candidate_failed, identity, generation, str(exc))
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_candidate, payload, identity, generation)

    def _candidate_failed(self, identity: str, generation: int, message: str) -> None:
        if not self._candidate_gate.accepts("new-instance", generation) or identity != self._active_request_identity:
            return
        self.set_status(f"✗ Manager candidate rejected: {message}")

    def _apply_candidate(self, payload: Mapping[str, Any], identity: str, generation: int) -> None:
        if not self._candidate_gate.accepts("new-instance", generation) or identity != self._active_request_identity:
            return
        discovery = payload.get("discovery") if isinstance(payload.get("discovery"), Mapping) else {}
        new_identity = str(discovery.get("workspace_identity") or "") or None
        if self.workspace_identity is not None and new_identity is not None and new_identity != self.workspace_identity:
            self.field_edits = [
                item for item in self.field_edits if str(item.get("path")) in self.REUSABLE_EDIT_PATHS
            ]
            self.access_edits = []
            self.workspace_identity = new_identity
            resolved = discovery.get("workspace_path")
            if isinstance(resolved, str) and resolved:
                self._populating = True
                self.query_one("#new-workspace", Input).value = resolved
                self._populating = False
            self._request_candidate("canonical workspace changed; target-bound edits invalidated")
            return
        self.workspace_identity = new_identity or self.workspace_identity
        self.candidate_payload = payload
        self.existing_instance_id = str(payload.get("existing_instance_id")) if payload.get("existing_instance_id") else None
        self._populate_from_candidate(payload)
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        warning_suffix = f" — {warnings[0]}" if warnings else ""
        self.set_status(f"✓ Manager-authoritative candidate refreshed{warning_suffix}")

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _load_summaries_worker(self) -> None:
        worker = get_current_worker()
        try:
            payload = self.client.summaries()
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"! Existing-instance list unavailable: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_summaries, payload)

    def _apply_summaries(self, payload: Mapping[str, Any]) -> None:
        values = payload.get("instances") if isinstance(payload.get("instances"), list) else []
        options: list[tuple[str, str]] = []
        for item in values:
            if not isinstance(item, Mapping) or not item.get("instance_id"):
                continue
            instance_id = str(item["instance_id"])
            workspace = str(item.get("workspace_path") or "?")
            options.append((f"{instance_id}  {workspace}", instance_id))
        select = self.query_one("#new-copy-source", Select)
        select.set_options(options)
        if not options:
            select.disabled = True

    def _known_ports_text(self, projection: Mapping[str, Any]) -> str:
        endpoints = projection.get("endpoints") if isinstance(projection.get("endpoints"), list) else []
        state = projection.get("listener_observation") if isinstance(projection.get("listener_observation"), Mapping) else {}
        lines = [f"Listener evidence: {state.get('state', 'unknown')}"]
        for item in endpoints[:12]:
            if not isinstance(item, Mapping):
                continue
            owner = item.get("instance_id") or "—"
            item_state = item.get("state") or item.get("source") or "?"
            lines.append(
                f"{item.get('port', '?'):>5}  {item.get('purpose', 'Unknown'):<14}  {owner:<16}  {item_state}"
            )
        if len(endpoints) > 12:
            lines.append(f"… {len(endpoints) - 12} more")
        return "\n".join(lines)

    @staticmethod
    def _provenance_for(payload: Mapping[str, Any], path: str) -> Mapping[str, Any] | None:
        values = payload.get("field_provenance")
        if not isinstance(values, list):
            return None
        for item in values:
            if isinstance(item, Mapping) and item.get("path") == path:
                return item
        return None

    def _populate_from_candidate(self, payload: Mapping[str, Any]) -> None:
        desired = payload.get("effective_declaration") if isinstance(payload.get("effective_declaration"), Mapping) else {}
        mcp = desired.get("mcp") if isinstance(desired.get("mcp"), Mapping) else {}
        tunnel = desired.get("tunnel") if isinstance(desired.get("tunnel"), Mapping) else {}
        self._populating = True
        self.query_one("#new-workspace", Input).value = str(desired.get("workspace_path") or "")
        self.query_one("#new-instance-id", Input).value = str(desired.get("instance_id") or "")
        if mcp.get("permission_mode"):
            self.query_one("#new-permission", Select).value = str(mcp["permission_mode"])
        self.query_one("#new-mcp-port", Input).value = "" if mcp.get("port") is None else str(mcp.get("port"))
        self.query_one("#new-tunnel-id", Input).value = str(tunnel.get("id") or "")
        self.query_one("#new-health-port", Input).value = "" if tunnel.get("health_port") is None else str(tunnel.get("health_port"))
        self._populating = False

        port_projection = payload.get("port_projection") if isinstance(payload.get("port_projection"), Mapping) else {}
        known = self._known_ports_text(port_projection)
        self.query_one("#new-runtime-known-ports", Static).update(known)
        self.query_one("#new-tunnel-known-ports", Static).update(known)
        collision_state = str(port_projection.get("collision_state") or "unknown")
        mcp_provenance = self._provenance_for(payload, "/mcp/port") or {}
        health_provenance = self._provenance_for(payload, "/tunnel/health_port") or {}
        self.query_one("#new-mcp-port-state", Static).update(
            f"{mcp_provenance.get('source', 'manager')} / {mcp_provenance.get('status', 'effective')} — collision: {collision_state}"
        )
        self.query_one("#new-health-port-state", Static).update(
            f"{health_provenance.get('source', 'manager')} / {health_provenance.get('status', 'effective')} — collision: {collision_state}"
        )

        discovery = payload.get("discovery") if isinstance(payload.get("discovery"), Mapping) else {}
        local_git = discovery.get("local_git") if isinstance(discovery.get("local_git"), Mapping) else {}
        identity = local_git.get("identity") if isinstance(local_git.get("identity"), Mapping) else {}
        local_identity = identity.get("local") if isinstance(identity.get("local"), Mapping) else {}
        effective_identity = identity.get("effective") if isinstance(identity.get("effective"), Mapping) else {}
        remote = local_git.get("remote") if isinstance(local_git.get("remote"), Mapping) else {}
        desired_git = desired.get("git") if isinstance(desired.get("git"), Mapping) else {}
        desired_identity = desired_git.get("identity") if isinstance(desired_git.get("identity"), Mapping) else None
        desired_remote = desired_git.get("remote") if isinstance(desired_git.get("remote"), Mapping) else None
        git_lines = [
            f"Repository: {'detected' if discovery.get('repository_detected') else 'not detected'}",
            f"Root: {discovery.get('repository_root') or '—'}",
        ]
        if local_identity.get("name") or effective_identity.get("name"):
            display_identity = local_identity if local_identity.get("name") else effective_identity
            git_lines.append(
                f"Identity: {display_identity.get('name', '—')} <{display_identity.get('email', '—')}>"
            )
            git_lines.append(
                f"Identity source: {effective_identity.get('scope') or 'unknown'} / {identity.get('classification', 'unknown')}"
            )
        else:
            git_lines.append("Identity: absent")
        if desired_identity:
            git_lines.append("Identity adoption: manager candidate")
        if remote.get("selected"):
            git_lines.append(
                f"Remote: {remote.get('selected')} → {remote.get('host') or '—'}/{remote.get('repository') or '—'}"
            )
            git_lines.append(
                f"Transport: {remote.get('transport') or '—'} / {remote.get('classification') or 'unknown'}"
            )
        else:
            git_lines.append("Remote: none")
        if desired_remote:
            git_lines.append(f"Remote adoption: {desired_remote.get('protocol')} via manager candidate")
        self.query_one("#new-git-summary", Static).update("\n".join(git_lines))
        desired_github = desired.get("github") if isinstance(desired.get("github"), Mapping) else {}
        desired_mode = str(desired_github.get("mode") or "disabled")
        if desired_mode in {"managed", "disabled"}:
            self._populating = True
            self.query_one("#new-github-mode", Select).value = desired_mode
            self._populating = False
        if desired_mode == "managed":
            github_lines = [
                "Managed per-instance profile",
                f"Profile: {desired_github.get('config_dir') or 'manager-derived after instance ID resolution'}",
                f"GitHub CLI: {desired_github.get('binary') or 'unavailable on effective PATH'}",
                "Status: setup required until registered and verified",
            ]
        else:
            github_lines = ["GitHub CLI API access is not enabled for this instance."]
        self.query_one("#new-github-summary", Static).update("\n".join(github_lines))
        self.query_one("#new-git-transport", Static).update(
            "\n".join(
                [
                    f"Repository transport: {remote.get('transport') or (desired_remote.get('protocol') if desired_remote else 'not configured')}",
                    f"SSH agent: {local_git.get('ssh_agent', {}).get('status', 'unknown') if isinstance(local_git.get('ssh_agent'), Mapping) else 'unknown'}",
                    "GitHub CLI authentication is independent of repository transport.",
                ]
            )
        )

        access = desired.get("access") if isinstance(desired.get("access"), Mapping) else {}
        access_lines: list[str] = []
        access_options: list[tuple[str, str]] = []
        for group, mode in (("read_only", "ro"), ("read_write", "rw")):
            values = access.get(group) if isinstance(access.get(group), list) else []
            for item in values:
                if not isinstance(item, Mapping):
                    continue
                alias = str(item.get("alias") or "")
                path = str(item.get("path") or "")
                access_lines.append(f"{mode.upper()}  {alias}  {path}")
                access_options.append((f"{mode.upper()} {alias} — {path}", f"{mode}|{alias}"))
        self.query_one("#new-access-summary", Static).update(
            "\n".join(access_lines) if access_lines else "No additional folders configured"
        )
        access_select = self.query_one("#new-access-selection", Select)
        access_select.set_options(access_options)
        access_select.disabled = not access_options

        auth = discovery.get("authentication_status") if isinstance(discovery.get("authentication_status"), Mapping) else {}
        providers = auth.get("providers") if isinstance(auth.get("providers"), list) else []
        auth_lines: list[str] = []
        self.setup_action_url = None
        for item in providers:
            if not isinstance(item, Mapping):
                continue
            auth_lines.append(f"{item.get('provider', '?')}: {item.get('status', 'unknown')} — {item.get('reason', '')}")
            action = item.get("setup_action") if isinstance(item.get("setup_action"), Mapping) else None
            if self.setup_action_url is None and action and isinstance(action.get("url"), str):
                self.setup_action_url = str(action["url"])
        self.query_one("#new-auth-status", Static).update("\n".join(auth_lines) or "Authentication status unavailable")
        self.query_one("#new-auth-setup", Button).styles.display = "block" if self.setup_action_url else "none"

        existing_row = self.query_one("#existing-workspace-row")
        existing_row.styles.display = "block" if self.existing_instance_id else "none"
        if self.existing_instance_id:
            self.query_one("#new-existing-workspace", Static).update(
                f"Already managed by {self.existing_instance_id}. Opening the existing instance is the primary action."
            )
        self._show_step()

    def _show_step(self) -> None:
        self.query_one("#wizard-progress", Static).update(
            f"Step {self.step + 1}/5 — {self.STEPS[self.step]}"
        )
        ids = ("wizard-basics", "wizard-runtime", "wizard-tunnel", "wizard-git", "wizard-review")
        for index, widget_id in enumerate(ids):
            self.query_one(f"#{widget_id}").styles.display = "block" if index == self.step else "none"
        self.query_one("#wizard-back", Button).disabled = self.step == 0
        next_button = self.query_one("#wizard-next", Button)
        next_button.styles.display = "block" if self.step < 4 else "none"
        next_button.disabled = self.step == 0 and self.existing_instance_id is not None
        self.query_one("#wizard-create", Button).styles.display = "block" if self.step == 4 else "none"
        self.query_one("#wizard-deploy", Button).styles.display = "block" if self.step == 4 else "none"

    def _capture_step(self) -> None:
        if self.step == 0:
            workspace = self.query_one("#new-workspace", Input).value.strip()
            if not workspace.startswith("/"):
                raise ValueError("Workspace must be an absolute path")
            if "/instance_id" in self._dirty_paths:
                instance_id = self.query_one("#new-instance-id", Input).value.strip()
                if instance_id:
                    self._set_field_edit("/instance_id", instance_id)
                else:
                    self._remove_field_edit("/instance_id")
            self._workspace_dirty = False
            self._dirty_paths.discard("/instance_id")
            self._request_candidate("workspace/instance intent")
        elif self.step == 1:
            if "/mcp/permission_mode" in self._dirty_paths:
                self._set_field_edit(
                    "/mcp/permission_mode",
                    self._select_value(self.query_one("#new-permission", Select)),
                )
            if "/mcp/port" in self._dirty_paths:
                port_text = self.query_one("#new-mcp-port", Input).value.strip()
                self._set_field_edit("/mcp/port", int(port_text))
            self._dirty_paths -= {"/mcp/permission_mode", "/mcp/port"}
            self._request_candidate("runtime intent")
        elif self.step == 2:
            tunnel_id = self.query_one("#new-tunnel-id", Input).value.strip()
            if not tunnel_id:
                raise ValueError("Tunnel ID is required; authentication credentials remain external")
            if "/tunnel/id" in self._dirty_paths or not any(item.get("path") == "/tunnel/id" for item in self.field_edits):
                self._set_field_edit("/tunnel/id", tunnel_id)
            if "/tunnel/health_port" in self._dirty_paths:
                health_text = self.query_one("#new-health-port", Input).value.strip()
                self._set_field_edit("/tunnel/health_port", int(health_text))
            self._dirty_paths -= {"/tunnel/id", "/tunnel/health_port"}
            self._request_candidate("tunnel intent")
        elif self.step == 3:
            mode = self._select_value(self.query_one("#new-github-mode", Select)) or "disabled"
            self._set_field_edit("/github/mode", mode)
            self._request_candidate("GitHub access intent")

    @on(Input.Changed)
    def input_changed(self, event: Input.Changed) -> None:
        if self._populating:
            return
        mapping = {
            "new-instance-id": "/instance_id",
            "new-mcp-port": "/mcp/port",
            "new-tunnel-id": "/tunnel/id",
            "new-health-port": "/tunnel/health_port",
        }
        if event.input.id == "new-workspace":
            self._workspace_dirty = True
        elif event.input.id in mapping:
            self._dirty_paths.add(mapping[event.input.id])

    @on(Select.Changed, "#new-permission")
    def permission_changed(self, event: Select.Changed) -> None:
        if not self._populating:
            self._dirty_paths.add("/mcp/permission_mode")

    @on(Select.Changed, "#new-source-mode")
    def source_mode_changed(self, event: Select.Changed) -> None:
        if self._populating:
            return
        mode = "" if event.value is Select.BLANK else str(event.value)
        self.query_one("#copy-source-row").styles.display = "block" if mode == "copy" else "none"
        self._request_candidate("copy mode changed")

    @on(Select.Changed, "#new-copy-source")
    def copy_source_changed(self, event: Select.Changed) -> None:
        if self._populating:
            return
        if self._select_value(self.query_one("#new-source-mode", Select)) == "copy":
            self._request_candidate("copy source changed")

    @on(Select.Changed, "#new-github-mode")
    def github_mode_changed(self, event: Select.Changed) -> None:
        if self._populating:
            return
        mode = "disabled" if event.value is Select.BLANK else str(event.value)
        timing = self.query_one("#new-github-configure-timing", Select)
        timing.disabled = mode != "managed"

    def _workspace_picked(self, path: Path | None) -> None:
        if path is None:
            return
        self._populating = True
        self.query_one("#new-workspace", Input).value = str(path)
        self._populating = False
        self._workspace_dirty = True
        self._request_candidate("workspace selected")

    def _access_picked(self, mode: str, path: Path | None) -> None:
        if path is None:
            return
        self.access_edits.append({"operation": "add", "mode": mode, "path": str(path)})
        self._request_candidate("Access folder added")

    def _preview_current(self) -> None:
        request = self._build_request()
        identity = self._request_identity(request)
        self._active_request_identity = identity
        self.set_status("Reconstructing and previewing manager-authoritative candidate…")
        self._review_worker(request, identity)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _review_worker(self, request: Mapping[str, Any], identity: str) -> None:
        worker = get_current_worker()
        try:
            candidate = self.client.candidate(request)
            unresolved = candidate.get("unresolved_required_operator_fields")
            if isinstance(unresolved, list) and unresolved:
                raise TuiError(f"required operator fields remain unresolved: {', '.join(str(item) for item in unresolved)}")
            effective = candidate.get("effective_declaration")
            if not isinstance(effective, Mapping):
                raise TuiError("manager candidate did not contain an effective declaration")
            preview = self.client.preview_declaration(effective)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._review_failed, identity, str(exc))
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_review, candidate, preview, identity)

    def _review_failed(self, identity: str, message: str) -> None:
        if identity != self._active_request_identity:
            return
        self.preview_payload = None
        self.query_one("#wizard-create", Button).disabled = True
        self.query_one("#wizard-deploy", Button).disabled = True
        self.set_status(f"✗ Preview rejected: {message}")

    def _apply_review(
        self,
        candidate: Mapping[str, Any],
        preview: Mapping[str, Any],
        identity: str,
    ) -> None:
        if identity != self._active_request_identity:
            return
        self.candidate_payload = candidate
        self.preview_payload = preview
        log = self.query_one("#wizard-review-log", RichLog)
        log.clear()
        effective = candidate.get("effective_declaration") if isinstance(candidate.get("effective_declaration"), Mapping) else {}
        tunnel = effective.get("tunnel") if isinstance(effective.get("tunnel"), Mapping) else {}
        log.write(f"Instance: {effective.get('instance_id', '?')}")
        log.write(f"Workspace: {effective.get('workspace_path', '?')}")
        log.write(f"Derived tunnel profile: {tunnel.get('profile', '?')}")
        log.write(f"Candidate input fingerprint: {candidate.get('candidate_input_fingerprint', '?')}")
        log.write(f"Candidate fingerprint: {candidate.get('candidate_fingerprint', '?')}")
        collision = preview.get("collision_result") if isinstance(preview.get("collision_result"), Mapping) else {}
        log.write(f"Collision state: {collision.get('state', 'unknown')}")
        log.write("Provenance:")
        provenance = candidate.get("field_provenance") if isinstance(candidate.get("field_provenance"), list) else []
        for item in provenance:
            if isinstance(item, Mapping):
                log.write(
                    f"• {item.get('path', '?')}: {item.get('source', '?')} / {item.get('status', '?')}"
                )
        semantic = preview.get("semantic_operations") if isinstance(preview.get("semantic_operations"), list) else []
        log.write("Initial reconciliation plan:")
        for item in semantic:
            if isinstance(item, Mapping) and item.get("operation") != "NOOP":
                log.write(f"• {item.get('description', item.get('operation'))}")
        for warning in candidate.get("warnings", []) if isinstance(candidate.get("warnings"), list) else []:
            log.write(f"[yellow]! {warning}[/yellow]")
        for warning in preview.get("warnings", []) if isinstance(preview.get("warnings"), list) else []:
            log.write(f"[yellow]! {warning}[/yellow]")
        for error in preview.get("errors", []) if isinstance(preview.get("errors"), list) else []:
            log.write(f"[red]✗ {error}[/red]")
        log.write(f"Plan fingerprint: {preview.get('plan_fingerprint', '?')}")
        valid = bool(preview.get("validation", {}).get("valid")) if isinstance(preview.get("validation"), Mapping) else False
        clear = collision.get("state") == "clear" if collision.get("state") is not None else bool(collision.get("clear"))
        self.query_one("#wizard-create", Button).disabled = not (valid and clear)
        self.query_one("#wizard-deploy", Button).disabled = not (valid and clear)
        self.set_status("Review manager-authoritative effective declaration, provenance, and plan")

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "wizard-back" and self.step > 0:
            self.step -= 1
            self._show_step()
            return
        if button_id == "wizard-next":
            try:
                self._capture_step()
            except (ValueError, TypeError) as exc:
                self.set_status(f"✗ {exc}")
                return
            self.step += 1
            self._show_step()
            if self.step == 4:
                self._preview_current()
            return
        if button_id == "new-workspace-browse":
            raw = self.query_one("#new-workspace", Input).value.strip() or os.getcwd()
            self.app.push_screen(DirectoryPicker(Path(raw)), self._workspace_picked)
            return
        if button_id == "new-open-existing" and self.existing_instance_id:
            self.app.push_screen(InstanceScreen(self.existing_instance_id))
            return
        if button_id in {"new-access-add-ro", "new-access-add-rw"}:
            mode = "ro" if button_id.endswith("-ro") else "rw"
            self.app.push_screen(
                DirectoryPicker(Path(self.query_one("#new-workspace", Input).value.strip() or os.getcwd())),
                partial(self._access_picked, mode),
            )
            return
        if button_id == "new-access-remove":
            selected = self._select_value(self.query_one("#new-access-selection", Select))
            if selected and "|" in selected:
                mode, alias = selected.split("|", 1)
                self.access_edits.append({"operation": "remove", "mode": mode, "target_alias": alias})
                self._request_candidate("Access folder removed")
            return
        if button_id == "new-auth-setup" and self.setup_action_url:
            webbrowser.open(self.setup_action_url)
            self.set_status("Opened manager-projected external authentication setup")
            return
        if button_id == "new-github-token":
            webbrowser.open("https://github.com/settings/tokens")
            self.set_status("Opened manager-projected GitHub token management")
            return
        if button_id in {"wizard-create", "wizard-deploy"}:
            if not self.preview_payload or not self.preview_payload.get("plan_fingerprint") or not self.candidate_payload:
                self.set_status("? Preview is not current; review again before creation")
                self._preview_current()
                return
            effective = self.candidate_payload.get("effective_declaration")
            if not isinstance(effective, Mapping):
                self.set_status("✗ Manager candidate has no effective declaration")
                return
            self.manager_app.start_create_workflow(
                effective,
                reviewed_plan_fingerprint=str(self.preview_payload["plan_fingerprint"]),
                deploy=button_id == "wizard-deploy",
                configure_github=(
                    self._select_value(self.query_one("#new-github-mode", Select)) == "managed"
                    and self._select_value(self.query_one("#new-github-configure-timing", Select)) == "now"
                ),
            )


class WorkspaceManagerApp(App[None]):
    TITLE = "Workspace MCP Manager"
    SUB_TITLE = "Overview → Instance → Task"
    COMMAND_PALETTE_BINDING = "ctrl+p"
    BINDINGS = [Binding("f6", "toggle_terminal_selection", "Terminal select")]
    CSS = """
    Screen { background: $surface; }
    .page { width: 100%; height: 100%; padding: 0 1; }
    .title { height: auto; padding: 0 1; text-style: bold; }
    .card { height: auto; border: round $panel; padding: 0 1; margin-bottom: 1; }
    .actions { height: auto; min-height: 3; margin-top: 1; }
    .actions Button { min-width: 12; margin-right: 1; }
    #screen-status { dock: bottom; height: auto; min-height: 1; color: $text-muted; padding: 0 1; }
    DataTable { height: 1fr; min-height: 5; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    RichLog { height: 1fr; border: round $panel; }
    TextArea { height: 1fr; border: round $panel; }
    .section-title { height: auto; margin-top: 1; color: $accent; }
    .form-field { height: auto; margin-bottom: 1; }
    .form-field Label { color: $text-muted; }
    Input, Select { width: 100%; }
    #settings-form, #wizard-body { height: 1fr; }
    .wizard-step { height: auto; }
    #wizard-review-log { min-height: 10; }

    """

    def __init__(self, client: ManagerClient, *, initial_load: bool = True) -> None:
        super().__init__()
        self.client = client
        self.initial_load = initial_load
        self.busy_instances: set[str] = set()
        self.uncertain_instances: set[str] = set()
        try:
            cli_version = client.cli_version()
        except (TuiError, AttributeError):
            cli_version = "unknown"
        self._version_sub_title = f"Overview → Instance → Task · TUI {__version__} · CLI {cli_version}"
        self.sub_title = self._version_sub_title
        self._terminal_selection_mode = False

    def copy_to_clipboard(self, text: str) -> None:
        """Copy through Textual and, on WSL, directly to the Windows clipboard."""

        super().copy_to_clipboard(text)
        _copy_to_host_clipboard(text)

    def action_toggle_terminal_selection(self) -> None:
        """Release/restore terminal mouse reporting for native terminal selection."""

        driver = self._driver
        if driver is None:
            return
        if self._terminal_selection_mode:
            enable = getattr(driver, "_enable_mouse_support", None)
            if callable(enable):
                enable()
            self._terminal_selection_mode = False
            self.sub_title = self._version_sub_title
            self.notify("TUI mouse interaction restored", title="Terminal selection")
            return

        disable = getattr(driver, "_disable_mouse_support", None)
        if not callable(disable):
            self.notify(
                "This terminal driver cannot release mouse reporting; use Shift+drag for terminal selection",
                title="Terminal selection",
                severity="warning",
            )
            return
        self.capture_mouse(None)
        disable()
        self._terminal_selection_mode = True
        self.sub_title = self._version_sub_title + " · SELECT MODE"
        self.notify(
            "Mouse released to terminal. Select normally and copy with the terminal shortcut; press F6 to restore TUI mouse input.",
            title="Terminal selection",
        )

    def action_quit(self) -> None:
        self.exit()

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("Open dashboard", "Overview of all managed instances", self.open_dashboard)
        yield SystemCommand("New instance", "Open the guided instance wizard", self.open_new_instance)
        yield SystemCommand("Host overview", "Open manager-authoritative host state", self.open_host)
        yield SystemCommand("Refresh current view", "Refresh public manager projections", self.refresh_current)
        instance_id = getattr(screen, "instance_id", None)
        if isinstance(instance_id, str):
            yield SystemCommand(
                "Review changes",
                f"Review reconciliation for {instance_id}",
                lambda iid=instance_id: self.push_screen(ReviewChangesScreen(iid)),
            )
            yield SystemCommand(
                "Open logs",
                f"Open manager logs for {instance_id}",
                lambda iid=instance_id: self.push_screen(LogsScreen(iid)),
            )
            yield SystemCommand(
                "Restart instance",
                f"Restart {instance_id} through the manager",
                lambda iid=instance_id: self.start_mutation(
                    "Restart", iid, lambda: self.client.lifecycle("restart", iid)
                ),
                discover=False,
            )

    def open_dashboard(self) -> None:
        self.switch_screen(DashboardScreen())

    def open_new_instance(self) -> None:
        self.push_screen(NewInstanceScreen())

    def open_host(self) -> None:
        self.push_screen(HostScreen())

    def refresh_current(self) -> None:
        refresh = getattr(self.screen, "refresh_authoritative", None)
        if callable(refresh):
            refresh()

    def _screen_status(self, text: str) -> None:
        screen = self.screen
        if isinstance(screen, BaseScreen):
            screen.set_status(text)

    def _screen_busy(self, instance_id: str, busy: bool, *, uncertain: bool = False) -> None:
        screen = self.screen
        if getattr(screen, "instance_id", None) == instance_id and isinstance(screen, BaseScreen):
            screen.set_mutation_busy(busy, uncertain=uncertain)

    def mark_authority_verified(self, instance_id: str) -> None:
        if instance_id in self.uncertain_instances and self.client.wait_for_uncertain_mutation(instance_id, timeout=0):
            self.uncertain_instances.discard(instance_id)
            self.busy_instances.discard(instance_id)
            self._screen_busy(instance_id, False)

    def start_mutation(self, label: str, instance_id: str, operation: Callable[[], Mapping[str, Any]]) -> None:
        if instance_id in self.busy_instances or instance_id in self.uncertain_instances:
            self._screen_status("! Another mutation or outcome-resolution is active for this instance")
            return
        self.busy_instances.add(instance_id)
        self._screen_busy(instance_id, True)
        self._screen_status(f"{label}…")
        self._mutation_worker(label, instance_id, operation)

    @work(thread=True, exit_on_error=False)
    def _mutation_worker(self, label: str, instance_id: str, operation: Callable[[], Mapping[str, Any]]) -> None:
        try:
            operation()
        except TuiOutcomeUnknown as exc:
            self.call_from_thread(self._mark_unknown, label, instance_id, exc)
            self.client.wait_for_uncertain_mutation(instance_id)
            try:
                self.client.summary(instance_id)
                self.client.plan(instance_id)
            except TuiError as refresh_exc:
                self.call_from_thread(self._resolution_failed, label, instance_id, refresh_exc)
                return
            self.call_from_thread(self._mutation_resolved, label, instance_id)
            return
        except TuiError as exc:
            self.call_from_thread(self._mutation_error, label, instance_id, exc)
            return
        self.call_from_thread(self._mutation_success, label, instance_id)

    def _mark_unknown(self, label: str, instance_id: str, exc: TuiOutcomeUnknown) -> None:
        self.uncertain_instances.add(instance_id)
        self._screen_busy(instance_id, True, uncertain=True)
        self._screen_status(f"! {label}: {exc}")

    def _resolution_failed(self, label: str, instance_id: str, exc: TuiError) -> None:
        self._screen_status(f"? {label} outcome remains unknown; authoritative refresh failed: {exc}")
        self._screen_busy(instance_id, True, uncertain=True)

    def _mutation_resolved(self, label: str, instance_id: str) -> None:
        self.uncertain_instances.discard(instance_id)
        self.busy_instances.discard(instance_id)
        self._screen_busy(instance_id, False)
        self._screen_status(f"✓ {label} outcome resolved from authoritative manager state")
        self.refresh_current()

    def _mutation_error(self, label: str, instance_id: str, exc: TuiError) -> None:
        self.busy_instances.discard(instance_id)
        self._screen_busy(instance_id, False)
        screen = self.screen
        if getattr(screen, "instance_id", None) == instance_id and isinstance(screen, BaseScreen):
            screen.handle_mutation_error(exc, label)
        else:
            self._screen_status(f"✗ {label}: {exc}")

    def _mutation_success(self, label: str, instance_id: str) -> None:
        self.busy_instances.discard(instance_id)
        self._screen_busy(instance_id, False)
        self._screen_status(f"✓ {label} completed; refreshing authoritative state")
        if isinstance(self.screen, SettingsScreen) and label == "Save settings":
            self.pop_screen()
        self.refresh_current()

    def start_create_workflow(
        self,
        candidate: Mapping[str, Any],
        *,
        reviewed_plan_fingerprint: str,
        deploy: bool,
        configure_github: bool = False,
    ) -> None:
        instance_id = str(candidate.get("instance_id", ""))
        if not instance_id or instance_id in self.busy_instances:
            return
        self.busy_instances.add(instance_id)
        self._screen_busy(instance_id, True)
        self._screen_status("Creating declaration…")
        self._create_worker(
            copy.deepcopy(dict(candidate)),
            reviewed_plan_fingerprint,
            deploy,
            configure_github,
        )

    @work(thread=True, exit_on_error=False)
    def _create_worker(
        self,
        candidate: Mapping[str, Any],
        reviewed_plan_fingerprint: str,
        deploy: bool,
        configure_github: bool,
    ) -> None:
        instance_id = str(candidate["instance_id"])
        try:
            self.client.save_declaration("create", candidate)
        except TuiError as exc:
            self.call_from_thread(self._mutation_error, "Create declaration", instance_id, exc)
            return
        if configure_github:
            self.call_from_thread(
                self._configure_created_github,
                instance_id,
                reviewed_plan_fingerprint,
                deploy,
            )
            return
        self._finish_created_deployment(instance_id, reviewed_plan_fingerprint, deploy)

    def _configure_created_github(
        self,
        instance_id: str,
        reviewed_plan_fingerprint: str,
        deploy: bool,
    ) -> None:
        self._screen_status("Declaration registered; entering foreground GitHub access setup…")
        try:
            with self.suspend():
                returncode = self.client.github_access_configure_foreground(instance_id)
        except Exception as exc:
            # Credential bytes never enter this exception path; the foreground
            # client exposes only a sanitized process-level failure.
            self._screen_status(f"! GitHub access setup did not complete: {exc}")
            returncode = 1
        if returncode == 0:
            self._screen_status("GitHub access setup completed; refreshing authoritative state…")
        else:
            self._screen_status("GitHub access setup returned without success; final state will be re-observed")
        self._post_github_create_worker(instance_id, reviewed_plan_fingerprint, deploy)

    @work(thread=True, exit_on_error=False)
    def _post_github_create_worker(
        self,
        instance_id: str,
        reviewed_plan_fingerprint: str,
        deploy: bool,
    ) -> None:
        self._finish_created_deployment(instance_id, reviewed_plan_fingerprint, deploy)

    def _finish_created_deployment(self, instance_id: str, reviewed_plan_fingerprint: str, deploy: bool) -> None:
        if not deploy:
            self.call_from_thread(self._create_finished, instance_id, "Declaration created; deployment remains pending")
            return
        try:
            current_plan = self.client.plan(instance_id)
        except TuiError as exc:
            self.call_from_thread(
                self._create_finished,
                instance_id,
                f"Declaration created; authoritative re-plan failed: {exc}",
            )
            return
        current_fingerprint = current_plan.get("plan_fingerprint")
        if current_fingerprint != reviewed_plan_fingerprint:
            self.call_from_thread(
                self._create_finished,
                instance_id,
                "Declaration created, but the authoritative plan changed; deployment was not applied and requires review",
            )
            return
        try:
            self.client.apply(instance_id, expected_plan_fingerprint=reviewed_plan_fingerprint)
        except TuiError as exc:
            self.call_from_thread(
                self._create_finished,
                instance_id,
                f"Declaration created; deployment did not complete: {exc}",
            )
            return
        self.call_from_thread(self._create_finished, instance_id, "Declaration created and reviewed plan applied")

    def configure_github_foreground(self, instance_id: str) -> None:
        if instance_id in self.busy_instances or instance_id in self.uncertain_instances:
            self._screen_status("! Another mutation or outcome-resolution is active for this instance")
            return
        self.busy_instances.add(instance_id)
        self._screen_busy(instance_id, True)
        self._screen_status("Suspending TUI for visible GitHub credential entry…")
        try:
            with self.suspend():
                returncode = self.client.github_access_configure_foreground(instance_id)
        except Exception as exc:
            self._screen_status(f"✗ GitHub access setup failed to launch: {exc}")
        else:
            self._screen_status(
                "✓ GitHub access setup finished; refreshing authoritative status"
                if returncode == 0
                else "! GitHub access setup finished without success; refreshing observed status"
            )
        finally:
            self.busy_instances.discard(instance_id)
            self._screen_busy(instance_id, False)
            self.refresh_current()

    def _create_finished(self, instance_id: str, message: str) -> None:
        self.busy_instances.discard(instance_id)
        self._screen_status(f"✓ {message}")
        self.push_screen(InstanceScreen(instance_id))


def build_app(client: ManagerClient, *, initial_load: bool = True) -> WorkspaceManagerApp:
    return WorkspaceManagerApp(client, initial_load=initial_load)


async def _smoke(client: ManagerClient) -> None:
    app = build_app(client, initial_load=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()


def run_textual(client: ManagerClient, *, smoke: bool = False) -> int:
    if smoke:
        asyncio.run(_smoke(client))
        return 0
    build_app(client).run()
    return 0
