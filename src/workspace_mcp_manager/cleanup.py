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
KNOWN_MCP_DROPIN_FILES = frozenset(
    {
        "github-access.conf",
        "zz-exec-roots.conf",
        "workspace-mcp-manager-access.conf",
    }
)
CONFIG_ASSIGNMENT_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
SYSTEMD_ENV_RE = re.compile(r'^Environment="([A-Z][A-Z0-9_]*)=(.*)"$')


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

    def _config_backup_path(self, iid: str) -> Path:
        return self.legacy_config_dir / f"{iid}.conf.bak"

    def _profile_path(self, iid: str) -> Path:
        return self.legacy_profile_dir / f"workspace-mcp-{iid}.yaml"

    def _mcp_unit_path(self, iid: str) -> Path:
        return self.paths.user_unit_dir / f"coding-tools-mcp-{iid}.service"

    def _mcp_dropin_dir(self, iid: str) -> Path:
        return self.paths.user_unit_dir / f"coding-tools-mcp-{iid}.service.d"

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
            for path in self.legacy_config_dir.glob("*.conf.bak"):
                add(path.name[: -len(".conf.bak")])
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
        try:
            for path in self.paths.user_unit_dir.glob("coding-tools-mcp-*.service.d"):
                name = path.name
                add(name[len("coding-tools-mcp-") : -len(".service.d")])
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

    def _audit_config_path(self, iid: str, path: Path, kind: str) -> dict[str, Any]:
        text, presence, detail = _read_regular_text(path)
        if presence == "absent":
            return self._resource(path, kind, "absent", detail)
        if presence != "present" or text is None:
            return self._resource(path, kind, "unsafe", detail)
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
            return self._resource(path, kind, "foreign", "legacy config contains malformed data lines")
        if values != [iid]:
            return self._resource(path, kind, "foreign", "INSTANCE_ID ownership evidence does not match")
        return self._resource(path, kind, "legacy-owned", "INSTANCE_ID matches legacy candidate")

    def _audit_config(self, iid: str) -> dict[str, Any]:
        return self._audit_config_path(iid, self._config_path(iid), "legacy-config")

    def _audit_config_backup(self, iid: str) -> dict[str, Any]:
        return self._audit_config_path(iid, self._config_backup_path(iid), "legacy-config-backup")

    def _legacy_workspace_path(self, iid: str) -> Path | None:
        text, presence, _detail = _read_regular_text(self._config_path(iid))
        if presence != "present" or text is None:
            return None
        values: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = CONFIG_ASSIGNMENT_RE.fullmatch(stripped)
            if match and match.group(1) == "WORKSPACE_PATH":
                values.append(match.group(2))
        if len(values) != 1:
            return None
        path = Path(values[0])
        return path if path.is_absolute() else None

    def _audit_mcp_dropin_file(self, iid: str, path: Path) -> tuple[str, str]:
        text, presence, detail = _read_regular_text(path)
        if presence != "present" or text is None:
            return ("unsafe", detail)
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not lines or lines[0] != "[Service]" or any(line.startswith("[") for line in lines[1:]):
            return ("foreign", "drop-in is not a single [Service] fragment")

        legacy_gh_dir = self.legacy_config_dir / f"{iid}-gh"
        nvm_root = self.paths.account_home / ".nvm" / "versions" / "node"
        directives = lines[1:]

        if path.name == "github-access.conf":
            seen_gh = False
            for line in directives:
                match = SYSTEMD_ENV_RE.fullmatch(line)
                if not match:
                    return ("foreign", "GitHub drop-in contains an unrecognized directive")
                key, value = match.groups()
                if key == "GH_CONFIG_DIR":
                    if value != str(legacy_gh_dir):
                        return ("foreign", "GH_CONFIG_DIR does not match the exact legacy instance profile")
                    seen_gh = True
                elif key == "CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS":
                    roots = [Path(item) for item in value.split(os.pathsep) if item]
                    if roots != [legacy_gh_dir]:
                        return ("foreign", "GitHub drop-in exec roots do not match the exact legacy profile")
                else:
                    return ("foreign", "GitHub drop-in contains an unrecognized environment key")
            if not seen_gh:
                return ("foreign", "GitHub drop-in lacks exact legacy GH_CONFIG_DIR ownership evidence")
            return ("legacy-owned", "legacy GitHub profile drop-in contract matches")

        if path.name == "zz-exec-roots.conf":
            if len(directives) != 1:
                return ("foreign", "exec-roots drop-in must contain exactly one environment directive")
            match = SYSTEMD_ENV_RE.fullmatch(directives[0])
            if not match or match.group(1) != "CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS":
                return ("foreign", "exec-roots drop-in contract does not match")
            roots = [Path(item) for item in match.group(2).split(os.pathsep) if item]
            if legacy_gh_dir not in roots:
                return ("foreign", "exec-roots drop-in lacks the exact legacy GitHub profile root")
            for root in roots:
                if root == legacy_gh_dir:
                    continue
                try:
                    root.relative_to(nvm_root)
                except ValueError:
                    return ("foreign", "exec-roots drop-in contains a root outside legacy GitHub/NVM paths")
            return ("legacy-owned", "legacy GitHub/NVM exec-roots drop-in contract matches")

        if path.name == "workspace-mcp-manager-access.conf":
            workspace = self._legacy_workspace_path(iid)
            if workspace is None:
                return ("foreign", "access drop-in cannot be tied to one legacy WORKSPACE_PATH")
            access_root = workspace / ".workspace-mcp-access"
            if not directives:
                return ("foreign", "access drop-in contains no bind directives")
            private_users = False
            bind_count = 0
            for line in directives:
                if line == "PrivateUsers=true":
                    if private_users:
                        return ("foreign", "access drop-in repeats PrivateUsers=true")
                    private_users = True
                    continue
                if line.startswith("BindPaths="):
                    value = line[len("BindPaths=") :]
                elif line.startswith("BindReadOnlyPaths="):
                    value = line[len("BindReadOnlyPaths=") :]
                else:
                    return ("foreign", "access drop-in contains an unrecognized directive")
                bind_count += 1
                parts = value.split(":")
                if len(parts) not in {2, 3} or (len(parts) == 3 and parts[2] != "rbind"):
                    return ("foreign", "access drop-in bind syntax is not recognized")
                source, target = Path(parts[0]), Path(parts[1])
                if not source.is_absolute() or not target.is_absolute():
                    return ("foreign", "access drop-in contains a non-absolute bind path")
                if target.parent != access_root:
                    return ("foreign", "access drop-in target is outside the exact legacy workspace access root")
            if not private_users or bind_count == 0:
                return ("foreign", "access drop-in lacks the legacy PrivateUsers/bind contract")
            return ("legacy-owned", "legacy workspace access drop-in contract matches")

        return ("foreign", "drop-in filename is not in the recognized legacy set")

    def _audit_mcp_dropins(self, iid: str) -> dict[str, Any]:
        path = self._mcp_dropin_dir(iid)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return self._resource(path, "legacy-mcp-dropins", "absent", "path does not exist")
        except OSError as exc:
            return self._resource(path, "legacy-mcp-dropins", "unsafe", f"cannot inspect drop-in directory: {redact_text(str(exc))}")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return self._resource(path, "legacy-mcp-dropins", "unsafe", "drop-in path is not a real directory")
        try:
            children = sorted(path.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            return self._resource(path, "legacy-mcp-dropins", "unsafe", f"cannot enumerate drop-in directory: {redact_text(str(exc))}")
        if not children:
            return self._resource(path, "legacy-mcp-dropins", "foreign", "drop-in directory is empty and has no ownership evidence")
        for child in children:
            if child.name not in KNOWN_MCP_DROPIN_FILES:
                return self._resource(path, "legacy-mcp-dropins", "foreign", f"unrecognized drop-in file: {child.name}")
            ownership, detail = self._audit_mcp_dropin_file(iid, child)
            if ownership != "legacy-owned":
                return self._resource(path, "legacy-mcp-dropins", ownership, f"{child.name}: {detail}")
        return self._resource(path, "legacy-mcp-dropins", "legacy-owned", "all legacy MCP drop-ins match recognized instance-owned contracts")

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
            self._audit_config_backup(iid),
            self._audit_marked_file(iid, self._profile_path(iid), "legacy-tunnel-profile"),
            self._audit_marked_file(iid, self._mcp_unit_path(iid), "legacy-mcp-unit"),
            self._audit_mcp_dropins(iid),
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
        dropins = self._audit_mcp_dropins(iid)
        dropin_dir = self._mcp_dropin_dir(iid)
        if dropins["ownership"] == "legacy-owned":
            for child in sorted(dropin_dir.iterdir(), key=lambda item: item.name):
                ownership, detail = self._audit_mcp_dropin_file(iid, child)
                if ownership != "legacy-owned":
                    raise ManagerError(
                        ErrorCode.RECONCILIATION_CONFLICT,
                        "legacy MCP drop-in ownership changed before removal",
                        {"path": str(child), "detail": detail},
                    )
                child.unlink()
                self._record(operations, "REMOVE", str(child))
            dropin_dir.rmdir()
            self._record(operations, "REMOVE", str(dropin_dir))
            removed_unit = True
        elif dropins["ownership"] != "absent":
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, "legacy MCP drop-in ownership changed before removal")

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
            (self._config_backup_path(iid), "legacy-config-backup", None),
            (self._config_path(iid), "legacy-config", None),
        )
        for path, kind, marked_auditor in simple_resources:
            if marked_auditor is not None:
                audit = marked_auditor(iid, path, kind)
            elif kind == "legacy-launcher":
                audit = self._audit_launcher(iid)
            elif kind == "legacy-config-backup":
                audit = self._audit_config_backup(iid)
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
