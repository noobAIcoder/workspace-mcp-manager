from __future__ import annotations

import asyncio
import copy
import json
from functools import partial
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
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from .tui import (
    SETTINGS_FIELDS,
    GenerationGate,
    ManagerClient,
    SettingsField,
    TuiError,
    TuiOutcomeUnknown,
    apply_settings_values,
    get_nested,
    project_v1_to_v2,
    semantic_plan_diff,
    settings_field_applicable,
)


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
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._apply_error, generation, exc)
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_refresh, generation, summary, show, access, git)

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
    ) -> None:
        if not self.gate.accepts("instance", generation):
            return
        self.summary_payload = summary
        self.show_payload = show
        self.access_payload = access
        self.git_payload = git
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
        lines = [
            "[b]Configuration managed by manager[/b]",
            f"Repository: {projection.get('workspace', '?')}",
            f"GitHub profile mode: {projection.get('github_mode', 'disabled')}",
            f"Git identity configured: {projection.get('identity_configured', False)}",
            f"Remote configured: {projection.get('remote_configured', False)}",
            f"Remote transport: {projection.get('remote_protocol') or 'not configured'}",
            f"Agent provider: {projection.get('agent_mode', 'none')}",
            "",
            "[b]Authentication external[/b]",
            "The manager/TUI never requests tokens, private keys, passphrases, or API keys.",
        ]
        lines.extend("• " + line for line in _semantic_lines(self.git_payload, max_depth=2)[:30])
        self.query_one("#git-content", Static).update("\n".join(lines))

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

    def _candidate(self) -> dict[str, Any]:
        candidate = apply_settings_values(self.draft, self._values())
        self.draft = copy.deepcopy(candidate)
        return candidate

    def _sync_applicability(self) -> None:
        try:
            transient = apply_settings_values(self.draft, self._values())
        except ValueError:
            transient = self.draft
            # Choice controls still provide enough state for dependency display.
            for index, field in enumerate(SETTINGS_FIELDS):
                if field.path not in {("github", "mode"), ("agent", "mode")}:
                    continue
                control = self.query_one(f"#{self._control_id(index)}", Select)
                if control.value is not Select.BLANK:
                    transient = copy.deepcopy(dict(transient))
                    from .tui import set_nested

                    set_nested(transient, field.path, str(control.value))
        for index, field in enumerate(SETTINGS_FIELDS):
            wrapper = self.query_one(f"#{self._wrap_id(index)}")
            wrapper.styles.display = "block" if settings_field_applicable(field, transient) else "none"

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
                candidate = self._candidate()
            except ValueError as exc:
                self.set_status(f"✗ {exc}")
                return
            self.set_status("Previewing candidate with manager authority…")
            self._preview_worker(candidate)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _preview_worker(self, candidate: Mapping[str, Any]) -> None:
        worker = get_current_worker()
        try:
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


