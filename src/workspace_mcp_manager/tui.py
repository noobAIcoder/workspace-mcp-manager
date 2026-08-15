from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from . import __version__

READ_CONCURRENCY = 4
OUTPUT_LIMIT_BYTES = 1024 * 1024
READ_TIMEOUT_SECONDS = 30.0
LONG_READ_TIMEOUT_SECONDS = 180.0
MUTATION_TIMEOUT_SECONDS = 180.0
SNAPSHOT_MAX_BYTES = 64 * 1024


class TuiError(RuntimeError):
    def __init__(self, message: str, *, payload: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = dict(payload) if payload is not None else None


class TuiOutcomeUnknown(TuiError):
    """A mutation started, but the frontend did not obtain its final result."""


@dataclass(frozen=True, slots=True)
class ManagerInvocation:
    argv: tuple[str, ...]
    returncode: int
    payload: Mapping[str, Any]


class GenerationGate:
    """Pure generation guard used to suppress late read results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, int] = {}

    def next(self, key: str) -> int:
        with self._lock:
            value = self._values.get(key, 0) + 1
            self._values[key] = value
            return value

    def current(self, key: str) -> int:
        with self._lock:
            return self._values.get(key, 0)

    def accepts(self, key: str, generation: int) -> bool:
        return self.current(key) == generation


class MutationCoordinator:
    """Serialize manager mutations per instance without serializing all reads."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._active: set[str] = set()

    def _lock_for(self, instance_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(instance_id, threading.Lock())

    @contextmanager
    def mutation(self, instance_id: str) -> Iterator[None]:
        lock = self._lock_for(instance_id)
        with lock:
            with self._guard:
                self._active.add(instance_id)
            try:
                yield
            finally:
                with self._guard:
                    self._active.discard(instance_id)

    def active(self, instance_id: str) -> bool:
        with self._guard:
            return instance_id in self._active

    def any_active(self) -> bool:
        with self._guard:
            return bool(self._active)


def _error_message(payload: Mapping[str, Any], fallback: str) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        message = error.get("message")
        if code and message:
            return f"{code}: {message}"
        if message:
            return str(message)
    return fallback


def _release_root() -> Path:
    # release/src/workspace_mcp_manager/tui.py -> release
    return Path(__file__).resolve().parents[2]


def default_manager_executable(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    configured = os.environ.get("WORKSPACE_MCP_MANAGER_BIN")
    if configured:
        return configured

    release = _release_root()
    release_manifest = release / "install.json"
    release_manager = release / "bin" / "workspace-mcp-manager"
    if release_manifest.is_file():
        if release_manager.is_file() and os.access(release_manager, os.X_OK):
            return str(release_manager)
        raise TuiError(
            "the TUI release does not contain its matching workspace-mcp-manager executable"
        )

    sibling = Path(sys.argv[0]).resolve().with_name("workspace-mcp-manager")
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    discovered = shutil.which("workspace-mcp-manager")
    if discovered:
        return discovered
    raise TuiError("workspace-mcp-manager executable was not found")


class ManagerClient:
    """Bounded public-CLI adapter shared by snapshots and the Textual frontend."""

    def __init__(
        self,
        manager: str | None = None,
        *,
        registry_dir: Path | None = None,
        read_concurrency: int = READ_CONCURRENCY,
        output_limit_bytes: int = OUTPUT_LIMIT_BYTES,
    ) -> None:
        self.manager = default_manager_executable(manager)
        self.registry_dir = registry_dir
        self.output_limit_bytes = output_limit_bytes
        self.read_slots = threading.BoundedSemaphore(max(1, read_concurrency))
        self.mutations = MutationCoordinator()
        self._pending_guard = threading.Lock()
        self._pending_mutations: dict[str, threading.Event] = {}

    def argv(self, *args: str) -> tuple[str, ...]:
        values = [self.manager]
        if self.registry_dir is not None:
            values.extend(["--registry-dir", str(self.registry_dir)])
        values.extend(args)
        return tuple(values)

    def cli_version(self) -> str:
        returncode, stdout, stderr = self._invoke_process(
            (self.manager, "--version"),
            stdin_text=None,
            timeout=READ_TIMEOUT_SECONDS,
            mutation=False,
        )
        if returncode != 0:
            evidence = (stderr or stdout).strip()[-500:]
            raise TuiError(f"manager version probe failed with status {returncode}: {evidence}")
        text = stdout.strip()
        if not text:
            raise TuiError("manager version probe returned no version")
        prefix = "workspace-mcp-manager "
        return text[len(prefix) :] if text.startswith(prefix) else text

    def _bounded_file_text(self, handle: Any, *, stream: str) -> str:
        handle.flush()
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size > self.output_limit_bytes:
            raise TuiError(
                f"manager {stream} exceeded the {self.output_limit_bytes}-byte frontend bound"
            )
        handle.seek(0)
        return handle.read(size).decode("utf-8", errors="replace")

    @staticmethod
    def _cleanup_detached(
        process: subprocess.Popen[bytes],
        stdout_file: Any,
        stderr_file: Any,
        completed: threading.Event,
    ) -> None:
        try:
            process.wait()
        finally:
            stdout_file.close()
            stderr_file.close()
            completed.set()

    def _invoke_process(
        self,
        argv: tuple[str, ...],
        *,
        stdin_text: str | None,
        timeout: float,
        mutation: bool,
        mutation_key: str | None = None,
    ) -> tuple[int, str, str]:
        stdout_file = tempfile.TemporaryFile(mode="w+b")
        stderr_file = tempfile.TemporaryFile(mode="w+b")
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            shell=False,
            start_new_session=mutation,
        )
        if stdin_text is not None:
            assert process.stdin is not None
            process.stdin.write(stdin_text.encode("utf-8"))
            process.stdin.close()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if mutation:
                completed = threading.Event()
                if mutation_key is not None:
                    with self._pending_guard:
                        self._pending_mutations[mutation_key] = completed
                threading.Thread(
                    target=self._cleanup_detached,
                    args=(process, stdout_file, stderr_file, completed),
                    daemon=True,
                    name="workspace-mcp-manager-tui-mutation-cleanup",
                ).start()
                raise TuiOutcomeUnknown(
                    "Outcome unknown: the manager mutation is still running; refresh authoritative state before another dependent action"
                ) from exc
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            stdout_file.close()
            stderr_file.close()
            raise TuiError(f"manager read timed out after {timeout:g}s") from exc

        try:
            stdout = self._bounded_file_text(stdout_file, stream="stdout")
            stderr = self._bounded_file_text(stderr_file, stream="stderr")
        finally:
            stdout_file.close()
            stderr_file.close()
        return returncode, stdout, stderr

    def invoke(
        self,
        *args: str,
        stdin_text: str | None = None,
        require_ok: bool = False,
        timeout: float = READ_TIMEOUT_SECONDS,
        mutation: bool = False,
        mutation_key: str | None = None,
    ) -> ManagerInvocation:
        argv = self.argv(*args)
        if mutation:
            returncode, stdout, stderr = self._invoke_process(
                argv,
                stdin_text=stdin_text,
                timeout=timeout,
                mutation=True,
                mutation_key=mutation_key,
            )
        else:
            with self.read_slots:
                returncode, stdout, stderr = self._invoke_process(
                    argv,
                    stdin_text=stdin_text,
                    timeout=timeout,
                    mutation=False,
                )

        payload_text = stdout.strip()
        if not payload_text and stderr.strip().startswith("{"):
            payload_text = stderr.strip()
        try:
            decoded = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError as exc:
            evidence = (stderr or stdout).strip()[-2000:]
            raise TuiError(f"manager returned invalid JSON: {evidence}") from exc
        if not isinstance(decoded, Mapping):
            raise TuiError("manager returned a non-object JSON result")
        payload = dict(decoded)
        if returncode != 0 or (require_ok and payload.get("ok") is False):
            raise TuiError(
                _error_message(payload, f"manager exited with status {returncode}"),
                payload=payload,
            )
        return ManagerInvocation(argv, returncode, payload)

    def wait_for_uncertain_mutation(self, instance_id: str, timeout: float | None = None) -> bool:
        with self._pending_guard:
            event = self._pending_mutations.get(instance_id)
        if event is None:
            return True
        completed = event.wait(timeout)
        if completed:
            with self._pending_guard:
                if self._pending_mutations.get(instance_id) is event:
                    self._pending_mutations.pop(instance_id, None)
        return completed

    def list_instances(self) -> list[Mapping[str, Any]]:
        payload = self.invoke("instance", "list", require_ok=True).payload
        values = payload.get("instances")
        return list(values) if isinstance(values, list) else []

    def summaries(self) -> Mapping[str, Any]:
        return self.invoke("instance", "summaries", require_ok=True).payload

    def summary(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke("instance", "summary", instance_id, require_ok=True).payload

    def show(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke("instance", "show", instance_id, require_ok=True).payload

    def plan(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke("instance", "plan", instance_id).payload

    def view(self, command: str, instance_id: str) -> Mapping[str, Any]:
        if command == "plan":
            return self.plan(instance_id)
        return self.invoke("instance", command, instance_id).payload

    def template(self) -> Mapping[str, Any]:
        return self.invoke("instance", "template", require_ok=True).payload

    def discover(self, path: str) -> Mapping[str, Any]:
        return self.invoke("instance", "discover", path, require_ok=True).payload

    def candidate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.invoke(
            "instance",
            "candidate",
            stdin_text=json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            require_ok=True,
        ).payload

    def ports(self) -> Mapping[str, Any]:
        return self.invoke("host", "ports", require_ok=True).payload

    def git(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke("instance", "git", instance_id).payload

    def github_access_status(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke(
            "instance",
            "github-access",
            "status",
            instance_id,
            require_ok=True,
        ).payload

    def github_access_verify(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke(
            "instance",
            "github-access",
            "verify",
            instance_id,
            timeout=LONG_READ_TIMEOUT_SECONDS,
        ).payload

    def github_access_configure_foreground(self, instance_id: str) -> int:
        """Run credential onboarding on the controlling terminal without capture/detach."""

        argv = self.argv("instance", "github-access", "configure", instance_id)
        try:
            completed = subprocess.run(
                argv,
                stdin=None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=False,
                timeout=150.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TuiError("GitHub access foreground configuration did not complete") from exc
        return completed.returncode

    def diagnose(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke(
            "instance",
            "diagnose",
            instance_id,
            timeout=LONG_READ_TIMEOUT_SECONDS,
        ).payload

    def logs(self, instance_id: str, *, category: str = "all", lines: int = 100) -> Mapping[str, Any]:
        return self.invoke(
            "instance",
            "logs",
            instance_id,
            "--lines",
            str(lines),
            "--category",
            category,
            require_ok=True,
            timeout=LONG_READ_TIMEOUT_SECONDS,
        ).payload

    def host_overview(self) -> Mapping[str, Any]:
        inspect = self.invoke("host", "inspect").payload
        components = self.invoke("host", "components").payload
        return {"ok": True, "inspect": inspect, "components": components}

    def host_doctor(self) -> Mapping[str, Any]:
        return self.invoke("host", "doctor", timeout=LONG_READ_TIMEOUT_SECONDS).payload

    @staticmethod
    def _candidate_file(desired: Mapping[str, Any]) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix="workspace-mcp-manager-tui-", suffix=".json")
        path = Path(raw_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", errors="strict") as handle:
                json.dump(desired, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return path

    def preview_declaration(self, desired: Mapping[str, Any]) -> Mapping[str, Any]:
        path = self._candidate_file(desired)
        try:
            return self.invoke("instance", "preview", str(path), require_ok=True).payload
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def save_declaration(
        self,
        action: str,
        desired: Mapping[str, Any],
        *,
        expected_current_fingerprint: str | None = None,
    ) -> Mapping[str, Any]:
        if action not in {"create", "update"}:
            raise ValueError(f"unsupported declaration action: {action}")
        instance_id = str(desired.get("instance_id", ""))
        if not instance_id:
            raise TuiError("candidate declaration has no instance_id")
        path = self._candidate_file(desired)
        try:
            self.invoke("instance", "validate", str(path), require_ok=True)
            args = ["instance", action, str(path)]
            if action == "update" and expected_current_fingerprint is not None:
                args.extend(["--expected-current-fingerprint", expected_current_fingerprint])
            with self.mutations.mutation(instance_id):
                return self.invoke(
                    *args,
                    require_ok=True,
                    timeout=MUTATION_TIMEOUT_SECONDS,
                    mutation=True,
                    mutation_key=instance_id,
                ).payload
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def lifecycle(self, action: str, instance_id: str) -> Mapping[str, Any]:
        if action not in {"start", "stop", "restart", "remove"}:
            raise ValueError(f"unsupported lifecycle action: {action}")
        with self.mutations.mutation(instance_id):
            return self.invoke(
                "instance",
                action,
                instance_id,
                require_ok=True,
                timeout=MUTATION_TIMEOUT_SECONDS,
                mutation=True,
                mutation_key=instance_id,
            ).payload

    def apply(self, instance_id: str, *, expected_plan_fingerprint: str) -> Mapping[str, Any]:
        with self.mutations.mutation(instance_id):
            return self.invoke(
                "instance",
                "apply",
                instance_id,
                "--expected-plan-fingerprint",
                expected_plan_fingerprint,
                require_ok=True,
                timeout=MUTATION_TIMEOUT_SECONDS,
                mutation=True,
                mutation_key=instance_id,
            ).payload

    def access_list(self, instance_id: str) -> Mapping[str, Any]:
        return self.invoke("access", "list", instance_id, require_ok=True).payload

    def access_add(
        self,
        instance_id: str,
        *,
        mode: str,
        alias: str,
        path: str,
        expected_current_fingerprint: str | None = None,
    ) -> Mapping[str, Any]:
        command = "add-ro" if mode == "ro" else "add-rw"
        args = ["access", command, instance_id, alias, path]
        if expected_current_fingerprint is not None:
            args.extend(["--expected-current-fingerprint", expected_current_fingerprint])
        with self.mutations.mutation(instance_id):
            return self.invoke(
                *args,
                require_ok=True,
                timeout=MUTATION_TIMEOUT_SECONDS,
                mutation=True,
                mutation_key=instance_id,
            ).payload

    def access_update(
        self,
        instance_id: str,
        *,
        existing_alias: str,
        mode: str,
        alias: str,
        path: str,
        expected_current_fingerprint: str,
    ) -> Mapping[str, Any]:
        with self.mutations.mutation(instance_id):
            return self.invoke(
                "access",
                "update",
                instance_id,
                existing_alias,
                mode,
                alias,
                path,
                "--expected-current-fingerprint",
                expected_current_fingerprint,
                require_ok=True,
                timeout=MUTATION_TIMEOUT_SECONDS,
                mutation=True,
                mutation_key=instance_id,
            ).payload

    def access_remove(
        self,
        instance_id: str,
        *,
        alias: str,
        expected_current_fingerprint: str | None = None,
    ) -> Mapping[str, Any]:
        args = ["access", "remove", instance_id, alias]
        if expected_current_fingerprint is not None:
            args.extend(["--expected-current-fingerprint", expected_current_fingerprint])
        with self.mutations.mutation(instance_id):
            return self.invoke(
                *args,
                require_ok=True,
                timeout=MUTATION_TIMEOUT_SECONDS,
                mutation=True,
                mutation_key=instance_id,
            ).payload


def clip_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def get_nested(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def set_nested(value: dict[str, Any], path: Sequence[str], replacement: Any) -> None:
    current: dict[str, Any] = value
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[path[-1]] = replacement


def project_v1_to_v2(desired: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(desired))
    version = result.get("config_version")
    if version == 2:
        return result
    if version != 1:
        raise ValueError(f"unsupported config_version for frontend projection: {version}")
    legacy_github = result.get("github") if isinstance(result.get("github"), Mapping) else {}
    config_dir = legacy_github.get("config_dir")
    result["config_version"] = 2
    result["github"] = {
        "mode": "external" if config_dir else "disabled",
        "config_dir": config_dir,
        "binary": None,
    }
    result["git"] = {"identity": None, "remote": None}
    result["agent"] = {"mode": "none", "ssh_auth_sock": None}
    return result


@dataclass(frozen=True, slots=True)
class SettingsField:
    group: str
    label: str
    path: tuple[str, ...]
    kind: str = "text"
    choices: tuple[str, ...] = ()
    optional: bool = False


SETTINGS_FIELDS: tuple[SettingsField, ...] = (
    SettingsField("General", "Desired runtime", ("lifecycle", "runtime"), "choice", ("running", "stopped")),
    SettingsField("Runtime", "Permission mode", ("mcp", "permission_mode"), "choice", ("safe", "trusted", "dangerous")),
    SettingsField("Runtime", "MCP port", ("mcp", "port"), "int"),
    SettingsField("Runtime", "Shell environment", ("mcp", "shell_env_inherit"), "choice", ("core", "all", "none")),
    SettingsField("Tunnel", "Tunnel ID", ("tunnel", "id")),
    SettingsField("Tunnel", "Health port", ("tunnel", "health_port"), "int"),
    SettingsField("Git & GitHub", "GitHub profile mode", ("github", "mode"), "choice", ("disabled", "external", "managed")),
    SettingsField("Git & GitHub", "GitHub config directory", ("github", "config_dir"), "text", optional=True),
    SettingsField("Git & GitHub", "GitHub binary", ("github", "binary"), "text", optional=True),
    SettingsField("Git & GitHub", "Git identity name", ("git", "identity", "name"), "text", optional=True),
    SettingsField("Git & GitHub", "Git identity email", ("git", "identity", "email"), "text", optional=True),
    SettingsField("Git & GitHub", "Git remote name", ("git", "remote", "name"), "text", optional=True),
    SettingsField("Git & GitHub", "Git remote protocol", ("git", "remote", "protocol"), "choice", ("ssh", "https-gh"), optional=True),
    SettingsField("Git & GitHub", "Agent mode", ("agent", "mode"), "choice", ("none", "external", "managed-ssh-agent")),
    SettingsField("Git & GitHub", "SSH agent socket", ("agent", "ssh_auth_sock"), "text", optional=True),
    SettingsField("Recovery", "Admission guard", ("recovery", "admission_guard_enabled"), "bool"),
    SettingsField("Recovery", "Guard interval (seconds)", ("recovery", "guard_interval_seconds"), "int"),
    SettingsField("Recovery", "Recovery cooldown (seconds)", ("recovery", "recovery_cooldown_seconds"), "int"),
)


def settings_field_applicable(field: SettingsField, draft: Mapping[str, Any]) -> bool:
    if field.path in {("github", "config_dir"), ("github", "binary")}:
        return get_nested(draft, ("github", "mode")) == "external"
    if field.path == ("agent", "ssh_auth_sock"):
        return get_nested(draft, ("agent", "mode")) == "external"
    return True


def parse_settings_value(field: SettingsField, text: Any) -> Any:
    if field.kind == "bool":
        if isinstance(text, bool):
            return text
        normalized = str(text).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{field.label} must be true or false")
    if field.kind == "int":
        try:
            value = int(str(text).strip())
        except ValueError as exc:
            raise ValueError(f"{field.label} must be an integer") from exc
        if value < 1:
            raise ValueError(f"{field.label} must be positive")
        return value
    if field.kind == "choice":
        value = str(text)
        if field.optional and not value:
            return None
        if value not in field.choices:
            raise ValueError(f"{field.label} must be one of {', '.join(field.choices)}")
        return value
    value = str(text).strip()
    if field.optional and not value:
        return None
    if not value:
        raise ValueError(f"{field.label} is required")
    return value


def apply_settings_values(draft: Mapping[str, Any], values: Mapping[tuple[str, ...], Any]) -> dict[str, Any]:
    result = project_v1_to_v2(draft) if draft.get("config_version") == 1 else copy.deepcopy(dict(draft))
    for field in SETTINGS_FIELDS:
        if not settings_field_applicable(field, result):
            continue
        if field.path in values:
            set_nested(result, field.path, parse_settings_value(field, values[field.path]))
    git = result.get("git")
    if isinstance(git, dict):
        identity = git.get("identity")
        if isinstance(identity, dict):
            name = identity.get("name")
            email = identity.get("email")
            if not name and not email:
                git["identity"] = None
            elif not name or not email:
                raise ValueError("Git identity requires both name and email")
        remote = git.get("remote")
        if isinstance(remote, dict):
            name = remote.get("name")
            protocol = remote.get("protocol")
            if not name and not protocol:
                git["remote"] = None
            elif not name or not protocol:
                raise ValueError("Git remote requires both name and protocol")
    github = result.get("github")
    if isinstance(github, dict) and github.get("mode") == "disabled":
        github["config_dir"] = None
    agent = result.get("agent")
    if isinstance(agent, dict) and agent.get("mode") != "external":
        agent["ssh_auth_sock"] = None
    return result


def semantic_plan_diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    def descriptions(payload: Mapping[str, Any]) -> list[str]:
        items = payload.get("semantic_operations")
        if not isinstance(items, list):
            return []
        return [
            str(item.get("description"))
            for item in items
            if isinstance(item, Mapping) and item.get("operation") != "NOOP" and item.get("description")
        ]

    before = descriptions(old)
    after = descriptions(new)
    lines = [f"- {item}" for item in before if item not in after]
    lines.extend(f"+ {item}" for item in after if item not in before)
    return lines or ["No semantic operation changes"]


def dashboard_snapshot(client: ManagerClient, *, instance_id: str | None = None) -> str:
    # Preserve PM2's external plain-text contract while PM3 changes interactive presentation.
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
        lines.append(
            f"mcp={mcp.get('host', '?')}:{mcp.get('port', '?')} permission={mcp.get('permission_mode', '?')}"
        )
        lines.append(f"tunnel_health={tunnel.get('health_host', '?')}:{tunnel.get('health_port', '?')}")
        plan = client.plan(instance_id)
        operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
        non_noop = [
            item
            for item in operations
            if isinstance(item, Mapping) and item.get("operation") != "NOOP"
        ]
        lines.append(f"plan_valid={plan.get('valid')} non_noop={len(non_noop)}")
    text = "\n".join(lines) + "\n"
    encoded = text.encode("utf-8")
    if len(encoded) > SNAPSHOT_MAX_BYTES:
        text = encoded[: SNAPSHOT_MAX_BYTES - 32].decode("utf-8", errors="ignore") + "\n… snapshot truncated\n"
    return text


def _run_textual(client: ManagerClient, *, smoke: bool) -> int:
    try:
        from .tui_textual import run_textual
    except ImportError as exc:
        raise TuiError(f"Textual runtime is unavailable: {exc}") from exc
    return run_textual(client, smoke=smoke)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workspace-mcp-manager-tui")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--manager", help="explicit manager executable for controlled testing")
    parser.add_argument("--registry-dir", type=Path, help="override manager registry for controlled testing")
    parser.add_argument("--snapshot", action="store_true", help="emit bounded read-only plain-text snapshot")
    parser.add_argument("--instance", help="instance to include in --snapshot")
    parser.add_argument("--smoke", action="store_true", help="headless Textual startup/render validation")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = ManagerClient(args.manager, registry_dir=args.registry_dir)
        if args.snapshot:
            sys.stdout.write(dashboard_snapshot(client, instance_id=args.instance))
            return 0
        if args.instance is not None:
            raise TuiError("--instance is only valid with --snapshot")
        return _run_textual(client, smoke=args.smoke)
    except TuiError as exc:
        print(f"workspace-mcp-manager-tui: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
