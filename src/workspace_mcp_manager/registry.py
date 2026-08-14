from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .domain import DesiredInstance, InstanceId
from .errors import ErrorCode, ManagerError


class InstanceRegistry:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, instance_id: InstanceId | str) -> Path:
        iid = instance_id if isinstance(instance_id, InstanceId) else InstanceId(instance_id)
        return self.directory / f"{iid.value}.json"

    @contextmanager
    def _instance_lock(self, instance_id: InstanceId | str):
        iid = instance_id if isinstance(instance_id, InstanceId) else InstanceId(instance_id)
        lock_root = self.directory / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.directory, 0o700)
            os.chmod(lock_root, 0o700)
        except OSError:
            pass
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