class NewInstanceScreen(BaseScreen):
    STEPS = ("Basics", "Runtime", "Tunnel", "Git & Access", "Review")

    def __init__(self) -> None:
        super().__init__()
        self.step = 0
        self.candidate: dict[str, Any] = {}
        self.preview_payload: Mapping[str, Any] | None = None
        self.source_mode = "defaults"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static("[b]New Instance[/b]", classes="title")
            yield Static("", id="wizard-progress", classes="card")
            with VerticalScroll(id="wizard-body"):
                with Vertical(id="wizard-basics", classes="wizard-step"):
                    yield Label("Start from")
                    yield Select(
                        (("Defaults", "defaults"), ("Copy existing instance", "copy")),
                        value="defaults",
                        allow_blank=False,
                        id="new-source-mode",
                    )
                    yield Label("Existing instance ID (copy mode)")
                    yield Input(id="new-copy-source")
                    yield Button("Load source", id="new-load-source")
                    yield Label("Instance ID")
                    yield Input(placeholder="example", id="new-instance-id")
                    yield Label("Workspace")
                    yield Input(placeholder="/home/user/repos/example", id="new-workspace")
                with Vertical(id="wizard-runtime", classes="wizard-step"):
                    yield Label("MCP port")
                    yield Input(placeholder="7654", id="new-mcp-port")
                    yield Label("Permission mode")
                    yield Select((("safe", "safe"), ("trusted", "trusted"), ("dangerous", "dangerous")), value="trusted", allow_blank=False, id="new-permission")
                with Vertical(id="wizard-tunnel", classes="wizard-step"):
                    yield Label("Tunnel ID")
                    yield Input(id="new-tunnel-id")
                    yield Label("Tunnel profile")
                    yield Input(id="new-tunnel-profile")
                    yield Label("Tunnel health port")
                    yield Input(placeholder="7070", id="new-health-port")
                with Vertical(id="wizard-git", classes="wizard-step"):
                    yield Label("GitHub profile mode")
                    yield Select((("disabled", "disabled"), ("external", "external"), ("managed", "managed")), value="disabled", allow_blank=False, id="new-github-mode")
                    yield Label("GitHub config directory (external/managed mode)")
                    yield Input(id="new-github-config-dir")
                    yield Label("Initial read-only access alias")
                    yield Input(id="new-ro-alias")
                    yield Label("Initial read-only access path")
                    yield Input(id="new-ro-path")
                    yield Label("Initial read-write access alias")
                    yield Input(id="new-rw-alias")
                    yield Label("Initial read-write access path")
                    yield Input(id="new-rw-path")
                with Vertical(id="wizard-review", classes="wizard-step"):
                    yield RichLog(id="wizard-review-log", wrap=True, markup=True)
            with Horizontal(classes="actions"):
                yield Button("Back", id="wizard-back")
                yield Button("Next", id="wizard-next", variant="primary")
                yield Button("Create declaration", id="wizard-create", classes="mutation-action")
                yield Button("Create and deploy", id="wizard-deploy", variant="success", classes="mutation-action")
            yield Static("Loading manager defaults…", id="screen-status")
        yield Footer()

    def on_mount(self) -> None:
        self._show_step()
        self._load_template_worker()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _load_template_worker(self) -> None:
        worker = get_current_worker()
        try:
            template = self.client.template()
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"✗ Manager template unavailable: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_template, template)

    def _apply_template(self, template: Mapping[str, Any]) -> None:
        defaults = template.get("defaults")
        if not isinstance(defaults, Mapping):
            self.set_status("✗ Manager template did not provide defaults")
            return
        self.candidate = copy.deepcopy(dict(defaults))
        self._populate_from_candidate()
        self.set_status("✓ Manager-owned defaults loaded")

    def _show_step(self) -> None:
        self.query_one("#wizard-progress", Static).update(
            f"Step {self.step + 1}/5 — {self.STEPS[self.step]}"
        )
        ids = ("wizard-basics", "wizard-runtime", "wizard-tunnel", "wizard-git", "wizard-review")
        for index, widget_id in enumerate(ids):
            self.query_one(f"#{widget_id}").styles.display = "block" if index == self.step else "none"
        self.query_one("#wizard-back", Button).disabled = self.step == 0
        self.query_one("#wizard-next", Button).styles.display = "block" if self.step < 4 else "none"
        self.query_one("#wizard-create", Button).styles.display = "block" if self.step == 4 else "none"
        self.query_one("#wizard-deploy", Button).styles.display = "block" if self.step == 4 else "none"

    @staticmethod
    def _select_value(select: Select) -> str:
        return "" if select.value is Select.BLANK else str(select.value)

    def _capture_step(self) -> None:
        if not self.candidate:
            raise ValueError("manager defaults are not loaded")
        if self.step == 0:
            instance_id = self.query_one("#new-instance-id", Input).value.strip()
            workspace = self.query_one("#new-workspace", Input).value.strip()
            if not instance_id or not workspace.startswith("/"):
                raise ValueError("Instance ID and an absolute workspace path are required")
            self.candidate["instance_id"] = instance_id
            self.candidate["workspace_path"] = workspace
            self.candidate.setdefault("tunnel", {})["profile"] = self.candidate.get("tunnel", {}).get("profile") or instance_id
        elif self.step == 1:
            port = int(self.query_one("#new-mcp-port", Input).value.strip())
            self.candidate.setdefault("mcp", {})["port"] = port
            self.candidate["mcp"]["permission_mode"] = self._select_value(self.query_one("#new-permission", Select))
        elif self.step == 2:
            tunnel_id = self.query_one("#new-tunnel-id", Input).value.strip()
            profile = self.query_one("#new-tunnel-profile", Input).value.strip()
            health_port = int(self.query_one("#new-health-port", Input).value.strip())
            if not tunnel_id or not profile:
                raise ValueError("Tunnel ID and profile are required")
            self.candidate.setdefault("tunnel", {}).update({"id": tunnel_id, "profile": profile, "health_port": health_port})
        elif self.step == 3:
            mode = self._select_value(self.query_one("#new-github-mode", Select))
            self.candidate.setdefault("github", {})["mode"] = mode
            config_dir = self.query_one("#new-github-config-dir", Input).value.strip()
            if mode == "disabled":
                self.candidate["github"]["config_dir"] = None
            elif not config_dir.startswith("/"):
                raise ValueError("GitHub config directory must be an absolute path when GitHub profile mode is enabled")
            else:
                self.candidate["github"]["config_dir"] = config_dir
            access = self.candidate.setdefault("access", {"read_only": [], "read_write": []})
            ro_alias = self.query_one("#new-ro-alias", Input).value.strip()
            ro_path = self.query_one("#new-ro-path", Input).value.strip()
            rw_alias = self.query_one("#new-rw-alias", Input).value.strip()
            rw_path = self.query_one("#new-rw-path", Input).value.strip()
            access["read_only"] = [{"alias": ro_alias, "path": ro_path}] if ro_alias and ro_path else []
            access["read_write"] = [{"alias": rw_alias, "path": rw_path}] if rw_alias and rw_path else []

    def _populate_from_candidate(self) -> None:
        if not self.candidate:
            return
        self.query_one("#new-instance-id", Input).value = str(self.candidate.get("instance_id", ""))
        self.query_one("#new-workspace", Input).value = str(self.candidate.get("workspace_path", ""))
        mcp = self.candidate.get("mcp") if isinstance(self.candidate.get("mcp"), Mapping) else {}
        tunnel = self.candidate.get("tunnel") if isinstance(self.candidate.get("tunnel"), Mapping) else {}
        self.query_one("#new-mcp-port", Input).value = "" if mcp.get("port") is None else str(mcp.get("port"))
        if mcp.get("permission_mode"):
            self.query_one("#new-permission", Select).value = str(mcp.get("permission_mode"))
        self.query_one("#new-tunnel-id", Input).value = str(tunnel.get("id", ""))
        self.query_one("#new-tunnel-profile", Input).value = str(tunnel.get("profile", ""))
        self.query_one("#new-health-port", Input).value = "" if tunnel.get("health_port") is None else str(tunnel.get("health_port"))
        github = self.candidate.get("github") if isinstance(self.candidate.get("github"), Mapping) else {}
        if github.get("mode"):
            self.query_one("#new-github-mode", Select).value = str(github.get("mode"))
        self.query_one("#new-github-config-dir", Input).value = str(github.get("config_dir") or "")

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
                self._preview_candidate()
            return
        if button_id == "new-load-source":
            source = self.query_one("#new-copy-source", Input).value.strip()
            if source:
                self._copy_worker(source)
            return
        if button_id in {"wizard-create", "wizard-deploy"}:
            if not self.preview_payload or not self.preview_payload.get("plan_fingerprint"):
                self.set_status("? Preview is not current; review again before creation")
                self._preview_candidate()
                return
            self.manager_app.start_create_workflow(
                self.candidate,
                reviewed_plan_fingerprint=str(self.preview_payload["plan_fingerprint"]),
                deploy=button_id == "wizard-deploy",
            )

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _copy_worker(self, source: str) -> None:
        worker = get_current_worker()
        try:
            show = self.client.show(source)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"✗ Cannot copy {source}: {exc}")
            return
        desired = show.get("desired") if isinstance(show.get("desired"), Mapping) else None
        if desired is not None and not worker.is_cancelled:
            self.app.call_from_thread(self._apply_copy, desired)

    def _apply_copy(self, desired: Mapping[str, Any]) -> None:
        new_id = self.query_one("#new-instance-id", Input).value.strip()
        self.candidate = project_v1_to_v2(desired) if desired.get("config_version") == 1 else copy.deepcopy(dict(desired))
        self.candidate["instance_id"] = new_id
        self._populate_from_candidate()
        self.set_status("✓ Non-secret manager declaration copied; choose a new instance ID and review ports/tunnel")

    def _preview_candidate(self) -> None:
        self.set_status("Previewing validation, collisions, and initial reconciliation…")
        self._preview_worker(copy.deepcopy(self.candidate))

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _preview_worker(self, candidate: Mapping[str, Any]) -> None:
        worker = get_current_worker()
        try:
            preview = self.client.preview_declaration(candidate)
        except TuiError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self.set_status, f"✗ Preview rejected: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply_preview, preview)

    def _apply_preview(self, preview: Mapping[str, Any]) -> None:
        self.preview_payload = preview
        log = self.query_one("#wizard-review-log", RichLog)
        log.clear()
        log.write(f"Instance: {preview.get('instance_id', '?')}")
        log.write(f"Candidate fingerprint: {preview.get('candidate_fingerprint', '?')}")
        collision = preview.get("collision_result") if isinstance(preview.get("collision_result"), Mapping) else {}
        log.write(f"Collisions clear: {collision.get('clear', False)}")
        semantic = preview.get("semantic_operations") if isinstance(preview.get("semantic_operations"), list) else []
        log.write("Initial plan:")
        for item in semantic:
            if isinstance(item, Mapping) and item.get("operation") != "NOOP":
                log.write(f"• {item.get('description', item.get('operation'))}")
        for warning in preview.get("warnings", []) if isinstance(preview.get("warnings"), list) else []:
            log.write(f"[yellow]! {warning}[/yellow]")
        for error in preview.get("errors", []) if isinstance(preview.get("errors"), list) else []:
            log.write(f"[red]✗ {error}[/red]")
        log.write(f"Plan fingerprint: {preview.get('plan_fingerprint', '?')}")
        valid = bool(preview.get("validation", {}).get("valid")) if isinstance(preview.get("validation"), Mapping) else False
        clear = bool(collision.get("clear"))
        self.query_one("#wizard-create", Button).disabled = not (valid and clear)
        self.query_one("#wizard-deploy", Button).disabled = not (valid and clear)
        self.set_status("Review manager-authoritative candidate and proposed plan")


class WorkspaceManagerApp(App[None]):
    TITLE = "Workspace MCP Manager"
    SUB_TITLE = "Overview → Instance → Task"
    COMMAND_PALETTE_BINDING = "ctrl+p"
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
    ) -> None:
        instance_id = str(candidate.get("instance_id", ""))
        if not instance_id or instance_id in self.busy_instances:
            return
        self.busy_instances.add(instance_id)
        self._screen_busy(instance_id, True)
        self._screen_status("Creating declaration…")
        self._create_worker(copy.deepcopy(dict(candidate)), reviewed_plan_fingerprint, deploy)

    @work(thread=True, exit_on_error=False)
    def _create_worker(self, candidate: Mapping[str, Any], reviewed_plan_fingerprint: str, deploy: bool) -> None:
        instance_id = str(candidate["instance_id"])
        try:
            self.client.save_declaration("create", candidate)
        except TuiError as exc:
            self.call_from_thread(self._mutation_error, "Create declaration", instance_id, exc)
            return
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
