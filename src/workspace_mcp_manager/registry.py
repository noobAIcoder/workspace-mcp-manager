from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .domain import DesiredInstance, InstanceId
from .errors import ErrorCode, ManagerError


# An in-process capability used only while the importer owns the exclusive gate.
_IMPORT_AUTHORITY = ContextVar('workspace_import_authority', default=frozenset())


@contextmanager
def import_authority(registry, instance_id):
    token = _IMPORT_AUTHORITY.set(_IMPORT_AUTHORITY.get() | {(id(registry), instance_id)})
    try:
        yield
    finally:
        _IMPORT_AUTHORITY.reset(token)


def importing(registry, instance_id):
    return (id(registry), instance_id) in _IMPORT_AUTHORITY.get()


@contextmanager
def import_coordination(state_root, instance_id, *, exclusive=False, registry=None):
    iid = InstanceId(instance_id).value
    if registry is not None and importing(registry, iid):
        yield
        return
    directory = state_root / 'import-locks'
    if state_root.is_symlink() or directory.is_symlink():
        raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, 'unsafe import coordination path')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (iid + '.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, 'instance import is in progress: ' + iid) from exc
        try:
            journal = state_root / 'imports' / (iid + '.json')
            if not exclusive and (journal.exists() or journal.is_symlink()):
                raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT, 'recover the pending import: ' + iid)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def assert_maintenance_allows(state_root: Path, *, removing: bool = False) -> None:
    journal = state_root / "maintenance-transaction.json"
    if not journal.exists() and not journal.is_symlink():
        return
    try:
        if journal.is_symlink():
            raise ValueError("symlinked maintenance journal")
        data = json.loads(journal.read_text())
        if removing and data.get("operation") == "uninstall":
            return
    except (OSError, ValueError, AttributeError):
        pass
    raise ManagerError(ErrorCode.RECONCILIATION_CONFLICT,
                       "distribution maintenance is pending; recover or finish it before changing instances")


class InstanceRegistry:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _maintenance_guard(self, desired: DesiredInstance | None = None) -> None:
        # Canonical registries identify their account without relying on ambient HOME.
        if self.directory.name == "instances" and self.directory.parent.name == "workspace-mcp-manager" and self.directory.parent.parent.name == ".config":
            home = self.directory.parent.parent.parent
            removing = desired is not None and desired.lifecycle.to_dict() == {"deployment": "absent", "runtime": "stopped"}
            assert_maintenance_allows(home / ".local/state/workspace-mcp-manager", removing=removing)

    def path_for(self, instance_id: InstanceId | str) -> Path:
        iid = instance_id if isinstance(instance_id, InstanceId) else InstanceId(instance_id)
        return self.directory / f"{iid.value}.json"

    @contextmanager
    def _instance_lock(self, instance_id: InstanceId | str):
        iid = instance_id.value if isinstance(instance_id, InstanceId) else instance_id
        canonical = self.directory.name == 'instances' and self.directory.parent.name == 'workspace-mcp-manager' and self.directory.parent.parent.name == '.config'
        if canonical:
            state = self.directory.parent.parent.parent / '.local/state/workspace-mcp-manager'
            with import_coordination(state, iid, registry=self):
                with self._registry_instance_lock(instance_id):
                    yield
        else:
            with self._registry_instance_lock(instance_id):
                yield

    @contextmanager
    def _registry_instance_lock(self, instance_id: InstanceId | str):
        iid = instance_id if isinstance(instance_id, InstanceId) else InstanceId(instance_id)
        lock_root = self.directory / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.directory, 0o700)
            os.chmod(lock_root, 0o700)
        except OSError:
            pass
        # Distribution maintenance can briefly exclude all registry writes,
        # including creation of instance IDs not present in its preview.
        with (lock_root / ".registry.lock").open("a+", encoding="utf-8") as gate:
            os.chmod(lock_root / ".registry.lock", 0o600)
            fcntl.flock(gate.fileno(), fcntl.LOCK_SH)
            path = lock_root / f"{iid.value}.lock"
            with path.open("a+", encoding="utf-8") as handle:
                os.chmod(path, 0o600)
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self, path: Path) -> DesiredInstance:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except FileNotFoundError as exc:
            raise ManagerError(ErrorCode.INSTANCE_NOT_FOUND, f"instance declaration not found: {path.stem}") from exc
        except (OSError, UnicodeError) as exc:
            raise ManagerError(ErrorCode.IO_ERROR, f"cannot read instance declaration: {path}") from exc
        desired = DesiredInstance.from_json(text)
        if desired.instance_id.value != path.stem:
            raise ManagerError(
                ErrorCode.REGISTRY_INVALID,
                "registry filename does not match instance_id",
                {"path": str(path), "instance_id": desired.instance_id.value},
            )
        return desired

    def get(self, instance_id: str) -> DesiredInstance:
        return self._read(self.path_for(instance_id))

    def list(self) -> list[DesiredInstance]:
        if not self.directory.exists():
            return []
        try:
            paths = sorted(self.directory.glob("*.json"))
        except OSError as exc:
            raise ManagerError(ErrorCode.IO_ERROR, f"cannot list registry: {self.directory}") from exc
        return [self._read(path) for path in paths]

    def create(self, desired: DesiredInstance) -> Path:
        path = self.path_for(desired.instance_id)
        with self._instance_lock(desired.instance_id):
            self._maintenance_guard()
            if path.exists():
                raise ManagerError(ErrorCode.INSTANCE_EXISTS, f"instance already exists: {desired.instance_id.value}")
            self._write(path, desired)
        return path

    def update(
        self,
        desired: DesiredInstance,
        *,
        expected_current_fingerprint: str | None = None,
    ) -> Path:
        path = self.path_for(desired.instance_id)
        with self._instance_lock(desired.instance_id):
            self._maintenance_guard(desired)
            if not path.is_file():
                raise ManagerError(ErrorCode.INSTANCE_NOT_FOUND, f"instance declaration not found: {desired.instance_id.value}")
            current = self._read(path)
            current_fingerprint = current.fingerprint()
            if (
                expected_current_fingerprint is not None
                and current_fingerprint != expected_current_fingerprint
            ):
                raise ManagerError(
                    ErrorCode.STALE_STATE,
                    f"stale declaration for {desired.instance_id.value}",
                    {
                        "instance_id": desired.instance_id.value,
                        "expected_current_fingerprint": expected_current_fingerprint,
                        "current_fingerprint": current_fingerprint,
                    },
                )
            if desired.config_version < current.config_version:
                raise ManagerError(
                    ErrorCode.CONFIG_VERSION_UNSUPPORTED,
                    "instance config_version downgrade is not supported",
                    {"current": current.config_version, "requested": desired.config_version},
                )
            self._write(path, desired)
        return path

    def _write(self, path: Path, desired: DesiredInstance) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        payload = json.dumps(desired.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                errors="strict",
                prefix=f".{path.stem}.",
                suffix=".tmp",
                dir=self.directory,
                delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
        except OSError as exc:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise ManagerError(ErrorCode.IO_ERROR, f"cannot write instance declaration: {path}") from exc

