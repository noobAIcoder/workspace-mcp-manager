from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .domain import DeploymentTarget, DesiredInstance, RuntimeTarget
from .generation import OWNER_PREFIX, GeneratedBundle, GeneratedResource, ResourceGenerator, ResourceKind
from .paths import ManagerPaths
from .registry import InstanceRegistry


class PlanOperation(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    ENABLE = "ENABLE"
    DISABLE = "DISABLE"
    START = "START"
    STOP = "STOP"
    RESTART = "RESTART"
    REMOVE = "REMOVE"
    NOOP = "NOOP"
    CONFLICT = "CONFLICT"


class ObservationStatus(StrEnum):
    ABSENT = "absent"
    EXACT = "exact"
    DIFFERENT = "different"
    FOREIGN = "foreign"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    status: ObservationStatus
    detail: str


@dataclass(frozen=True, slots=True)
class UnitObservation:
    loaded: bool
    enabled: bool
    active: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedIdentityScan:
    records: tuple[dict[str, str], ...]
    incomplete_units: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanItem:
    operation: PlanOperation
    target: str
    reason: str
    resource_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value,
            "target": self.target,
            "reason": self.reason,
            "resource_id": self.resource_id,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    instance_id: str
    desired_fingerprint: str
    valid: bool
    operations: tuple[PlanItem, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "desired_fingerprint": self.desired_fingerprint,
            "valid": self.valid,
            "warnings": list(self.warnings),
            "operations": [item.to_dict() for item in self.operations],
        }


class HostResourceObserver:
    def check_path(
        self,
        path: str,
        *,
        expected: str,
        executable: bool = False,
        writable: bool = False,
    ) -> ResourceObservation:
        candidate = Path(path)
        try:
            metadata = candidate.stat()
        except FileNotFoundError:
            return ResourceObservation(ObservationStatus.ABSENT, "path does not exist")
        except PermissionError:
            return ResourceObservation(
                ObservationStatus.UNVERIFIED,
                "current execution context cannot inspect path",
            )
        except OSError as exc:
            return ResourceObservation(ObservationStatus.UNVERIFIED, f"cannot inspect path: {exc}")
        if expected == "directory" and not stat.S_ISDIR(metadata.st_mode):
            return ResourceObservation(ObservationStatus.FOREIGN, "expected directory")
        if expected == "file" and not stat.S_ISREG(metadata.st_mode):
            return ResourceObservation(ObservationStatus.FOREIGN, "expected regular file")
        if executable and not os.access(candidate, os.X_OK):
            return ResourceObservation(ObservationStatus.DIFFERENT, "path is not executable")
        if writable and not os.access(candidate, os.W_OK | os.R_OK | os.X_OK):
            return ResourceObservation(
                ObservationStatus.DIFFERENT,
                "directory is not read/write/search accessible",
            )
        return ResourceObservation(ObservationStatus.EXACT, "path satisfies requirement")

    def observe_resource(self, resource: GeneratedResource) -> ResourceObservation:
        path = Path(resource.path)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return ResourceObservation(ObservationStatus.ABSENT, "path does not exist")
        except PermissionError:
            return ResourceObservation(
                ObservationStatus.UNVERIFIED,
                "current execution context cannot inspect path",
            )
        except OSError as exc:
            return ResourceObservation(ObservationStatus.UNVERIFIED, f"cannot inspect path: {exc}")

        if resource.ownership_marker_path:
            marker_path = Path(resource.ownership_marker_path)
            try:
                marker_text = marker_path.read_text(encoding="utf-8", errors="strict")
            except FileNotFoundError:
                return ResourceObservation(
                    ObservationStatus.FOREIGN,
                    f"ownership marker is missing: {marker_path}",
                )
            except PermissionError:
                return ResourceObservation(
                    ObservationStatus.UNVERIFIED,
                    f"current execution context cannot read ownership marker: {marker_path}",
                )
            except UnicodeError:
                return ResourceObservation(
                    ObservationStatus.FOREIGN,
                    f"ownership marker is not valid UTF-8: {marker_path}",
                )
            except OSError as exc:
                return ResourceObservation(
                    ObservationStatus.UNVERIFIED,
                    f"cannot read ownership marker {marker_path}: {exc}",
                )
            if resource.ownership_token and resource.ownership_token not in marker_text:
                return ResourceObservation(
                    ObservationStatus.FOREIGN,
                    f"ownership marker does not identify this instance: {marker_path}",
                )

        if resource.kind is ResourceKind.DIRECTORY:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                return ResourceObservation(
                    ObservationStatus.FOREIGN,
                    "expected directory but found another file type",
                )
            actual_mode = stat.S_IMODE(metadata.st_mode)
            if actual_mode != resource.mode:
                return ResourceObservation(
                    ObservationStatus.DIFFERENT,
                    f"directory mode is {oct(actual_mode)}, expected {oct(resource.mode)}",
                )
            return ResourceObservation(ObservationStatus.EXACT, "directory exists with expected mode")

        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return ResourceObservation(
                ObservationStatus.FOREIGN,
                "expected regular file but found another file type",
            )
        try:
            content = path.read_text(encoding="utf-8", errors="strict")
        except PermissionError:
            return ResourceObservation(
                ObservationStatus.UNVERIFIED,
                "current execution context cannot safely read file",
            )
        except UnicodeError:
            return ResourceObservation(ObservationStatus.FOREIGN, "managed text file is not valid UTF-8")
        except OSError as exc:
            return ResourceObservation(ObservationStatus.UNVERIFIED, f"cannot read file: {exc}")
        if (
            resource.ownership_token
            and not resource.ownership_marker_path
            and resource.ownership_token not in content
        ):
            return ResourceObservation(ObservationStatus.FOREIGN, "ownership marker is absent")
        actual_mode = stat.S_IMODE(metadata.st_mode)
        if content == (resource.content or "") and actual_mode == resource.mode:
            return ResourceObservation(ObservationStatus.EXACT, "content and mode match")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        return ResourceObservation(
            ObservationStatus.DIFFERENT,
            f"owned resource differs (content_sha256_prefix={digest}, mode={oct(actual_mode)})",
        )

    def observe_unit(self, unit_name: str) -> UnitObservation:
        try:
            completed = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit_name,
                    "-p",
                    "LoadState",
                    "-p",
                    "UnitFileState",
                    "-p",
                    "ActiveState",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return UnitObservation(False, False, False, f"systemctl unavailable: {exc}")
        properties: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties[key] = value
        if completed.returncode != 0:
            return UnitObservation(False, False, False, completed.stderr.strip() or "unit query failed")
        load_state = properties.get("LoadState", "not-found")
        unit_file_state = properties.get("UnitFileState", "")
        active_state = properties.get("ActiveState", "inactive")
        return UnitObservation(
            loaded=load_state == "loaded",
            enabled=unit_file_state in {"enabled", "enabled-runtime", "linked", "linked-runtime"},
            active=active_state in {"active", "activating", "reloading"},
        )

    def listeners(self) -> set[str]:
        try:
            completed = subprocess.run(
                ["ss", "-H", "-ltn"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return set()
        listeners: set[str] = set()
        for line in completed.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 4:
                listeners.add(fields[3])
        return listeners

    def managed_identities(self) -> list[dict[str, str]]:
        try:
            listed = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "list-unit-files",
                    "--no-legend",
                    "coding-tools-mcp-*.service",
                    "tunnel-client-*.service",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        result: list[dict[str, str]] = []
        for line in listed.stdout.splitlines():
            fields = line.split()
            if not fields:
                continue
            unit_name = fields[0]
            if not unit_name.endswith(".service"):
                continue
            record: dict[str, str] = {"unit_name": unit_name}
            try:
                cat = subprocess.run(
                    ["systemctl", "--user", "cat", unit_name],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    errors="replace",
                    timeout=3,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                cat = None
            if cat is not None and cat.returncode == 0:
                marker_map = {
                    "# workspace-mcp-port=": "mcp_port",
                    "# workspace-mcp-manager-port=": "mcp_port",
                    "# workspace-mcp-profile=": "tunnel_profile",
                    "# workspace-mcp-manager-profile=": "tunnel_profile",
                    "# workspace-mcp-health-port=": "tunnel_health_port",
                    "# workspace-mcp-manager-health-port=": "tunnel_health_port",
                    "# workspace-mcp-tunnel-id=": "tunnel_id",
                    "# workspace-mcp-manager-tunnel-id=": "tunnel_id",
                    "# workspace-mcp-instance=": "instance_id",
                    f"# {OWNER_PREFIX}": "instance_id",
                }
                for raw_line in cat.stdout.splitlines():
                    stripped = raw_line.strip()
                    for prefix, key in marker_map.items():
                        if stripped.startswith(prefix):
                            record[key] = stripped[len(prefix) :].strip()
            if unit_name.startswith("coding-tools-mcp-"):
                try:
                    shown = subprocess.run(
                        [
                            "systemctl",
                            "--user",
                            "show",
                            unit_name,
                            "-p",
                            "WorkingDirectory",
                            "-p",
                            "ExecStart",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        errors="replace",
                        timeout=3,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    shown = None
                if shown is not None and shown.returncode == 0:
                    for property_line in shown.stdout.splitlines():
                        key, separator, value = property_line.partition("=")
                        if not separator:
                            continue
                        if key == "WorkingDirectory" and value.strip():
                            record["workspace_path"] = value.strip()
                        elif key == "ExecStart":
                            host_match = re.search(r"(?:^|\s)--host\s+(\S+)(?:\s|$)", value)
                            if host_match:
                                record["mcp_host"] = host_match.group(1)
                            port_match = re.search(r"(?:^|\s)--port\s+([0-9]+)(?:\s|$)", value)
                            if port_match:
                                record["mcp_port"] = port_match.group(1)
            result.append(record)
        return result

    def managed_identity_scan(self) -> ManagedIdentityScan:
        records = tuple(self.managed_identities())
        incomplete: list[str] = []
        for record in records:
            unit_name = record.get("unit_name", "")
            if unit_name.startswith("coding-tools-mcp-"):
                required = {"workspace_path", "mcp_port"}
            elif unit_name.startswith("tunnel-client-"):
                required = {"tunnel_profile", "tunnel_health_port", "tunnel_id"}
            else:
                continue
            if not required.issubset(record):
                incomplete.append(unit_name)
        return ManagedIdentityScan(records=records, incomplete_units=tuple(sorted(incomplete)))


class ReconciliationPlanner:
    def __init__(
        self,
        paths: ManagerPaths,
        registry: InstanceRegistry,
        *,
        generator: ResourceGenerator | None = None,
        observer: HostResourceObserver | None = None,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.generator = generator or ResourceGenerator(paths)
        self.observer = observer or HostResourceObserver()

    def _registry_conflicts(self, desired: DesiredInstance) -> list[PlanItem]:
        result: list[PlanItem] = []
        for other in self.registry.list():
            if other.instance_id == desired.instance_id:
                continue
            comparisons = (
                (desired.workspace_path, other.workspace_path, "workspace path"),
                (desired.tunnel.id, other.tunnel.id, "tunnel ID"),
                (desired.tunnel.profile, other.tunnel.profile, "tunnel profile"),
                ((desired.mcp.host, desired.mcp.port), (other.mcp.host, other.mcp.port), "MCP endpoint"),
                (
                    (desired.tunnel.health_host, desired.tunnel.health_port),
                    (other.tunnel.health_host, other.tunnel.health_port),
                    "tunnel health endpoint",
                ),
            )
            for left, right, label in comparisons:
                if left == right:
                    result.append(
                        PlanItem(
                            PlanOperation.CONFLICT,
                            desired.instance_id.value,
                            f"{label} collides with declared instance {other.instance_id.value}",
                        )
                    )
        return result

    def _host_preconditions(self, desired: DesiredInstance) -> list[PlanItem]:
        checks: list[tuple[str, str, str, bool, bool]] = [
            (desired.workspace_path, "workspace", "directory", False, True),
            (desired.mcp.binary, "MCP binary", "file", True, False),
            (desired.tunnel.binary, "tunnel binary", "file", True, False),
            (desired.tunnel.env_file, "tunnel environment file", "file", False, False),
            (str(self.paths.manager_executable), "manager runtime executable", "file", True, False),
            (self.generator.curl_binary, "curl binary", "file", True, False),
        ]
        for folder in desired.access.read_only:
            checks.append((folder.path, f"RO access source {folder.alias}", "directory", False, False))
        for folder in desired.access.read_write:
            checks.append((folder.path, f"RW access source {folder.alias}", "directory", False, True))

        result: list[PlanItem] = []
        for path, label, expected, executable, writable in checks:
            observation = self.observer.check_path(
                path,
                expected=expected,
                executable=executable,
                writable=writable,
            )
            if observation.status is ObservationStatus.EXACT:
                continue
            result.append(
                PlanItem(
                    PlanOperation.CONFLICT,
                    path,
                    f"{label} precondition failed: {observation.detail}",
                    resource_id="precondition",
                )
            )
        return result

    def _managed_host_conflicts(self, desired: DesiredInstance) -> list[PlanItem]:
        result: list[PlanItem] = []
        target_units = {
            f"coding-tools-mcp-{desired.instance_id.value}.service",
            f"tunnel-client-{desired.instance_id.value}.service",
        }
        expected = {
            "workspace_path": desired.workspace_path,
            "mcp_port": str(desired.mcp.port),
            "tunnel_profile": desired.tunnel.profile,
            "tunnel_health_port": str(desired.tunnel.health_port),
            "tunnel_id": desired.tunnel.id,
        }
        labels = {
            "workspace_path": "workspace path",
            "mcp_port": "MCP port",
            "tunnel_profile": "tunnel profile",
            "tunnel_health_port": "tunnel health port",
            "tunnel_id": "tunnel ID",
        }
        scan = self.observer.managed_identity_scan()
        for unit_name in scan.incomplete_units:
            if unit_name in target_units:
                continue
            result.append(
                PlanItem(
                    PlanOperation.CONFLICT,
                    unit_name,
                    "collision-sensitive identity metadata cannot be fully verified from the current execution context",
                    resource_id="host-identity",
                )
            )
        seen: set[tuple[str, str]] = set()
        for record in scan.records:
            unit_name = record.get("unit_name", "")
            if unit_name in target_units:
                continue
            for key, expected_value in expected.items():
                if record.get(key) != expected_value:
                    continue
                dedupe = (unit_name, key)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                result.append(
                    PlanItem(
                        PlanOperation.CONFLICT,
                        expected_value,
                        f"{labels[key]} collides with existing managed unit {unit_name}",
                        resource_id="host-identity",
                    )
                )
        return result

    @staticmethod
    def _endpoint_in_use(listeners: set[str], host: str, port: int) -> bool:
        suffix = f":{port}"
        for item in listeners:
            if not item.endswith(suffix):
                continue
            if host in {"0.0.0.0", "::"}:
                return True
            if item.startswith(host + ":") or item.startswith("[" + host + "]:"):
                return True
            if item.startswith("0.0.0.0:") or item.startswith("[::]:") or item.startswith("*:"):
                return True
        return False

    def _listener_conflicts(self, desired: DesiredInstance, bundle: GeneratedBundle) -> list[PlanItem]:
        listeners = self.observer.listeners()
        result: list[PlanItem] = []
        unit_by_id = {resource.resource_id: resource for resource in bundle.resources if resource.unit_name}
        identities = {
            record.get("unit_name", ""): record
            for record in self.observer.managed_identity_scan().records
        }
        checks = (
            ("mcp-unit", desired.mcp.host, desired.mcp.port, "MCP listener"),
            ("tunnel-unit", desired.tunnel.health_host, desired.tunnel.health_port, "tunnel health listener"),
        )
        for resource_id, host, port, label in checks:
            if not self._endpoint_in_use(listeners, host, port):
                continue
            resource = unit_by_id[resource_id]
            unit = self.observer.observe_unit(resource.unit_name or "")
            observed_resource = self.observer.observe_resource(resource)
            owned_listener = (
                unit.loaded
                and unit.active
                and observed_resource.status is ObservationStatus.EXACT
            )
            if (
                not owned_listener
                and resource_id == "mcp-unit"
                and unit.loaded
                and unit.active
                and observed_resource.status is ObservationStatus.DIFFERENT
            ):
                current = identities.get(resource.unit_name or "", {})
                owned_listener = (
                    current.get("instance_id") == desired.instance_id.value
                    and current.get("mcp_host") == host
                    and current.get("mcp_port") == str(port)
                )
            if not owned_listener:
                result.append(
                    PlanItem(
                        PlanOperation.CONFLICT,
                        f"{host}:{port}",
                        f"{label} is already occupied without verified ownership by {desired.instance_id.value}",
                        resource_id=resource_id,
                    )
                )
        return result

    def _file_operations(
        self,
        desired: DesiredInstance,
        bundle: GeneratedBundle,
    ) -> tuple[list[PlanItem], set[str]]:
        operations: list[PlanItem] = []
        changed: set[str] = set()
        present = desired.lifecycle.deployment is DeploymentTarget.PRESENT
        resources = list(bundle.resources)
        if not present:
            def removal_rank(resource: GeneratedResource) -> tuple[int, str]:
                if resource.unit_name or resource.kind is ResourceKind.TUNNEL_PROFILE:
                    rank = 10
                elif resource.resource_id == "access-ignore":
                    rank = 20
                elif resource.resource_id.startswith("access-") and resource.kind is ResourceKind.DIRECTORY:
                    rank = 30
                elif resource.resource_id == "access-root":
                    rank = 40
                elif resource.resource_id == "access-owner":
                    rank = 50
                elif resource.resource_id == "state-dir":
                    rank = 60
                elif resource.resource_id == "state-owner":
                    rank = 70
                else:
                    rank = 25
                return rank, resource.resource_id

            resources.sort(key=removal_rank)
        for resource in resources:
            if not present and not resource.remove_when_absent:
                operations.append(
                    PlanItem(
                        PlanOperation.NOOP,
                        resource.path,
                        "shared resource is retained when one instance is absent",
                        resource.resource_id,
                    )
                )
                continue
            observation = self.observer.observe_resource(resource)
            target = resource.path
            if observation.status is ObservationStatus.UNVERIFIED:
                operations.append(
                    PlanItem(
                        PlanOperation.CONFLICT,
                        target,
                        f"resource cannot be safely reconciled: {observation.detail}",
                        resource.resource_id,
                    )
                )
                continue
            if present:
                if observation.status is ObservationStatus.ABSENT:
                    operations.append(
                        PlanItem(PlanOperation.CREATE, target, "managed resource is absent", resource.resource_id)
                    )
                    changed.add(resource.resource_id)
                elif observation.status is ObservationStatus.EXACT:
                    operations.append(
                        PlanItem(PlanOperation.NOOP, target, "managed resource matches desired state", resource.resource_id)
                    )
                elif observation.status is ObservationStatus.DIFFERENT:
                    operations.append(
                        PlanItem(PlanOperation.UPDATE, target, observation.detail, resource.resource_id)
                    )
                    changed.add(resource.resource_id)
                else:
                    operations.append(
                        PlanItem(PlanOperation.CONFLICT, target, observation.detail, resource.resource_id)
                    )
            else:
                if observation.status is ObservationStatus.ABSENT:
                    operations.append(
                        PlanItem(PlanOperation.NOOP, target, "resource is already absent", resource.resource_id)
                    )
                elif observation.status in {ObservationStatus.EXACT, ObservationStatus.DIFFERENT}:
                    operations.append(
                        PlanItem(PlanOperation.REMOVE, target, "owned resource must be absent", resource.resource_id)
                    )
                    changed.add(resource.resource_id)
                else:
                    operations.append(
                        PlanItem(PlanOperation.CONFLICT, target, observation.detail, resource.resource_id)
                    )
        return operations, changed

    def _unit_operations(
        self,
        desired: DesiredInstance,
        bundle: GeneratedBundle,
        changed: set[str],
    ) -> list[PlanItem]:
        operations: list[PlanItem] = []
        present = desired.lifecycle.deployment is DeploymentTarget.PRESENT
        running = desired.lifecycle.runtime is RuntimeTarget.RUNNING
        resources = [resource for resource in bundle.resources if resource.unit_name]
        if not present:
            stop_priority = {
                "admission-guard-timer": 0,
                "tunnel-unit": 1,
                "mcp-unit": 2,
                "admission-guard-unit": 3,
            }
            resources.sort(key=lambda resource: (stop_priority.get(resource.resource_id, 10), resource.resource_id))
        for resource in resources:
            observed = self.observer.observe_unit(resource.unit_name)
            if present:
                if resource.enable_when_present and not observed.enabled:
                    operations.append(
                        PlanItem(PlanOperation.ENABLE, resource.unit_name, "unit must be enabled", resource.resource_id)
                    )
                target_active = resource.active_when_running and running
                if target_active:
                    if observed.active and resource.resource_id in changed:
                        operations.append(
                            PlanItem(PlanOperation.RESTART, resource.unit_name, "active unit resource changed", resource.resource_id)
                        )
                    elif not observed.active:
                        operations.append(
                            PlanItem(PlanOperation.START, resource.unit_name, "unit must be active", resource.resource_id)
                        )
                elif observed.active:
                    operations.append(
                        PlanItem(PlanOperation.STOP, resource.unit_name, "unit must be stopped", resource.resource_id)
                    )
            else:
                if observed.active:
                    operations.append(
                        PlanItem(PlanOperation.STOP, resource.unit_name, "deployment target is absent", resource.resource_id)
                    )
                if observed.enabled:
                    operations.append(
                        PlanItem(PlanOperation.DISABLE, resource.unit_name, "deployment target is absent", resource.resource_id)
                    )
        return operations

    def plan(self, desired: DesiredInstance) -> ReconciliationPlan:
        bundle = self.generator.generate(desired)
        operations = self._registry_conflicts(desired)
        if desired.lifecycle.deployment is DeploymentTarget.PRESENT:
            operations.extend(self._host_preconditions(desired))
            operations.extend(self._managed_host_conflicts(desired))
            operations.extend(self._listener_conflicts(desired, bundle))
            if desired.recovery.admission_guard_enabled:
                operations.append(
                    PlanItem(
                        PlanOperation.CONFLICT,
                        "recovery.admission_guard_enabled",
                        "admission recovery runtime is intentionally deferred to P11",
                        resource_id="admission-guard-runtime",
                    )
                )
        file_ops, changed = self._file_operations(desired, bundle)
        unit_ops = self._unit_operations(desired, bundle, changed)
        if desired.lifecycle.deployment is DeploymentTarget.PRESENT:
            operations.extend(file_ops)
            operations.extend(unit_ops)
        else:
            operations.extend(unit_ops)
            operations.extend(file_ops)
        warnings: list[str] = []
        valid = not any(item.operation is PlanOperation.CONFLICT for item in operations)
        return ReconciliationPlan(
            instance_id=desired.instance_id.value,
            desired_fingerprint=desired.fingerprint(),
            valid=valid,
            operations=tuple(operations),
            warnings=tuple(warnings),
        )
