from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class ErrorCode(StrEnum):
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_VERSION_UNSUPPORTED = "CONFIG_VERSION_UNSUPPORTED"
    INSTANCE_EXISTS = "INSTANCE_EXISTS"
    INSTANCE_NOT_FOUND = "INSTANCE_NOT_FOUND"
    REGISTRY_INVALID = "REGISTRY_INVALID"
    HOST_INSPECTION_FAILED = "HOST_INSPECTION_FAILED"
    RECONCILIATION_CONFLICT = "RECONCILIATION_CONFLICT"
    STALE_STATE = "STALE_STATE"
    FEATURE_NOT_IMPLEMENTED = "FEATURE_NOT_IMPLEMENTED"
    IO_ERROR = "IO_ERROR"


@dataclass(slots=True)
class ManagerError(Exception):
    code: ErrorCode
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": dict(self.details),
            },
        }


def config_error(message: str, **details: Any) -> ManagerError:
    return ManagerError(ErrorCode.CONFIG_INVALID, message, details)

