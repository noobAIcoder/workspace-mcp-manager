from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .domain import InstanceId
from .errors import ErrorCode, ManagerError
from .paths import ManagerPaths
from .redaction import redact_text, sanitized_subprocess_env
from .registry import InstanceRegistry


LEGACY_MARKER_PREFIX = "# workspace-mcp-instance="
MANAGER_MARKER_PREFIX = "# workspace-mcp-manager-instance="
KNOWN_STATE_FILES = frozenset({"mcp.log", "tunnel.log", "tunnel-supervisor.log"})
CONFIG_ASSIGNMENT_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _run(argv: list[str], *, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
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
        raise ManagerError(
            ErrorCode.IO_ERROR,
            f"cleanup host command failed: {argv[0]}",
            {"error": redact_text(str(exc))},
        ) from exc


def _read_regular_text(path: Path) -> tuple[str | None, str, str]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None, "absent", "path does not exist"
    except OSError as exc:
        return None, "unsafe", f"cannot inspect path: {redact_text(str(exc))}"
    if stat.S_ISLNK(metadata.st_mode):
        return None, "unsafe", "path is a symlink"
    if not stat.S_ISREG(metadata.st_mode):
        return None, "unsafe", "expected regular file"
    try:
        return path.read_text(encoding="utf-8", errors="strict"), "present", "regular UTF-8 file"
    except UnicodeError:
        return None, "unsafe", "file is not valid UTF-8"
    except OSError as exc:
        return None, "unsafe", f"cannot read file: {redact_text(str(exc))}"


class LegacyCleanupService:
    def __init__(self, paths: ManagerPaths, registry: InstanceRegistry) -> None:
        self.paths = paths
        self.registry = registry
        self.legacy_config_dir = paths.account_home / ".config" / "workspace-mcp"
        self.legacy_profile_dir = paths.account_home / ".config" / "tunnel-client"
        self.legacy_launcher_dir = paths.account_home / ".local" / "bin"
        self.legacy_state_root = paths.account_home / ".local" / "state" / "workspace-mcp"

    def _config_path(self, iid: str) -> Path:
        return self.legacy_config_dir / f"{iid}.conf"

    def _profile_path(self, iid: str) -> Path:
        return self.legacy_profile_dir / f"workspace-mcp-{iid}.yaml"

    def _mcp_unit_path(self, iid: str) -> Path:
        return self.paths.user_unit_dir / f"coding-tools-mcp-{iid}.service"

    def _tunnel_unit_path(self, iid: str) -> Path:
        return self.paths.user_unit_dir / f"tunnel-client-{iid}.service"

    def _launcher_path(self, iid: str) -> Path:
        return self.legacy_launcher_dir / f"{iid}-mcp"

    def _state_path(self, iid: str) -> Path:
        return self.legacy_state_root / iid

    @staticmethod
    def _valid_candidate(raw: str) -> str | None:
        try:
            return InstanceId(raw).value
        except ManagerError:
            return None

    def _discover_candidates(self) -> list[str]:
        candidates: set[str] = set()

        def add(raw: str) -> None:
            value = self._valid_candidate(raw)
            if value:
                candidates.add(value)

        try:
            for path in self.legacy_config_dir.glob("*.conf"):
                add(path.stem)
        except OSError:
            pass
        try:
            for path in self.legacy_profile_dir.glob("workspace-mcp-*.yaml"):
                name = path.name
                add(name[len("workspace-mcp-") : -len(".yaml")])
        except OSError:
            pass
        try:
            for path in self.legacy_launcher_dir.glob("*-mcp"):
                add(path.name[: -len("-mcp")])
        except OSError:
            pass
        try:
            for path in self.legacy_state_root.iterdir():
                add(path.name)
        except OSError:
            pass
        for prefix in ("coding-tools-mcp-", "tunnel-client-"):
            try:
                paths = self.paths.user_unit_dir.glob(f"{prefix}*.service")
                for path in paths:
                    iid = path.name[len(prefix) : -len(".service")]
                    value = self._valid_candidate(iid)
                    if not value:
                        continue
                    text, presence, _detail = _read_regular_text(path)
                    if presence == "present" and text is not None:
                        if f"{LEGACY_MARKER_PREFIX}{value}" in text.splitlines():
                            candidates.add(value)
            except OSError:
                pass
        return sorted(candidates)

    def _manager_ids(self) -> set[str]:
        return {item.instance_id.value for item in self.registry.list()}

    @staticmethod
    def _resource(path: Path, kind: str, ownership: str, detail: str) -> dict[str, Any]:
        return {
            "path": str(path),
            "kind": kind,
            "present": ownership != "absent",
            "ownership": ownership,
            "detail": detail,
        }

    def _audit_config(self, iid: str) -> dict[str, Any]:
        path = self._config_path(iid)
        text, presence, detail = _read_regular_text(path)
        if presence == "absent":
            return self._resource(path, "legacy-config", "absent", detail)
        if presence != "present" or text is None:
            return self._resource(path, "legacy-config", "unsafe", detail)
        values: list[str] = []
        malformed = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = CONFIG_ASSIGNMENT_RE.fullmatch(stripped)
            if not match:
                malformed = True
                continue
            key, value = match.groups()
            if key == "INSTANCE_ID":
                values.append(value)
        if malformed:
            return self._resource(path, "legacy-config", "foreign", "legacy config contains malformed data lines")
        if values != [iid]:
            return self._resource(path, "legacy-config", "foreign", "INSTANCE_ID ownership evidence does not match")
        return self._resource(path, "legacy-config", "legacy-owned", "INSTANCE_ID matches legacy candidate")

    def _audit_marked_file(self, iid: str, path: Path, kind: str) -> dict[str, Any]:
        text, presence, detail = _read_regular_text(path)
        if presence == "absent":
            return self._resource(path, kind, "absent", detail)
        if presence != "present" or text is None:
            return self._resource(path, kind, "unsafe", detail)
        lines = text.splitlines()
        legacy_marker = f"{LEGACY_MARKER_PREFIX}{iid}"
        manager_marker = f"{MANAGER_MARKER_PREFIX}{iid}"
        if manager_marker in lines:
            return self._resource(path, kind, "foreign", "resource is workspace-mcp-manager owned")
        if legacy_marker not in lines:
            return self._resource(path, kind, "foreign", "legacy ownership marker is absent")
        return self._resource(path, kind, "legacy-owned", "legacy ownership marker matches")

    def _audit_launcher(self, iid: str) -> dict[str, Any]:
        path = self._launcher_path(iid)
        text, presence, detail = _read_regular_text(path)
        if presence == "absent":
            return self._resource(path, "legacy-launcher", "absent", detail)
        if presence != "present" or text is None:
            return self._resource(path, "legacy-launcher", "unsafe", detail)
        expected_config = str(self._config_path(iid))
        if "WORKSPACE_MCP_CONFIG=" not in text or expected_config not in text:
            return self._resource(path, "legacy-launcher", "foreign", "launcher does not reference exact legacy config")
        if '"$@"' not in text:
            return self._resource(path, "legacy-launcher", "foreign", "launcher does not pass through argv")
        exec_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("exec ")]
        if len(exec_lines) != 1:
            return self._resource(path, "legacy-launcher", "foreign", "launcher must contain exactly one exec line")
        if "/workspace-mcp/workspace-mcp" not in exec_lines[0]:
            return self._resource(path, "legacy-launcher", "foreign", "launcher does not execute shared workspace-mcp binary")
        return self._resource(path, "legacy-launcher", "legacy-owned", "legacy wrapper contract matches")

    def _audit_state(self, iid: str) -> dict[str, Any]:
        path = self._state_path(iid)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return self._resource(path, "legacy-state", "absent", "path does not exist")
        except OSError as exc:
            return self._resource(path, "legacy-state", "unsafe", f"cannot inspect state directory: {redact_text(str(exc))}")
        if stat.S_ISLNK(metadata.st_mode):
            return self._resource(path, "legacy-state", "unsafe", "state path is a symlink")
        if not stat.S_ISDIR(metadata.st_mode):
            return self._resource(path, "legacy-state", "unsafe", "state path is not a directory")
        try:
            children = list(path.iterdir())
        except OSError as exc:
            return self._resource(path, "legacy-state", "unsafe", f"cannot enumerate state directory: {redact_text(str(exc))}")
        for child in children:
            if child.name not in KNOWN_STATE_FILES:
                return self._resource(path, "legacy-state", "unsafe", f"unknown state entry: {child.name}")
            try:
                child_meta = child.lstat()
            except OSError as exc:
                return self._resource(path, "legacy-state", "unsafe", f"cannot inspect state entry: {redact_text(str(exc))}")
            if stat.S_ISLNK(child_meta.st_mode) or not stat.S_ISREG(child_meta.st_mode):
                return self._resource(path, "legacy-state", "unsafe", f"unsafe state entry type: {child.name}")
        return self._resource(path, "legacy-state", "legacy-owned", "state directory contains only recognized legacy logs")

    def _audit_one(self, iid: str) -> dict[str, Any]:
        InstanceId(iid)
        resources = [
            self._audit_config(iid),
            self._audit_marked_file(iid, self._profile_path(iid), "legacy-tunnel-profile"),
            self._audit_marked_file(iid, self._mcp_unit_path(iid), "legacy-mcp-unit"),
            self._audit_marked_file(iid, self._tunnel_unit_path(iid), "legacy-tunnel-unit"),
            self._audit_launcher(iid),
            self._audit_state(iid),
        ]
        collision = iid in self._manager_ids()
        ownerships = {item["ownership"] for item in resources}
        present_owned = any(item["ownership"] == "legacy-owned" for item in resources)
        conflict = collision or bool(ownerships & {"foreign", "unsafe"})
        if conflict:
            state = "conflict"
        elif present_owned:
            state = "removable"
        else:
            state = "clean"
        return {
            "instance_id": iid,
            "state": state,
            "manager_registry_collision": collision,
            "resources": resources,
        }

    def audit(self, instance_id: str | None = None) -> dict[str, Any]:
        if instance_id is not None:
            candidate = self._audit_one(InstanceId(instance_id).value)
            return {
                "ok": candidate["state"] != "conflict",
                "action": "audit",
                "candidates": [candidate],
                "read_only": True,
            }
        candidates = [self._audit_one(iid) for iid in self._discover_candidates()]
        return {
            "ok": not any(item["state"] == "conflict" for item in candidates),
            "action": "audit",
            "candidates": candidates,
            "read_only": True,
        }

    @staticmethod
    def _systemd_observation(unit: str) -> dict[str, str]:
        completed = _run(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                unit,
                "-p",
                "LoadState",
                "-p",
                "ActiveState",
                "-p",
                "UnitFileState",
                "-p",
                "FragmentPath",
            ]
        )
        result: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
        return result

    def _verify_unit_fragment(self, iid: str, unit: str, expected: Path, kind: str) -> dict[str, str]:
        observation = self._systemd_observation(unit)
        fragment = observation.get("FragmentPath", "")
        if fragment and Path(fragment) != expected:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                f"legacy {kind} installed unit path is foreign",
                {"unit": unit, "fragment_path": fragment, "expected_path": str(expected)},
            )
        audit = self._audit_marked_file(iid, expected, kind)
        if audit["ownership"] not in {"legacy-owned", "absent"}:
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                f"legacy {kind} ownership changed before cleanup",
                {"path": str(expected), "detail": audit["detail"]},
            )
        return observation

    @staticmethod
    def _record(operations: list[dict[str, Any]], operation: str, target: str) -> None:
        operations.append({"operation": operation, "target": target})

    def apply(self, instance_id: str) -> dict[str, Any]:
        iid = InstanceId(instance_id).value
        before = self._audit_one(iid)
        if before["state"] == "clean":
            return {
                "ok": True,
                "action": "apply",
                "instance_id": iid,
                "operations": [],
                "before": before,
                "final": before,
            }
        if before["state"] != "removable":
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "legacy cleanup target is not safely removable",
                {"instance_id": iid, "audit": before},
            )

        operations: list[dict[str, Any]] = []
        mcp_unit = f"coding-tools-mcp-{iid}.service"
        tunnel_unit = f"tunnel-client-{iid}.service"
        mcp_path = self._mcp_unit_path(iid)
        tunnel_path = self._tunnel_unit_path(iid)
        mcp_obs = self._verify_unit_fragment(iid, mcp_unit, mcp_path, "legacy-mcp-unit")
        tunnel_obs = self._verify_unit_fragment(iid, tunnel_unit, tunnel_path, "legacy-tunnel-unit")

        if tunnel_obs.get("LoadState") != "not-found" and tunnel_obs.get("ActiveState") not in {"", "inactive", "failed"}:
            completed = _run(["/usr/bin/systemctl", "--user", "stop", tunnel_unit], timeout=30.0)
            if completed.returncode != 0:
                raise ManagerError(ErrorCode.IO_ERROR, "failed to stop legacy tunnel unit", {"unit": tunnel_unit})
            self._record(operations, "STOP", tunnel_unit)
        if mcp_obs.get("LoadState") != "not-found" and mcp_obs.get("ActiveState") not in {"", "inactive", "failed"}:
            completed = _run(["/usr/bin/systemctl", "--user", "stop", mcp_unit], timeout=30.0)
            if completed.returncode != 0:
                raise ManagerError(ErrorCode.IO_ERROR, "failed to stop legacy MCP unit", {"unit": mcp_unit})
            self._record(operations, "STOP", mcp_unit)

        for unit, observation in ((tunnel_unit, tunnel_obs), (mcp_unit, mcp_obs)):
            if observation.get("UnitFileState") in {"enabled", "enabled-runtime", "linked", "linked-runtime"}:
                completed = _run(["/usr/bin/systemctl", "--user", "disable", unit], timeout=30.0)
                if completed.returncode != 0:
                    raise ManagerError(ErrorCode.IO_ERROR, "failed to disable legacy unit", {"unit": unit})
                self._record(operations, "DISABLE", unit)

        removed_unit = False
        for path, kind in ((tunnel_path, "legacy-tunnel-unit"), (mcp_path, "legacy-mcp-unit")):
            audit = self._audit_marked_file(iid, path, kind)
            if audit["ownership"] == "legacy-owned":
                path.unlink()
                self._record(operations, "REMOVE", str(path))
                removed_unit = True
            elif audit["ownership"] != "absent":
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"{kind} ownership changed before removal")
        if removed_unit:
            completed = _run(["/usr/bin/systemctl", "--user", "daemon-reload"], timeout=30.0)
            if completed.returncode != 0:
                raise ManagerError(ErrorCode.IO_ERROR, "systemd user daemon-reload failed during legacy cleanup")
            self._record(operations, "DAEMON_RELOAD", "systemd --user")

        simple_resources = (
            (self._profile_path(iid), "legacy-tunnel-profile", self._audit_marked_file),
            (self._launcher_path(iid), "legacy-launcher", None),
            (self._config_path(iid), "legacy-config", None),
        )
        for path, kind, marked_auditor in simple_resources:
            if marked_auditor is not None:
                audit = marked_auditor(iid, path, kind)
            elif kind == "legacy-launcher":
                audit = self._audit_launcher(iid)
            else:
                audit = self._audit_config(iid)
            if audit["ownership"] == "legacy-owned":
                path.unlink()
                self._record(operations, "REMOVE", str(path))
            elif audit["ownership"] != "absent":
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, f"{kind} ownership changed before removal")

        state_audit = self._audit_state(iid)
        state_path = self._state_path(iid)
        if state_audit["ownership"] == "legacy-owned":
            for child in sorted(state_path.iterdir(), key=lambda item: item.name):
                child.unlink()
                self._record(operations, "REMOVE", str(child))
            state_path.rmdir()
            self._record(operations, "REMOVE", str(state_path))
        elif state_audit["ownership"] != "absent":
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "legacy state ownership changed before removal")

        final = self._audit_one(iid)
        if final["state"] != "clean":
            raise ManagerError(
                ErrorCode.RECONCILIATION_CONFLICT,
                "legacy cleanup did not converge to clean state",
                {"instance_id": iid, "final": final, "operations": operations},
            )
        return {
            "ok": True,
            "action": "apply",
            "instance_id": iid,
            "operations": operations,
            "before": before,
            "final": final,
        }
